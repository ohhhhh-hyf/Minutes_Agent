"""LLM 客户端构造。

连接方式、API Key、模型名全部由项目根目录 .env 与 infra.llm 决定。
"""
from __future__ import annotations

from infra.llm import LLMClient


def default_client() -> LLMClient:
    """构造默认 LLMClient。"""
    return LLMClient()


__all__ = ["default_client"]
