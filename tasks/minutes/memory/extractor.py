"""
tasks/minutes/memory/extractor.py
后台慢轨：异步调用大模型提炼结构化事实 open (action/decision) 与 close，并执行防御性结构校验
"""
from __future__ import annotations

import logging
from typing import Any
from infra.llm.client import LLMClient
from .prompts import MEMORY_EXTRACTOR_SYSTEM_PROMPT, build_memory_extractor_user_prompt

logger = logging.getLogger("MemoryExtractor")


def _sanitize_extracted_data(result: dict[str, Any]) -> dict[str, list[dict]]:
    """防御性解析与字段校验，规整 open (action/decision) 与 close"""
    raw_open = result.get("open", []) if isinstance(result.get("open"), list) else []
    raw_close = result.get("close", []) if isinstance(result.get("close"), list) else []

    clean_actions: list[dict] = []
    clean_decisions: list[dict] = []

    for item in raw_open:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue

        raw_cat = str(item.get("category") or "").strip().lower()
        
        # 规整 owner 与 deadline（过滤字面量 "null" / "none"）
        raw_owner = item.get("owner")
        owner = str(raw_owner).strip() if raw_owner and str(raw_owner).strip().lower() not in ("null", "none", "") else None

        raw_deadline = item.get("deadline")
        deadline = str(raw_deadline).strip() if raw_deadline and str(raw_deadline).strip().lower() not in ("null", "none", "") else None

        # 智能判定 category
        if raw_cat in ("action", "decision"):
            category = raw_cat
        elif owner:
            category = "action"
        elif "定案" in content or content.startswith("针对"):
            category = "decision"
        else:
            category = "action"

        if category == "decision":
            clean_decisions.append({
                "category": "decision",
                "content": content,
                "owner": None,
                "deadline": None,
            })
        else:
            clean_actions.append({
                "category": "action",
                "content": content,
                "owner": owner,
                "deadline": deadline,
            })

    # 配额防御：action 最多 15 条，decision 最多 10 条
    capped_actions = clean_actions[:15]
    capped_decisions = clean_decisions[:10]
    normalized_open = capped_actions + capped_decisions

    clean_close: list[dict] = []
    for item in raw_close:
        if not isinstance(item, dict):
            continue
        c_text = str(item.get("content") or "").strip()
        if c_text:
            clean_close.append({"content": c_text})

    logger.info(
        f"[MemoryExtractor] 成功提炼结构化事实: "
        f"action={len(capped_actions)} 条, decision={len(capped_decisions)} 条, close={len(clean_close)} 条"
    )

    return {
        "open": normalized_open,
        "close": clean_close,
    }


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
            return _sanitize_extracted_data(result)
        logger.warning(f"[MemoryExtractor] 模型返回非字典格式: {type(result)}")
        return {"open": [], "close": []}
    except Exception as e:
        logger.error(f"[MemoryExtractor] 提取事项失败: {e}", exc_info=True)
        return {"open": [], "close": []}
