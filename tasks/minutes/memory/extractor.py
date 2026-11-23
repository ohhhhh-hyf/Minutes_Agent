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

_INVALID_OWNER_WORDS = {"null", "none", "", "无", "全员", "团队", "大家", "所有人", "无责任人", "全体", "tbd"}
_INVALID_DEADLINE_WORDS = {"null", "none", "", "无", "待定", "tbd"}


def _sanitize_extracted_data(result: dict[str, Any]) -> dict[str, list[dict]]:
    """防御性解析与字段校验，支持顶级三抽屉 (decisions/actions/close) 并向下兼容旧版 open."""
    clean_actions: list[dict] = []
    clean_decisions: list[dict] = []
    clean_close: list[dict] = []

    # 1. 优先判定是否采用顶级物理隔离 Schema
    has_isolated_schema = ("decisions" in result) or ("actions" in result)

    if has_isolated_schema:
        # A. 提取 decisions 独立抽屉
        raw_decisions = result.get("decisions", [])
        if isinstance(raw_decisions, list):
            for item in raw_decisions:
                if isinstance(item, dict):
                    content = str(item.get("content") or "").strip()
                elif isinstance(item, str):
                    content = item.strip()
                else:
                    continue

                if content and content not in [d["content"] for d in clean_decisions]:
                    clean_decisions.append({
                        "category": "decision",
                        "content": content,
                        "owner": None,
                        "deadline": None,
                    })

        # B. 提取 actions 独立抽屉
        raw_actions = result.get("actions", [])
        if isinstance(raw_actions, list):
            for item in raw_actions:
                if not isinstance(item, dict):
                    continue
                content = str(item.get("content") or "").strip()
                if not content:
                    continue

                raw_owner = str(item.get("owner") or "").strip()
                owner = None if raw_owner.lower() in _INVALID_OWNER_WORDS else raw_owner

                raw_deadline = str(item.get("deadline") or "").strip()
                deadline = None if raw_deadline.lower() in _INVALID_DEADLINE_WORDS else raw_deadline

                clean_actions.append({
                    "category": "action",
                    "content": content,
                    "owner": owner,
                    "deadline": deadline,
                })
    else:
        # C. 向下兼容旧版 open 数组模式（双重防御）
        raw_open = result.get("open", [])
        if isinstance(raw_open, list):
            for item in raw_open:
                if not isinstance(item, dict):
                    continue
                content = str(item.get("content") or "").strip()
                if not content:
                    continue

                raw_cat = str(item.get("category") or "").strip().lower()
                raw_owner = str(item.get("owner") or "").strip()
                owner = None if raw_owner.lower() in _INVALID_OWNER_WORDS else raw_owner

                raw_deadline = str(item.get("deadline") or "").strip()
                deadline = None if raw_deadline.lower() in _INVALID_DEADLINE_WORDS else raw_deadline

                # 智能判定 category
                if raw_cat in ("decision", "决议", "决策", "共识") or not owner:
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

    # 2. 提取 close 办结事项抽屉
    raw_close = result.get("close", [])
    if isinstance(raw_close, list):
        for item in raw_close:
            if isinstance(item, dict):
                c_text = str(item.get("content") or "").strip()
            elif isinstance(item, str):
                c_text = item.strip()
            else:
                continue

            if c_text and c_text not in [c["content"] for c in clean_close]:
                clean_close.append({"content": c_text})

    # 3. 配额防御：action 最多 15 条，decision 最多 10 条
    capped_actions = clean_actions[:15]
    capped_decisions = clean_decisions[:10]
    normalized_open = capped_actions + capped_decisions

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
