"""
tasks/minutes/memory/reconciler.py
慢轨后台对账逻辑：结项闭环 + 查重更新 Upsert + 15条硬限熔断
使用单批次 Batch 批量向量化，消除循环内的重复网络请求
"""
from __future__ import annotations

import logging
import numpy as np
from .extractor import run_memory_extractor_agent
from .render import _get_embeddings

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
    2. 【批处理提速】单次 Batch 预先向量化全部去重文本，建立内存字典，消除循环内重复网络调用
    3. close 项与历史 open 项对账结项 (>= 0.75)
    4. 新 open 项与现存 open 项查重 (>= 0.80 原地 update 刷新时限，避免重复堆叠)
    5. 活跃 open 总量硬限熔断 (超出 15 条自动休眠最老事项)
    """
    try:
        # 1. 异步调用提取 Agent 提炼结构化事实
        extracted = await run_memory_extractor_agent(minutes_md, active_open_items)
        close_items = extracted.get("close", [])
        open_items = extracted.get("open", [])

        # 2. 收集本次对账涉及到的所有文本，做单批次预先 Batch 向量化
        texts_to_embed: list[str] = []
        for c in close_items:
            t = c.get("content", "").strip()
            if t and t not in texts_to_embed:
                texts_to_embed.append(t)

        for o in open_items[:15]:
            t = o.get("content", "").strip()
            if t and t not in texts_to_embed:
                texts_to_embed.append(t)

        for old in active_open_items:
            t = old.get("memory", "").strip()
            if t and t not in texts_to_embed:
                texts_to_embed.append(t)

        # 单次 Batch 网络请求 (~0.15s) 发送给 Embedding 接口并建立内存映射
        vector_map: dict[str, list[float]] = {}
        if texts_to_embed:
            vectors = _get_embeddings(mem.embedding_model, texts_to_embed)
            vector_map = {text: vec for text, vec in zip(texts_to_embed, vectors)}

        closed_ids = set()

        # 3. 阶段一：结项闭环流转 (close 项与历史 open 项计算相似度 >= 0.75 翻转为 close)
        # 全在内存做余弦相似度计算，0 网络 IO
        if close_items and active_open_items:
            for c in close_items:
                c_text = c.get("content", "").strip()
                if not c_text:
                    continue
                c_vector = vector_map.get(c_text)
                if c_vector is None:
                    continue

                best_id, highest_score = None, 0.0
                for old in active_open_items:
                    old_id = old["id"]
                    if old_id in closed_ids:
                        continue
                    old_text = old.get("memory", "").strip()
                    old_vector = vector_map.get(old_text)
                    if old_vector is None:
                        continue

                    score = cosine_similarity(c_vector, old_vector)
                    if score > highest_score:
                        highest_score = score
                        best_id = old_id

                if best_id and highest_score >= 0.75:
                    logger.info(f"[Reconciler] 事项闭环结项: {best_id} (相似度 {highest_score:.2f})")
                    if hasattr(mem, "vector_store") and hasattr(mem.vector_store, "update"):
                        mem.vector_store.update(
                            vector_id=best_id,
                            payload={
                                "status": "close",
                                "closed_at": meeting_time,
                                "series": series
                            }
                        )
                    else:
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

        # 4. 阶段二：查重更新与防膨胀 (新 open 项与 remaining_open 内存语义查重)
        for o in open_items[:15]:
            content = o.get("content", "").strip()
            if not content:
                continue

            o_vector = vector_map.get(content)
            if o_vector is None:
                continue

            matched_existing_id = None
            max_sim = 0.0

            for cur in remaining_open:
                cur_text = cur.get("memory", "").strip()
                cur_vector = vector_map.get(cur_text)
                if cur_vector is None:
                    continue
                sim = cosine_similarity(o_vector, cur_vector)
                if sim > max_sim:
                    max_sim = sim
                    matched_existing_id = cur["id"]

            new_meta = {
                "user_id": user_id,
                "series": series,
                "meeting_time": meeting_time,
                "status": "open",
                "owner": o.get("owner"),
                "deadline": o.get("deadline"),
                "role": "user"
            }

            if matched_existing_id and max_sim >= 0.80:
                # 命中已有任务：老任务延续推进 / 刷新截止时间 -> 【原地 UPDATE，复用 vector_map，0 网络调用】
                logger.info(f"[Reconciler] 老任务延续刷新 (相似度 {max_sim:.2f}): {matched_existing_id}")
                if hasattr(mem, "_update_memory"):
                    mem._update_memory(
                        memory_id=matched_existing_id,
                        data=content,
                        existing_embeddings=vector_map,
                        metadata=new_meta
                    )
                else:
                    mem.update(
                        memory_id=matched_existing_id,
                        text=content,
                        metadata=new_meta
                    )
            else:
                # 全新事项 -> 真正执行立项写入（复用 vector_map 向量直接入库，0 网络调用）
                logger.info(f"[Reconciler] 全新事项立项写入: {content[:20]}...")
                if hasattr(mem, "_create_memory") and content in vector_map:
                    mem._create_memory(
                        data=content,
                        existing_embeddings=vector_map,
                        metadata=new_meta
                    )
                else:
                    mem.add(
                        messages=content,
                        user_id=user_id,
                        infer=False,
                        metadata=new_meta
                    )

        # 5. 阶段三：容量熔断守卫 (Zombie Hard Cap = 15)
        current_active = mem.get_all(
            filters={"user_id": user_id, "series": series, "status": "open"},
            top_k=30
        ).get("results", [])

        if len(current_active) > 15:
            overflow_count = len(current_active) - 15
            for stale_item in current_active[15:]:
                stale_id = stale_item["id"]
                # 直接修改本地 Qdrant 属性，彻底消除无意义的重新 embedding 刷屏！
                if hasattr(mem, "vector_store") and hasattr(mem.vector_store, "update"):
                    mem.vector_store.update(
                        vector_id=stale_id,
                        payload={"status": "dormant", "series": series}
                    )
                else:
                    mem.update(
                        memory_id=stale_id,
                        metadata={"status": "dormant", "series": series}
                    )
            logger.info(f"[Reconciler] 触发容量上限熔断，已休眠 {overflow_count} 条陈旧待办")

    except Exception as e:
        logger.error(f"[background_memory_update] 后台更新失败: {e}", exc_info=True)
