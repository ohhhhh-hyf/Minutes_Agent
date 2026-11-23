"""
tests/test_memory_render.py
单元测试：验证记忆渲染阈值 MEMORY_MATCH_THRESHOLD 的配置化读取与判定逻辑
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock
import pytest

AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from tasks.minutes.memory.render import (
    DEFAULT_MEMORY_MATCH_THRESHOLD,
    get_memory_match_threshold,
    enrich_markdown_with_history,
)


def test_get_memory_match_threshold_default(monkeypatch):
    """验证未配置环境变量或为空时，返回默认值 0.6"""
    monkeypatch.delenv("MEMORY_MATCH_THRESHOLD", raising=False)
    assert get_memory_match_threshold() == 0.6
    assert DEFAULT_MEMORY_MATCH_THRESHOLD == 0.6

    monkeypatch.setenv("MEMORY_MATCH_THRESHOLD", "   ")
    assert get_memory_match_threshold() == 0.6


def test_get_memory_match_threshold_custom(monkeypatch):
    """验证配置合法数值时，能够正确读取并转换为 float"""
    monkeypatch.setenv("MEMORY_MATCH_THRESHOLD", "0.75")
    assert get_memory_match_threshold() == 0.75

    monkeypatch.setenv("MEMORY_MATCH_THRESHOLD", "0.5")
    assert get_memory_match_threshold() == 0.5


def test_get_memory_match_threshold_invalid(monkeypatch):
    """验证配置非数字非法字符时，防御性降级为默认值 0.6 且不崩溃"""
    monkeypatch.setenv("MEMORY_MATCH_THRESHOLD", "invalid_string")
    assert get_memory_match_threshold() == 0.6


def test_enrich_markdown_threshold_filtering(monkeypatch):
    """验证 enrich_markdown_with_history 动态遵循 MEMORY_MATCH_THRESHOLD 进行门禁过滤"""
    # 构造 mock embedder：使得候选句与记忆项的向量点积为 0.65
    mock_embedder = MagicMock()
    # 模拟 2 维归一化向量，让它们的余弦相似度为 0.65
    # 例如 vec1 = [1.0, 0.0], vec2 = [0.65, sqrt(1 - 0.65^2)]
    import math
    sin_val = math.sqrt(1 - 0.65 * 0.65)
    vec_candidate = [1.0, 0.0]
    vec_memory = [0.65, sin_val]

    # _get_embeddings 会接收 [candidate_text, mem_text]，返回两个向量
    mock_embedder.embed.side_effect = lambda t: vec_candidate if "汇报" in t else vec_memory
    # 若调用 client.embeddings.create
    mock_embedder.client = None

    active_items = [
        {
            "id": "mem_001",
            "memory": "张三负责推进压测工作",
            "metadata": {"meeting_time": "2026/11/13", "memory_link": "周会"},
        }
    ]

    sample_md = """# 会议纪要
## 议题一
张三在会上汇报了压测进度。
"""

    # 1. 当阈值设为 0.70 时，相似度 0.65 < 0.70，不应命中
    monkeypatch.setenv("MEMORY_MATCH_THRESHOLD", "0.70")
    res_high = enrich_markdown_with_history(sample_md, active_items, embedder=mock_embedder)
    assert "[1](#ref-1)" not in res_high
    assert "# 历史记忆关联" not in res_high

    # 2. 当阈值设为 0.60 时，相似度 0.65 >= 0.60，应当精准命中插标
    monkeypatch.setenv("MEMORY_MATCH_THRESHOLD", "0.60")
    res_low = enrich_markdown_with_history(sample_md, active_items, embedder=mock_embedder)
    assert "[1](#ref-1)" in res_low
    assert "# 历史记忆关联" in res_low
    assert "张三负责推进压测工作" in res_low
