"""Step 3 智能章节前置并发模块 + 视觉抽取三 Agent 流水线测试。

验证范围：
1. section_summary 智能章节附录模块：
   - 逐字稿正则时间戳提取与清洗
   - 规则时间窗切块兜底 fallback_sections
   - 标准 Markdown 引用块渲染 render_markdown
2. agents 规划与抽取三 Agent：
   - SectionPlanner (Agent 1): 章节图表形态规划与异常容灾回退
   - VisualExtractor (Agent 2): 结构化图表数据抽取与类型匹配
   - MasterPlanner (Agent 3): 全局大看板规划与实质性议题立柱保底 fallback_master
3. pipeline 顶层流水线：
   - build_visual_ir: Parser -> Planner -> Extractor/Master 并发调度 -> Validator
   - agenerate_visual_minutes: 端到端异步生成 visual.html
"""
import pytest
from tasks.minutes.visual.agents.extractor import VisualExtractor
from tasks.minutes.visual.agents.master import MasterPlanner, fallback_master
from tasks.minutes.visual.agents.planner import SectionPlanner
from tasks.minutes.visual.models.ir import (
    CardsVisual,
    FlowVisual,
    SectionDecision,
    TableVisual,
    VisualDocumentIR,
)
from tasks.minutes.visual.parsing.parser import parse_minutes
from tasks.minutes.visual.pipeline import (
    VisualMinutesResult,
    agenerate_visual_minutes,
    build_visual_ir,
)
from tasks.minutes.visual.section_summary import (
    SectionSummary,
    agenerate_section_summary,
    parse_transcript,
    render_markdown,
)
from tasks.minutes.visual.section_summary.fallback import fallback_sections


def test_section_summary_parser_and_fallback():
    """验证智能章节逐字稿解析、纯规则时间窗切分保底与 Markdown 渲染。"""
    raw_transcript = """
00:00:10 张三
大家上午好，今天召开技术架构轻量化推进会。

00:02:30 李四
目前我们正在进行解耦重构，移除了旧有重度依赖包，冷启动从 2 秒降到 200 毫秒。

00:08:00 王五
关于图文可视化增强，我们采用纯确定性 CSS 与内联 SVG，生成完全自包含的 visual.html。
"""
    lines = parse_transcript(raw_transcript)
    assert len(lines) == 3
    assert lines[0].seconds == 10
    assert lines[1].seconds == 150
    assert lines[2].seconds == 480

    # 验证规则兜底切分
    sections = fallback_sections(lines, gap_seconds=60, window_seconds=200)
    assert len(sections) >= 2
    assert sections[0].time_start == "00:00:10"

    # 验证 Markdown 渲染
    md_output = render_markdown(sections)
    assert "**00:00:10" in md_output
    assert "> " in md_output


def test_section_planner_fallback():
    """验证 SectionPlanner 在 LLM 异常时优雅回退为全部 none，不阻塞主流程。"""
    class FailingLLM:
        async def json_call(self, *args, **kwargs):
            raise RuntimeError("LLM network timeout")

    async def _test():
        parsed = parse_minutes("""# 会议纪要
## 议题一
讨论了微服务解耦。
## 议题二
讨论了图文纪要。
""")
        planner = SectionPlanner(FailingLLM())
        plan_resp = await planner.plan(parsed.sections)

        # 规划失败时应全量兜底为 none
        assert len(plan_resp.sections) == len(parsed.sections)
        assert all(d.visual == "none" for d in plan_resp.sections)
        assert any("planner fallback" in d.rationale for d in plan_resp.sections)

    import asyncio
    asyncio.run(_test())


def test_master_planner_fallback_rule():
    """验证 Master 看板纯规则保底：自动剔除结构性词汇，提取实质议题立柱。"""
    parsed = parse_minutes("""# 战略研讨纪要
## 全文摘要
会议简短回顾。
## 引擎轻量化改造方案
主服务彻底解耦，提升冷启动效率。
## 图文智能纪要增强
确定性渲染引擎，内嵌小艺离线资产。
## 待办事项
架构组跟进联调。
""")
    master = fallback_master(parsed)
    assert master.headline == "战略研讨纪要"
    assert len(master.widgets) == 1
    assert isinstance(master.widgets[0], CardsVisual)

    # 验证结构性词汇（全文摘要、待办事项）被剔除，立柱聚焦核心议题
    titles = [item.title for item in master.widgets[0].data.items]
    assert "全文摘要" not in titles
    assert "待办事项" not in titles
    assert "引擎轻量化改造方案" in titles
    assert "图文智能纪要增强" in titles


def test_build_visual_ir_and_pipeline(tmp_path):
    """验证端到端流水线 build_visual_ir 与 agenerate_visual_minutes。"""
    class MockSuccessLLM:
        async def json_call(self, system_prompt, user_prompt, *args, **kwargs):
            # 根据 system_prompt 判定是哪个 agent 调用
            if "各个章节规划" in system_prompt:
                # SectionPlanner
                return {
                    "sections": [
                        {
                            "section_id": "s2",
                            "visual": "table",
                            "necessity": 0.9,
                            "rationale": "对比新旧方案",
                            "required_data": ["性能指标对比"],
                        }
                    ]
                }
            elif "开篇全景简报看板" in system_prompt:
                # MasterPlanner
                return {
                    "headline": "全景战略看板",
                    "widgets": [
                        {
                            "kind": "cards",
                            "title": "战略立柱",
                            "data": {
                                "items": [
                                    {
                                        "tag": "01",
                                        "title": "架构轻量化",
                                        "summary": "微服务框架重构",
                                        "key_points": ["耗时减半"],
                                    }
                                ]
                            },
                        }
                    ],
                }
            elif "指定视觉形态所需的结构化数据" in system_prompt:
                # VisualExtractor
                return {
                    "kind": "table",
                    "title": "新旧架构性能对比矩阵",
                    "data": {
                        "columns": ["维度", "旧架构", "新轻量版"],
                        "rows": [
                            {"cells": ["时延", "1500ms", "80ms"], "evidence": "逐字稿00:02"},
                            {"cells": ["内存", "350MB", "60MB"], "evidence": "逐字稿00:03"},
                        ],
                    },
                    "evidence": ["00:02"],
                }
            return {}

    async def _test():
        minutes_text = """# 季度架构重构总结纪要

本会议由技术架构组召开。

## 新旧架构指标对比分析
通过对新旧版本服务压测，轻量化重构在吞吐与资源占用方面均表现优异。
"""
        transcript_text = """00:01:00 张三
现在汇报新旧架构性能对比，旧架构冷启动1500ms，新轻量版只需要80ms。
"""

        mock_llm = MockSuccessLLM()

        # 1. 测试 build_visual_ir
        ir = await build_visual_ir(minutes_text, transcript_text, llm=mock_llm)
        assert isinstance(ir, VisualDocumentIR)
        assert ir.master.headline == "全景战略看板"
        assert len(ir.sections) == 1
        assert ir.sections[0].visual.kind == "table"

        # 2. 测试端到端生成 visual.html
        out_html_path = tmp_path / "visual.html"
        res = await agenerate_visual_minutes(
            minutes_text,
            transcript_text,
            output_path=str(out_html_path),
            llm=mock_llm,
            section_summary_markdown="> **【智能章节速览】**\n> 00:01:00 汇报新旧架构性能",
        )
        assert isinstance(res, VisualMinutesResult)
        assert out_html_path.exists()
        html_text = out_html_path.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in html_text
        assert "战略立柱" in html_text
        assert "架构轻量化" in html_text
        assert "新旧架构性能对比矩阵" in html_text
        assert "智能章节速览" in html_text
        assert "小艺慧记" in html_text

    import asyncio
    asyncio.run(_test())
