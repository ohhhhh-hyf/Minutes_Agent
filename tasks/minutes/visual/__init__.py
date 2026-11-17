"""Visual Minutes (图文智能纪要) 核心包。"""
from .config import (
    DEFAULT_VISUAL_TEMPERATURE,
    MAX_EXTRACT_CONCURRENCY,
    MAX_SECTION_VISUALS,
    TRANSCRIPT_CHAR_BUDGET,
    VISUAL_MINUTES_ENABLED,
)
from .models.ir import VisualDocumentIR
from .parsing.parser import ParsedMinutes, parse_minutes
from .pipeline import (
    VisualMinutesResult,
    agenerate_visual_minutes,
    build_visual_ir,
    generate_visual_minutes,
)
from .rendering.compositor import DocumentCompositor

__all__ = [
    "DEFAULT_VISUAL_TEMPERATURE",
    "DocumentCompositor",
    "MAX_EXTRACT_CONCURRENCY",
    "MAX_SECTION_VISUALS",
    "ParsedMinutes",
    "TRANSCRIPT_CHAR_BUDGET",
    "VISUAL_MINUTES_ENABLED",
    "VisualDocumentIR",
    "VisualMinutesResult",
    "agenerate_visual_minutes",
    "build_visual_ir",
    "generate_visual_minutes",
    "parse_minutes",
]
