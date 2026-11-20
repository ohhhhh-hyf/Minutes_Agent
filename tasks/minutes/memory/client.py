"""
tasks/minutes/memory/client.py
Mem0 实例工厂与租户物理沙箱单例缓存
支持 http (DeepSeek 官方) 与 vllm (服务器) 动态切换
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from dotenv import load_dotenv
from mem0 import Memory

logger = logging.getLogger("MemoryClient")

_AGENT_DIR = Path(__file__).resolve().parents[3]
_ROOT_DIR = _AGENT_DIR.parent

# 优先加载 Minutes_Agent/.env（子项目独占配置优先），若无则继承父级 demo/.env
if (_AGENT_DIR / ".env").is_file():
    load_dotenv(_AGENT_DIR / ".env", override=True)
if (_ROOT_DIR / ".env").is_file():
    load_dotenv(_ROOT_DIR / ".env", override=False)
load_dotenv(override=False)

_MEMORY_CLIENT_CACHE: dict[str, Memory] = {}


def _build_llm_config() -> dict:
    backend = os.getenv("LLM_BACKEND", "http").lower()
    if backend == "vllm":
        return {
            "provider": "openai",
            "config": {
                "model": os.getenv("LLM_VLLM_MODEL", "deepseek-v4-flash-0731"),
                "api_key": os.getenv("LLM_VLLM_API_KEY", "EMPTY"),
                "openai_base_url": os.getenv("LLM_VLLM_BASE_URL", "http://127.0.0.1:8000/v1"),
                "temperature": float(os.getenv("LLM_VLLM_TEMPERATURE", 0.1))
            }
        }
    else:  # http (DeepSeek 官方 API)
        base_url = os.getenv("DEEPSEEK_BASE_URL") or os.getenv("LLM_BASE_URL") or "https://api.deepseek.com"
        if not base_url.endswith("/v1"):
            base_url = f"{base_url.rstrip('/')}/v1"
        api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("LLM_API_KEY") or ""
        model = os.getenv("DEEPSEEK_MODEL") or os.getenv("LLM_MODEL") or "deepseek-chat"
        temp = float(os.getenv("DEEPSEEK_TEMPERATURE") or os.getenv("LLM_TEMPERATURE") or 0.1)
        return {
            "provider": "openai",
            "config": {
                "model": model,
                "api_key": api_key,
                "openai_base_url": base_url,
                "temperature": temp
            }
        }


def _build_embedder_config() -> dict:
    model = os.getenv("KNOWLEDGE_EMBEDDING_MODEL") or os.getenv("EMBEDDING_MODEL") or "BAAI/bge-m3"
    api_key = os.getenv("SILICONFLOW_API_KEY") or os.getenv("EMBEDDING_API_KEY") or ""
    base_url = os.getenv("KNOWLEDGE_EMBEDDING_BASE_URL") or os.getenv("EMBEDDING_BASE_URL") or "https://api.siliconflow.cn/v1"
    return {
        "provider": "openai",
        "config": {
            "model": model,
            "api_key": api_key,
            "openai_base_url": base_url
        }
    }


def get_user_memory(user_id: str = "default", data_dir: Path | str | None = None) -> Memory:
    """
    基于用户物理沙箱路径构建/获取独占的 Memory 实例
    路径：data/{user_id}/memory/qdrant/
    """
    uid = (user_id or "default").strip()
    if not uid:
        uid = "default"

    if uid in _MEMORY_CLIENT_CACHE:
        return _MEMORY_CLIENT_CACHE[uid]

    base_p = Path(data_dir).resolve() if data_dir else (_AGENT_DIR / "data").resolve()
    user_db_path = str(base_p / uid / "memory" / "qdrant")
    os.makedirs(user_db_path, exist_ok=True)
    
    config = {
        "vector_store": {
            "provider": "qdrant",
            "config": {
                "path": user_db_path,
                "on_disk": True,
                "embedding_model_dims": 1024,
            }
        },
        "llm": _build_llm_config(),
        "embedder": _build_embedder_config()
    }
    
    client = Memory.from_config(config)
    _MEMORY_CLIENT_CACHE[uid] = client
    logger.info(f"[MemoryClient] 初始化租户 Memory 实例成功: user_id={uid}, path={user_db_path}")
    return client
