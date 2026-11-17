"""Step 4 端到端集成测试：
验证 stream_minutes_agent 第 0 秒拉起智能章节、流式直出、逻辑清洗、图文落盘 visual.html 以及接口协议。
"""
import asyncio
import json
from pathlib import Path
from typing import AsyncIterator

import pytest
from fastapi.testclient import TestClient

from main import app
from tasks.minutes.agent import generate_minutes, stream_minutes_agent


class MockLLMClient:
    """Mock LLM 客户端，用于模拟流式文本直出与 JSON 抽取。"""

    async def stream_text(self, prompt: str, system: str = "") -> AsyncIterator[str]:
        # 模拟流式生成 Markdown 正文
        tokens = [
            "# 2026 年度技术推进大会纪要\n\n",
            "会议由技术委员会与架构组联合召开。\n\n",
            "## 架构轻量化微服务拆分\n",
            "各业务模块完成依赖剔除，冷启动效率大幅提升，资源占用显著降低。\n\n",
            "## 图文智能纪要确定性渲染\n",
            "采用确定性 CSS 栅格与内联 SVG，生成自包含 visual.html 页面。\n\n",
            "## 总结与后续待办\n",
            "- 架构组完成全量回归测试\n",
            "- 各团队下周一同步进展\n",
        ]
        for t in tokens:
            yield t

    async def text(self, prompt: str, system: str = "", *args, **kwargs) -> str:
        # 用于 section_summary 或其它单次文本/JSON调用
        return """[
  {
    "time_start": "00:00:10",
    "title": "会议开场与背景汇报",
    "key_words": ["轻量化", "重构"],
    "section_summary": "全面启动微服务拆分与图文纪要增强工作。"
  }
]"""

    async def chat_json(self, system_prompt: str, user_prompt: str, *args, **kwargs):
        if "各个章节规划" in system_prompt:
            return {
                "sections": [
                    {
                        "section_id": "s2",
                        "visual": "table",
                        "necessity": 0.85,
                        "rationale": "对比新旧架构",
                        "required_data": ["性能参数"],
                    }
                ]
            }
        elif "开篇全景简报看板" in system_prompt:
            return {
                "headline": "2026 年度技术推进大会全景简报",
                "widgets": [
                    {
                        "kind": "cards",
                        "title": "核心议题架构",
                        "data": {
                            "items": [
                                {
                                    "tag": "议题 01",
                                    "title": "架构轻量化",
                                    "summary": "微服务彻底解耦重构",
                                    "key_points": ["耗时降低 80%"],
                                    "highlight": {"tag": "核心成效", "value": "敏捷轻量"},
                                }
                            ]
                        },
                    }
                ],
            }
        elif "指定视觉形态所需的结构化数据" in system_prompt:
            return {
                "kind": "table",
                "title": "新旧架构对比矩阵",
                "data": {
                    "columns": ["维度", "旧版", "轻量新版"],
                    "rows": [
                        {"cells": ["冷启动耗时", "1800ms", "120ms"], "evidence": "00:01"},
                        {"cells": ["内存占用", "400MB", "80MB"], "evidence": "00:02"},
                    ],
                },
                "evidence": ["00:01"],
            }
        return {}


def test_stream_minutes_agent_with_visual(tmp_path):
    """验证 stream_minutes_agent 产生 NDJSON、清洗落盘 minutes.md 和 visual.html。"""
    transcript = """00:00:10 张三
大家上午好，今天开始汇报架构轻量化与图文纪要增强进展。

00:01:20 李四
旧架构冷启动1800ms，新架构拆分后仅需120ms，内存占用从400MB降到80MB。
"""
    output_md_file = tmp_path / "user1" / "output" / "req_123" / "minutes.md"
    mock_client = MockLLMClient()

    async def _run():
        chunks = []
        done_event = None
        async for line in stream_minutes_agent(
            transcript=transcript,
            output_path=output_md_file,
            llm_client=mock_client,
            user_id="user1",
            request_id="req_123",
        ):
            event = json.loads(line)
            if event["type"] == "chunk":
                chunks.append(event["text"])
            elif event["type"] == "done":
                done_event = event

        # 1. 验证流式 chunk 输出正常
        assert len(chunks) > 0
        full_text = "".join(chunks)
        assert "2026 年度技术推进大会纪要" in full_text

        # 2. 验证 minutes.md 物理落盘
        assert output_md_file.is_file()
        md_saved = output_md_file.read_text(encoding="utf-8")
        assert "## 架构轻量化微服务拆分" in md_saved

        # 3. 验证 visual.html 同目录落盘
        visual_html_file = output_md_file.parent / "visual.html"
        assert visual_html_file.is_file()
        html_saved = visual_html_file.read_text(encoding="utf-8")

        # 4. 验证 visual.html 核心内容
        assert "<!DOCTYPE html>" in html_saved
        assert "vm-toc-sidebar" in html_saved
        assert "目录导航" in html_saved
        assert "架构轻量化" in html_saved
        assert "新旧架构对比矩阵" in html_saved
        assert "小艺慧记" in html_saved
        assert "data:image/png;base64," in html_saved

        # 5. 验证 done_event 数据载荷携带 visual_file
        assert done_event is not None
        assert done_event["code"] == 0
        assert done_event["data"]["file_name"] == "minutes.md"
        assert done_event["data"]["visual_file"] == "visual.html"

    asyncio.run(_run())


def test_sync_generate_minutes(tmp_path):
    """验证同步便捷函数 generate_minutes 返回完整纪要与携带 visual_file 的 done 事件。"""
    transcript = """00:00:10 张三
技术架构推进会议开始。
"""
    output_md_file = tmp_path / "test_sync" / "minutes.md"
    mock_client = MockLLMClient()

    async def _run():
        md, done = await generate_minutes(
            transcript=transcript,
            output_path=output_md_file,
            llm_client=mock_client,
        )
        assert "2026 年度技术推进大会纪要" in md
        assert done["code"] == 0
        assert done["data"]["visual_file"] == "visual.html"
        assert (output_md_file.parent / "visual.html").is_file()

    asyncio.run(_run())


def test_fastapi_endpoints():
    """验证 FastAPI /api/agent/v1/stream 与 /api/agent/v1 契约响应。"""
    client = TestClient(app)

    # 1. 测试健康检查
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
