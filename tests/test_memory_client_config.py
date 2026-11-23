"""
tests/test_memory_client_config.py
单元测试：验证 mem0 初始化配置防呆机制（尤其是 SFT / WebSocket / vLLM 后端下的 dummy placeholder 保护）
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
import pytest

AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from tasks.minutes.memory.client import (
    _build_llm_config,
    _build_embedder_config,
    get_user_memory,
    _MEMORY_CLIENT_CACHE,
)


def test_build_llm_config_sft(monkeypatch):
    """验证 SFT / WebSocket 后端下返回安全的 dummy placeholder，api_key 绝不为空"""
    monkeypatch.setenv("LLM_BACKEND", "sft")
    cfg = _build_llm_config()
    assert cfg["provider"] == "openai"
    assert cfg["config"]["api_key"] == "EMPTY"
    assert cfg["config"]["model"] == "dummy-placeholder"

    monkeypatch.setenv("LLM_BACKEND", "websocket")
    cfg_ws = _build_llm_config()
    assert cfg_ws["config"]["api_key"] == "EMPTY"


def test_build_llm_config_http_without_key(monkeypatch):
    """验证 HTTP 官方后端未配置 key 时，安全兜底为 'EMPTY' 防止 OpenAI SDK 抛出 Missing credentials"""
    monkeypatch.setenv("LLM_BACKEND", "http")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    cfg = _build_llm_config()
    assert cfg["config"]["api_key"] == "EMPTY"


def test_build_embedder_config_fallback(monkeypatch):
    """验证 Embedder 未配置 key 时同样具有 'EMPTY' 安全兜底"""
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    cfg = _build_embedder_config()
    assert cfg["config"]["api_key"] == "EMPTY"


def test_get_user_memory_init_sft_without_credentials(monkeypatch, tmp_path):
    """端到端验证：在纯 SFT 环境下（无任何 OPENAI/DEEPSEEK 凭证），Memory 实例成功初始化而不崩溃"""
    monkeypatch.setenv("LLM_BACKEND", "sft")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_ADMIN_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    test_uid = "test_sft_user_999"
    # 清理缓存确保真实执行初始化流程
    _MEMORY_CLIENT_CACHE.pop(test_uid, None)

    mem = get_user_memory(user_id=test_uid, data_dir=tmp_path)
    assert mem is not None
    assert hasattr(mem, "vector_store")
    assert hasattr(mem, "embedding_model")
    # 验证能顺利写入，且不触发任何凭证异常
    assert test_uid in _MEMORY_CLIENT_CACHE
