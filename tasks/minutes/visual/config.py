"""Visual Minutes 轻量化独立配置模块。

环境变量统一定义在 demo/.env 中：
- VISUAL_MINUTES_ENABLED: 图文智能纪要总开关 (on/off)
- MAX_EXTRACT_CONCURRENCY: 视觉数据抽取并发上限 (默认 4)
- TRANSCRIPT_CHAR_BUDGET: 单章节原文检索字符预算 (默认 5000)
- MAX_SECTION_VISUALS: 全文正文图表软上限 (默认 8)

视觉大模型抽取温度在代码中直接写死为 0.2，确保结构稳定性与事实保真。
"""
from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

# 定位 demo 项目根目录并加载 .env
PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = PROJECT_ROOT / ".env"

if ENV_FILE.is_file():
    load_dotenv(ENV_FILE, override=False)
load_dotenv(override=False)

# 与 infra.llm 监控 label 对齐
VISUAL_LLM_LABEL = "visual_minutes"

# 1. 唯一图文纪要主开关 (默认 off)
VISUAL_MINUTES_ENABLED: bool = (
    os.getenv("VISUAL_MINUTES_ENABLED", "off").strip().lower() == "on"
)

# 2. 视觉抽取并发限制 (默认 4)
try:
    MAX_EXTRACT_CONCURRENCY: int = int(os.getenv("MAX_EXTRACT_CONCURRENCY", "4").strip())
except ValueError:
    MAX_EXTRACT_CONCURRENCY = 4

# 3. 单章节原文检索字符上限 (默认 5000 字)
try:
    TRANSCRIPT_CHAR_BUDGET: int = int(os.getenv("TRANSCRIPT_CHAR_BUDGET", "5000").strip())
except ValueError:
    TRANSCRIPT_CHAR_BUDGET = 5000

# 4. 全文正文图表软上限 (默认 8 张)
try:
    MAX_SECTION_VISUALS: int = int(os.getenv("MAX_SECTION_VISUALS", "8").strip())
except ValueError:
    MAX_SECTION_VISUALS = 8

# 5. 视觉模型调用温度（代码直接写死为 0.2，不可外部环境变量扰动）
DEFAULT_VISUAL_TEMPERATURE: float = 0.2

# 开篇通览图概览限制
MAX_MASTER_BLOCK_SUMMARY_CHARS = 120
MAX_MASTER_BLOCK_KEY_POINTS = 3
MAX_MASTER_KEY_POINT_CHARS = 60

# 允许的最小视觉数据阈值（低于此门槛判定为无效图表，自动丢弃）
MIN_TIMELINE_EVENTS = 3
MIN_TABLE_ROWS = 2
MIN_TABLE_COLUMNS = 2
MIN_FLOW_NODES = 3
MIN_FLOW_EDGES = 2
MIN_TREE_NODES = 3
MIN_STATS_METRICS = 2

# 单个视觉的数据上限，避免将整篇正文塞入单一图表
MAX_TIMELINE_EVENTS = 10
MAX_TABLE_ROWS = 10
MAX_TABLE_COLUMNS = 6
MAX_FLOW_NODES = 10
MAX_FLOW_EDGES = 16
MAX_TREE_NODES = 12
MAX_STATS_METRICS = 6

__all__ = [
    "DEFAULT_VISUAL_TEMPERATURE",
    "ENV_FILE",
    "MAX_EXTRACT_CONCURRENCY",
    "MAX_FLOW_EDGES",
    "MAX_FLOW_NODES",
    "MAX_MASTER_BLOCK_KEY_POINTS",
    "MAX_MASTER_BLOCK_SUMMARY_CHARS",
    "MAX_MASTER_KEY_POINT_CHARS",
    "MAX_SECTION_VISUALS",
    "MAX_STATS_METRICS",
    "MAX_TABLE_COLUMNS",
    "MAX_TABLE_ROWS",
    "MAX_TIMELINE_EVENTS",
    "MAX_TREE_NODES",
    "MIN_FLOW_EDGES",
    "MIN_FLOW_NODES",
    "MIN_STATS_METRICS",
    "MIN_TABLE_COLUMNS",
    "MIN_TABLE_ROWS",
    "MIN_TIMELINE_EVENTS",
    "MIN_TREE_NODES",
    "PROJECT_ROOT",
    "TRANSCRIPT_CHAR_BUDGET",
    "VISUAL_LLM_LABEL",
    "VISUAL_MINUTES_ENABLED",
]
