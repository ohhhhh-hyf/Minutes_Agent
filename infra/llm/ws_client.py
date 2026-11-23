"""SFT WebSocket 流式传输客户端.

协议对齐 nlu_llm_ws_client.py：
- 一次调用 = 一条 WebSocket 连接；
- header `sender` + body `api_key` 鉴权；
- 服务端逐帧返回 OpenAI 风格的 `choices[0].delta.content`；
- `usage` / `metadata` 可能在个别帧上携带，取最后一个累计值。
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

logger = logging.getLogger("infra.llm.ws")


def _build_body(
    *,
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    temperature: float,
    max_tokens: int | None,
    top_p: float,
    top_k: int,
    enable_thinking: bool,
    skip_special_tokens: bool,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "api_key": api_key,
        "model": model,
        "stream": True,
        "extra_body": {
            "enable_thinking": bool(enable_thinking),
            "skip_special_tokens": bool(skip_special_tokens),
        },
        "stream_options": {"include_usage": True, "debug_usage": True},
        "temperature": float(temperature),
        "top_p": float(top_p),
        "top_k": int(top_k),
        "messages": messages,
    }
    if max_tokens is not None:
        body["max_tokens"] = int(max_tokens)
    return body


async def astream_sft(
    *,
    url: str,
    sender: str,
    model: str,
    api_key: str = "AccessService",
    messages: list[dict[str, Any]],
    temperature: float = 0.2,
    max_tokens: int | None = None,
    top_p: float = 1.0,
    top_k: int = 64,
    enable_thinking: bool = False,
    skip_special_tokens: bool = False,
    recv_timeout: float = 300.0,
    connect_timeout: float = 30.0,
    usage_out: dict[str, Any] | None = None,
) -> AsyncIterator[str]:
    """连接 SFT WebSocket，逐帧产出 content 增量。

    Args:
        usage_out: 可选的可变字典；服务端返回 usage 时原地更新为最新累计值。
    """
    if not sender:
        raise ValueError("SFT WebSocket 后端缺少 LLM_SFT_SENDER 配置")

    try:
        import websockets
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("SFT WebSocket 后端需要安装 websockets 包") from exc

    body = _build_body(
        api_key=api_key,
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        top_p=top_p,
        top_k=top_k,
        enable_thinking=enable_thinking,
        skip_special_tokens=skip_special_tokens,
    )

    try:
        async with websockets.connect(
            url,
            additional_headers={"sender": sender},
            open_timeout=connect_timeout,
        ) as websocket:
            await websocket.send(json.dumps(body, ensure_ascii=False))

            while True:
                try:
                    raw = await asyncio.wait_for(websocket.recv(), timeout=recv_timeout)
                except asyncio.TimeoutError as exc:
                    raise RuntimeError(
                        f"SFT WebSocket 等待响应超时 ({recv_timeout}s)"
                    ) from exc
                except websockets.exceptions.ConnectionClosedOK:
                    break

                if not raw:
                    continue
                if raw == "[DONE]":
                    break

                chunk = json.loads(raw)

                if chunk.get("usage") and usage_out is not None:
                    usage_out.clear()
                    usage_out.update(chunk["usage"])

                error = chunk.get("error")
                if error:
                    if isinstance(error, dict):
                        desc = error.get("desc") or error.get("message") or error
                    else:
                        desc = str(error)
                    raise RuntimeError(f"SFT WebSocket 调用失败: {desc}")

                finish_reason = None
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    content = delta.get("content")
                    if content:
                        yield content
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]

                if finish_reason:
                    break
    except websockets.exceptions.ConnectionClosedError as exc:
        raise RuntimeError(f"SFT WebSocket 连接异常关闭: {exc}") from exc


async def acomplete_sft(
    *,
    url: str,
    sender: str,
    model: str,
    api_key: str = "AccessService",
    messages: list[dict[str, Any]],
    temperature: float = 0.2,
    max_tokens: int | None = None,
    top_p: float = 1.0,
    top_k: int = 64,
    enable_thinking: bool = False,
    skip_special_tokens: bool = False,
    recv_timeout: float = 300.0,
    connect_timeout: float = 30.0,
) -> tuple[str, dict[str, Any]]:
    """消费完整 WS 流，返回 (完整正文, usage)。"""
    usage: dict[str, Any] = {}
    parts: list[str] = []
    async for delta in astream_sft(
        url=url,
        sender=sender,
        api_key=api_key,
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        top_p=top_p,
        top_k=top_k,
        enable_thinking=enable_thinking,
        skip_special_tokens=skip_special_tokens,
        recv_timeout=recv_timeout,
        connect_timeout=connect_timeout,
        usage_out=usage,
    ):
        parts.append(delta)
    return "".join(parts), usage


__all__ = ["acomplete_sft", "astream_sft"]
