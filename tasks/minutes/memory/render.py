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


def _enrich_via_keywords_fallback(lines: list[str], active_open_items: list[dict]) -> tuple[list[str], list[tuple[int, dict]]]:
    """兜底策略：当 Embedding API 超时/限流时，退化为本地分句关键词锚定"""
    matched_items = []
    for idx, item in enumerate(active_open_items[:15], start=1):
        content = item.get("memory", "")
        owner = item.get("metadata", {}).get("owner", "")
        needles = [w for w in [owner, content[:6]] if w and len(w) >= 2]

        matched = False
        for i, line in enumerate(lines):
            if line.startswith(("#", "---", "|", "```")):
                continue
            sentences = _split_into_sentences(line)
            if not sentences:
                continue

            for s in sentences:
                if any(needle in s for needle in needles):
                    # 精准在匹配句子的句末插入角标
                    labeled_s = f"{s.rstrip()} [{idx}](#ref-{idx}) "
                    lines[i] = lines[i].replace(s, labeled_s, 1)
                    matched = True
                    break
            if matched:
                break
        matched_items.append((idx, item))
    return lines, matched_items


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
    matched_items: list[tuple[int, dict]] = []

    # 1. 尝试主通道：分句级 BGE-M3 语义打标 (~100ms)
    if embedder is not None:
        try:
            # 收集候选语意句：记录 (line_idx, sentence_str)
            candidate_records: list[tuple[int, str]] = []
            for i, line in enumerate(lines):
                stripped = line.strip()
                # 跳过标题、分割线、表格、代码块等非正文行
                if not stripped or stripped.startswith(("#", "---", "|", "```", "<a ")) or len(stripped) < 4:
                    continue
                sentences = _split_into_sentences(line)
                for s in sentences:
                    s_clean = s.strip()
                    if len(s_clean) >= 4 and not s_clean.startswith(("#", "- [ ]")):
                        candidate_records.append((i, s))

            if candidate_records:
                candidate_texts = [r[1].strip() for r in candidate_records]
                mem_texts = [item.get("memory", "") for item in active_open_items[:15]]
                all_texts = candidate_texts + mem_texts

                # 单次 Batch 调用 BGE-M3 (~100ms)
                all_vectors = _get_embeddings(embedder, all_texts)
                candidate_vectors = all_vectors[:len(candidate_texts)]
                mem_vectors = all_vectors[len(candidate_texts):]

                # NumPy 矩阵瞬间算分
                sim_matrix = cosine_similarity_matrix(candidate_vectors, mem_vectors)

                for mem_idx, item in enumerate(active_open_items[:15], start=1):
                    col_scores = sim_matrix[:, mem_idx - 1]
                    best_cand_idx = int(np.argmax(col_scores))
                    best_score = float(col_scores[best_cand_idx])

                    # 语义门禁阈值：相似度 >= 0.68 判定该分句与该历史事项高度对齐
                    if best_score >= 0.68:
                        target_line_idx, target_sentence = candidate_records[best_cand_idx]
                        # 句末标点后精准插标，不破坏段落其他文字
                        labeled_sentence = f"{target_sentence.rstrip()} [{mem_idx}](#ref-{mem_idx}) "
                        lines[target_line_idx] = lines[target_line_idx].replace(target_sentence, labeled_sentence, 1)

                    matched_items.append((mem_idx, item))
            else:
                matched_items = list(enumerate(active_open_items[:15], start=1))
        except Exception as e:
            logger.warning(f"[MemoryRender] BGE-M3 向量打标异常，自动降级为规则匹配: {e}")
            lines, matched_items = _enrich_via_keywords_fallback(lines, active_open_items)
    else:
        lines, matched_items = _enrich_via_keywords_fallback(lines, active_open_items)

    # 2. 文末追加一级标题 # 历史记忆关联
    footer_lines = ["\n\n---\n# 历史记忆关联\n"]
    for idx, item in matched_items:
        meta = item.get("metadata", {})
        anchor = f'<a id="ref-{idx}"></a>'
        line = f"- {anchor}[{idx}] {item.get('memory', '')}\n"
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
