"""
tasks/minutes/memory/reconciler.py
慢轨后台对账逻辑：旧决议清理 + 结项彻底物理删除 + 查重更新 Upsert + 30条时间戳排序熔断物理清理
全程使用单批次 Batch 批量向量化，消除循环内的重复网络请求
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


def _delete_memory_item(mem, item_id: str):
    """物理删除指定的记忆点位 (无任何网络 embedding 开销)"""
    try:
        mem.delete(item_id)
    except Exception:
        if hasattr(mem, "vector_store") and hasattr(mem.vector_store, "delete"):
            mem.vector_store.delete(vector_id=item_id)


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
    1. 阶段零：前置物理清理属于上一场会议的旧决议 (单场接力棒用完即焚)
    2. 提炼 open (action/decision) 与 close
    3. 【批处理提速】单次 Batch 预先向量化全部去重文本，建立内存字典，消除循环内重复网络调用
    4. 阶段一：close 项与历史 open action 对账结项 (>= 0.75)，直接物理删除已办结待办
    5. 阶段二：新 decision 直接写入；新 action 与现存 open action 查重 (>= 0.80 原地 update 刷新时限，避免重复堆叠)
    6. 阶段三：活跃 open action 总量硬限熔断 (超过 30 条显式按时间戳降序排序，物理删除最老溢出项)
    """
    try:
        # 0. 阶段零：前置物理清理属于上一场会议的旧决议 (单场接力棒用完即焚)
        old_decisions = [
            item for item in active_open_items
            if item.get("metadata", {}).get("category") == "decision"
        ]
        for d in old_decisions:
            _delete_memory_item(mem, d["id"])
        if old_decisions:
            logger.info(f"[Reconciler] 已物理清除上一场历史决议 {len(old_decisions)} 条")

        # 仅保留正在推进中的行动待办 (action)
        active_actions = [
            item for item in active_open_items
            if item.get("metadata", {}).get("category") != "decision"
        ]

        # 1. 异步调用提取 Agent 提炼结构化事实
        extracted = await run_memory_extractor_agent(minutes_md, active_actions)
        close_items = extracted.get("close", [])
        open_items = extracted.get("open", [])

        # 2. 收集本次对账涉及到的所有文本，做单批次预先 Batch 向量化
        texts_to_embed: list[str] = []
        for c in close_items:
            t = c.get("content", "").strip()
            if t and t not in texts_to_embed:
                texts_to_embed.append(t)

        for o in open_items:
            t = o.get("content", "").strip()
            if t and t not in texts_to_embed:
                texts_to_embed.append(t)

        for old in active_actions:
            t = old.get("memory", "").strip()
            if t and t not in texts_to_embed:
                texts_to_embed.append(t)

        # 单次 Batch 网络请求 (~0.15s) 发送给 Embedding 接口并建立内存映射
        vector_map: dict[str, list[float]] = {}
        if texts_to_embed:
            vectors = _get_embeddings(mem.embedding_model, texts_to_embed)
            vector_map = {text: vec for text, vec in zip(texts_to_embed, vectors)}

        closed_ids = set()

        # 3. 阶段一：结项闭环流转 (close 项与历史 open action 计算相似度 >= 0.75 直接物理删除)
        # 全在内存做余弦相似度计算，0 网络 IO
        if close_items and active_actions:
            for c in close_items:
                c_text = c.get("content", "").strip()
                if not c_text:
                    continue
                c_vector = vector_map.get(c_text)
                if c_vector is None:
                    continue

                best_id, highest_score = None, 0.0
                for old in active_actions:
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
                    logger.info(f"[Reconciler] 事项办结物理删除: {best_id} (相似度 {highest_score:.2f})")
                    _delete_memory_item(mem, best_id)
                    closed_ids.add(best_id)

        # 现存仍处于 open 的待办清单（剔除刚被办结物理删除的）
        remaining_open_actions = [item for item in active_actions if item["id"] not in closed_ids]

        # 4. 阶段二：查重更新与新事项入库 (新 open 项与 remaining_open_actions 内存语义查重)
        for o in open_items:
            content = o.get("content", "").strip()
            if not content:
                continue

            o_vector = vector_map.get(content)
            if o_vector is None:
                continue

            cat = o.get("category", "action")
            new_meta = {
                "user_id": user_id,
                "series": series,
                "meeting_time": meeting_time,
                "status": "open",
                "category": cat,
                "owner": o.get("owner"),
                "deadline": o.get("deadline"),
                "role": "user"
            }

            if cat == "decision":
                # 定案决议直接立项写入作为本场最新接力棒（无需查重，0 HTTP 调用）
                logger.info(f"[Reconciler] 最新定案决议立项写入: {content[:25]}...")
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
            else:
                # 行动待办与存量待办做内存语义查重
                matched_existing_id = None
                max_sim = 0.0

                for cur in remaining_open_actions:
                    cur_text = cur.get("memory", "").strip()
                    cur_vector = vector_map.get(cur_text)
                    if cur_vector is None:
                        continue
                    sim = cosine_similarity(o_vector, cur_vector)
                    if sim > max_sim:
                        max_sim = sim
                        matched_existing_id = cur["id"]

                if matched_existing_id and max_sim >= 0.80:
                    # 命中已有任务：老任务延续推进 / 刷新截止时间 -> 【原地 UPDATE，复用 vector_map，0 网络调用】
                    logger.info(f"[Reconciler] 老待办延续刷新 (相似度 {max_sim:.2f}): {matched_existing_id}")
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
                    # 全新待办 -> 真正执行立项写入（复用 vector_map 向量直接入库，0 网络调用）
                    logger.info(f"[Reconciler] 全新待办立项写入: {content[:25]}...")
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

        # 5. 阶段三：容量熔断守卫 (Zombie Hard Cap = 30 条，显式按时间戳降序排序)
        current_active = mem.get_all(
            filters={"user_id": user_id, "series": series, "status": "open"},
            top_k=60
        ).get("results", [])

        # 仅针对 action 待办项进行数量盘点与熔断淘汰
        active_actions_in_db = [
            item for item in current_active
            if item.get("metadata", {}).get("category") != "decision"
        ]

        # 1. 显式按“最后更新时间”从新到老降序排列（最新活跃的排在最前面）
        active_actions_in_db.sort(
            key=lambda x: x.get("updated_at") or x.get("created_at") or x.get("metadata", {}).get("meeting_time", ""),
            reverse=True
        )

        # 2. 前 30 条是【最新提出来的】或者【最近被讨论刷新的】，受到严格保护！
        # 3. 超过 30 条的尾部数据（active_actions_in_db[30:]），才是真正长期无人问津的“最老僵尸项”
        if len(active_actions_in_db) > 30:
            overflow_stale_items = active_actions_in_db[30:]
            for stale_item in overflow_stale_items:
                _delete_memory_item(mem, stale_item["id"])
            logger.info(f"[Reconciler] 触发容量上限熔断，已物理删除 {len(overflow_stale_items)} 条最陈旧待办")

    except Exception as e:
        logger.error(f"[background_memory_update] 后台更新失败: {e}", exc_info=True)
