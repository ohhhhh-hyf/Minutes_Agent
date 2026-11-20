"""
tasks/minutes/memory/extractor.py
后台慢轨：异步调用大模型提炼结构化事实 open 与 close
"""
from __future__ import annotations

import logging
from typing import Any
from infra.llm.client import LLMClient
from .prompts import MEMORY_EXTRACTOR_SYSTEM_PROMPT, build_memory_extractor_user_prompt

logger = logging.getLogger("MemoryExtractor")


async def run_memory_extractor_agent(
    minutes_md: str,
    active_open_items: list[dict],
    llm_client: LLMClient | None = None
) -> dict[str, list[dict]]:
    """
    异步调用大模型提取事项，原生使用 infra.llm.client.LLMClient（继承重试与超时逻辑）
    返回字典结构: {"open": [...], "close": [...]}
    """
    client = llm_client or LLMClient()
    user_prompt = build_memory_extractor_user_prompt(minutes_md, active_open_items)
    
    try:
        result = await client.chat_json(
            system_prompt=MEMORY_EXTRACTOR_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.1
        )
        if isinstance(result, dict):
            return {
                "open": result.get("open", []) if isinstance(result.get("open"), list) else [],
                "close": result.get("close", []) if isinstance(result.get("close"), list) else []
            }
        logger.warning(f"[MemoryExtractor] 模型返回非字典格式: {type(result)}")
        return {"open": [], "close": []}
    except Exception as e:
        logger.error(f"[MemoryExtractor] 提取事项失败: {e}", exc_info=True)
        return {"open": [], "close": []}
