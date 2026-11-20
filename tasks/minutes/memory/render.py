"""
tasks/minutes/memory/render.py
快轨拼装：分句级 BGE-M3 语义打标 (~100ms) 为主 + 规则匹配兜底 -> 追加 # 历史记忆关联
"""
from __future__ import annotations

import logging
import re
import numpy as np

logger = logging.getLogger("MemoryRender")


def cosine_similarity_matrix(candidate_vecs: list[list[float]], memory_vecs: list[list[float]]) -> np.ndarray:
    """批量计算余弦相似度矩阵: shape = (len(candidates), len(memories))"""
    A = np.array(candidate_vecs)
    B = np.array(memory_vecs)
    norm_A = np.linalg.norm(A, axis=1, keepdims=True)
    norm_B = np.linalg.norm(B, axis=1, keepdims=True)
    return np.dot(A / (norm_A + 1e-9), (B / (norm_B + 1e-9)).T)


def _split_into_sentences(line: str) -> list[str]:
    """将正文行按中文断句标点拆分为独立语意句（通过正向后行断言保留句末标点）"""
    parts = re.split(r"(?<=[。；！？])", line)
    return [p for p in parts if p.strip()]


def _apply_hits_to_lines(lines: list[str], valid_hits: list[dict]) -> tuple[list[str], list[dict]]:
    """
    核心规范：
    1. 按正文出现的物理先后顺序 (line_idx, sent_idx) 升序排序
    2. 按文章从上到下的顺序重新分配连续递增序号 1, 2, 3...
    3. 在对应句末插入角标 [1](#ref-1), [2](#ref-2)...
    4. 仅返回真正命中的有序列表，用于渲染文末 # 历史记忆关联
    """
    if not valid_hits:
        return lines, []

    # 1. 严格按正文出现的物理行号与句号升序排序 (从上到下)
    valid_hits.sort(key=lambda x: (x["line_idx"], x["sent_idx"]))

    # 2. 为每个命中项分配严格自增的引用编号 (1, 2, 3...)
    for ref_id, hit in enumerate(valid_hits, start=1):
        hit["ref_id"] = ref_id

    # 3. 按 (line_idx, sentence) 分组，支持同一句子命中多个历史待办时的平滑合并插标
    from collections import defaultdict
    sentence_groups: dict[tuple[int, str], list[int]] = defaultdict(list)
    for hit in valid_hits:
        key = (hit["line_idx"], hit["sentence"])
        sentence_groups[key].append(hit["ref_id"])

    # 4. 执行句末标点后精准插标
    for (line_idx, sentence), ref_ids in sentence_groups.items():
        labels = "".join([f" [{r}](#ref-{r})" for r in ref_ids])
        labeled_sentence = f"{sentence.rstrip()}{labels} "
        lines[line_idx] = lines[line_idx].replace(sentence, labeled_sentence, 1)

    return lines, valid_hits


def _enrich_via_keywords_fallback(lines: list[str], active_open_items: list[dict]) -> tuple[list[str], list[dict]]:
    """兜底策略：当 Embedding API 超时/限流时，退化为本地分句关键词锚定"""
    valid_hits = []
    for item in active_open_items[:15]:
        content = item.get("memory", "")
        owner = item.get("metadata", {}).get("owner", "")
        needles = [w for w in [owner, content[:6]] if w and len(w) >= 2]
        if not needles:
            continue

        matched = False
        for i, line in enumerate(lines):
            if line.startswith(("#", "---", "|", "```")):
                continue
            sentences = _split_into_sentences(line)
            if not sentences:
                continue

            for sent_idx, s in enumerate(sentences):
                if any(needle in s for needle in needles):
                    valid_hits.append({
                        "line_idx": i,
                        "sent_idx": sent_idx,
                        "sentence": s,
                        "item": item,
                    })
                    matched = True
                    break
            if matched:
                break

    return _apply_hits_to_lines(lines, valid_hits)


def _get_embeddings(embedder, texts: list[str]) -> list[list[float]]:
    """批量计算文本嵌入向量（优先单批次 Batch 请求，异常时逐条兜底）"""
    if not texts:
        return []
    cleaned = [t.replace("\n", " ") for t in texts]
    if hasattr(embedder, "client") and hasattr(embedder.client, "embeddings"):
        try:
            model_name = getattr(getattr(embedder, "config", None), "model", "BAAI/bge-m3")
            resp = embedder.client.embeddings.create(
                input=cleaned,
                model=model_name,
                encoding_format="float"
            )
            return [item.embedding for item in resp.data]
        except Exception as e:
            logger.warning(f"[MemoryRender] 单批次 embedding 请求异常，退化为逐条计算: {e}")
    return [embedder.embed(t) for t in cleaned]


def enrich_markdown_with_history(
    minutes_md: str,
    active_open_items: list[dict],
    embedder=None
) -> str:
    """
    快轨总入口：分句级 BGE-M3 语义打标 (~100ms) 为主 + 规则匹配兜底 -> 追加 # 历史记忆关联
    """
    if not active_open_items:
        return minutes_md

    lines = minutes_md.split("\n")
    ordered_hits: list[dict] = []

    # 1. 尝试主通道：分句级 BGE-M3 语义打标 (~100ms)
    if embedder is not None:
        try:
            # 收集候选语意句：记录 (line_idx, sent_idx, sentence_str)
            candidate_records: list[tuple[int, int, str]] = []
            for i, line in enumerate(lines):
                stripped = line.strip()
                # 跳过标题、分割线、表格、代码块等非正文行
                if not stripped or stripped.startswith(("#", "---", "|", "```", "<a ")) or len(stripped) < 4:
                    continue
                sentences = _split_into_sentences(line)
                for sent_idx, s in enumerate(sentences):
                    s_clean = s.strip()
                    if len(s_clean) >= 4 and not s_clean.startswith(("#", "- [ ]")):
                        candidate_records.append((i, sent_idx, s))

            if candidate_records:
                candidate_texts = [r[2].strip() for r in candidate_records]
                mem_texts = [item.get("memory", "") for item in active_open_items[:15]]
                all_texts = candidate_texts + mem_texts

                # 单次 Batch 调用 BGE-M3 (~100ms)
                all_vectors = _get_embeddings(embedder, all_texts)
                candidate_vectors = all_vectors[:len(candidate_texts)]
                mem_vectors = all_vectors[len(candidate_texts):]

                # NumPy 矩阵瞬间算分
                sim_matrix = cosine_similarity_matrix(candidate_vectors, mem_vectors)

                valid_hits = []
                for mem_i, item in enumerate(active_open_items[:15]):
                    col_scores = sim_matrix[:, mem_i]
                    best_cand_idx = int(np.argmax(col_scores))
                    best_score = float(col_scores[best_cand_idx])

                    # 语义门禁阈值：相似度 >= 0.68 判定该分句与该历史事项高度对齐
                    if best_score >= 0.68:
                        line_idx, sent_idx, target_sentence = candidate_records[best_cand_idx]
                        valid_hits.append({
                            "line_idx": line_idx,
                            "sent_idx": sent_idx,
                            "sentence": target_sentence,
                            "item": item,
                            "score": best_score,
                        })

                lines, ordered_hits = _apply_hits_to_lines(lines, valid_hits)
            else:
                lines, ordered_hits = _enrich_via_keywords_fallback(lines, active_open_items)
        except Exception as e:
            logger.warning(f"[MemoryRender] BGE-M3 向量打标异常，自动降级为规则匹配: {e}")
            lines, ordered_hits = _enrich_via_keywords_fallback(lines, active_open_items)
    else:
        lines, ordered_hits = _enrich_via_keywords_fallback(lines, active_open_items)

    # 2. 若本次会议未引用任何历史事项，则完全不追加文末卡片，保持纪要干净纯洁
    if not ordered_hits:
        return "\n".join(lines)

    # 3. 文末仅追加实际在文中被引用的历史条目，序号与正文出现顺序严格 1:1 递增对齐
    footer_lines = ["\n\n---\n# 历史记忆关联\n"]
    for hit in ordered_hits:
        ref_id = hit["ref_id"]
        item = hit["item"]
        meta = item.get("metadata", {})
        anchor = f'<a id="ref-{ref_id}"></a>'
        line = f"- {anchor}[{ref_id}] {item.get('memory', '')}\n"
        line += f"  - 来源：{meta.get('meeting_time', '历史会议')} {meta.get('series', '')}\n"

        extra = []
        if meta.get("owner"):
            extra.append(f"责任人：{meta['owner']}")
        if meta.get("deadline"):
            extra.append(f"时限：{meta['deadline']}")
        if extra:
            line += f"  - {'  '.join(extra)}\n"
        footer_lines.append(line + "\n")

    return "\n".join(lines) + "".join(footer_lines)
