"""Visual Minutes 与 demo.infra.llm 的极简适配层。

温度严格使用 config.DEFAULT_VISUAL_TEMPERATURE (0.2)。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from infra.llm.client import LLMClient
from .config import DEFAULT_VISUAL_TEMPERATURE, VISUAL_LLM_LABEL

logger = logging.getLogger(__name__)


def extract_json_object(text: str) -> dict[str, Any]:
    """从模型输出提取 JSON 对象，兼容代码块与前后噪声。"""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(raw[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("模型输出必须是 JSON 对象")
    return value


class VisualLLM:
    """把 demo.infra.llm.LLMClient 适配成 Visual Minutes 的强类型 JSON 调用。"""

    def __init__(self, client: LLMClient | None = None) -> None:
        self.client = client or LLMClient()
        self.temperature = DEFAULT_VISUAL_TEMPERATURE

    async def json_call(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        label: str = VISUAL_LLM_LABEL,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        temp = self.temperature if temperature is None else float(temperature)
        try:
            # 优先使用 LLMClient 内置的高效 chat_json
            payload = await self.client.chat_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=temp,
                max_tokens=max_tokens,
            )
            if isinstance(payload, dict):
                return payload
            if isinstance(payload, str):
                return extract_json_object(payload)
            raise ValueError(f"预期返回 JSON 对象字典，实际为: {type(payload)}")
        except Exception as exc:
            # 兼容底层 text 容灾降级
            logger.debug("chat_json 解析异常，尝试通过 text() 容灾: %s", exc)
            content = await self.client.text(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=temp,
                max_tokens=max_tokens,
            )
            return extract_json_object(content)


__all__ = ["VisualLLM", "extract_json_object"]
