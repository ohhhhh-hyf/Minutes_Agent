"""
tasks/minutes/memory/reconciler.py
慢轨后台对账逻辑：结项闭环 + 查重更新 Upsert + 15条硬限熔断
"""
from __future__ import annotations

import logging
import numpy as np
from .extractor import run_memory_extractor_agent

logger = logging.getLogger("MemoryReconciler")


def cosine_similarity(v1: list[float], v2: list[float]) -> float:
    """计算两个向量的余弦相似度"""
    a, b = np.array(v1), np.array(v2)
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


async def run_background_memory_update(
    minutes_md: str,
    active_open_items: list[dict],
    series: str,
    user_id: str,
    meeting_time: str,
    mem
):
    """
    后台慢轨主函数：
    1. 提炼 open/close
    2. close 项与历史 open 项对账结项 (>= 0.75)
    3. 新 open 项与现存 open 项查重 (>= 0.80 原地 update 刷新时限，避免重复堆叠)
    4. 活跃 open 总量硬限熔断 (超出 15 条自动休眠最老事项)
    """
    try:
        # 1. 异步调用提取 Agent
        extracted = await run_memory_extractor_agent(minutes_md, active_open_items)
        close_items = extracted.get("close", [])
        open_items = extracted.get("open", [])

        closed_ids = set()

        # 2. 阶段一：结项闭环流转 (close 项与历史 open 项计算相似度 >= 0.75 翻转为 close)
        if close_items and active_open_items:
            for c in close_items:
                c_text = c.get("content", "").strip()
                if not c_text:
                    continue
                
                c_vector = mem.embedding_model.embed(c_text)
                best_id, highest_score = None, 0.0

                for old in active_open_items:
                    old_id = old["id"]
                    if old_id in closed_ids:
                        continue
                    old_text = old.get("memory", "")
                    old_vector = mem.embedding_model.embed(old_text)
                    
                    score = cosine_similarity(c_vector, old_vector)
                    if score > highest_score:
                        highest_score = score
                        best_id = old_id

                if best_id and highest_score >= 0.75:
                    logger.info(f"[Reconciler] 事项闭环结项: {best_id} (相似度 {highest_score:.2f})")
                    mem.update(
                        memory_id=best_id,
                        metadata={
                            "status": "close",
                            "closed_at": meeting_time,
                            "series": series
                        }
                    )
                    closed_ids.add(best_id)

        # 现存仍处于 open 的事项清单（剔除刚被 close 掉的）
        remaining_open = [item for item in active_open_items if item["id"] not in closed_ids]

        # 3. 阶段二：查重更新与防膨胀 (新 open 项与 remaining_open 语义查重)
        for o in open_items[:15]:
            content = o.get("content", "").strip()
            if not content:
                continue

            o_vector = mem.embedding_model.embed(content)
            matched_existing_id = None
            max_sim = 0.0

            for cur in remaining_open:
                cur_vector = mem.embedding_model.embed(cur.get("memory", ""))
                sim = cosine_similarity(o_vector, cur_vector)
                if sim > max_sim:
                    max_sim = sim
                    matched_existing_id = cur["id"]

            if matched_existing_id and max_sim >= 0.80:
                # 命中已有任务：老任务延续推进 / 刷新截止时间 -> 【原地 UPDATE，严禁 ADD】
                logger.info(f"[Reconciler] 老任务延续刷新 (相似度 {max_sim:.2f}): {matched_existing_id}")
                mem.update(
                    memory_id=matched_existing_id,
                    text=content,  # 刷新为最新表述
                    metadata={
                        "series": series,
                        "meeting_time": meeting_time,
                        "status": "open",
                        "owner": o.get("owner"),
                        "deadline": o.get("deadline")
                    }
                )
            else:
                # 全新事项 -> 真正执行 ADD
                logger.info(f"[Reconciler] 全新事项立项写入: {content[:20]}...")
                mem.add(
                    messages=content,
                    user_id=user_id,
                    metadata={
                        "series": series,
                        "meeting_time": meeting_time,
                        "status": "open",
                        "owner": o.get("owner"),
                        "deadline": o.get("deadline")
                    }
                )

        # 4. 阶段三：容量熔断守卫 (Zombie Hard Cap = 15)
        current_active = mem.get_all(
            filters={"user_id": user_id, "series": series, "status": "open"},
            top_k=30
        ).get("results", [])

        if len(current_active) > 15:
            overflow_count = len(current_active) - 15
            for stale_item in current_active[15:]:
                mem.update(
                    memory_id=stale_item["id"],
                    metadata={"status": "dormant", "series": series}
                )
            logger.info(f"[Reconciler] 触发容量上限熔断，已休眠 {overflow_count} 条陈旧待办")

    except Exception as e:
        logger.error(f"[background_memory_update] 后台更新失败: {e}", exc_info=True)
