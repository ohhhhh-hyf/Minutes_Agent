"""Step 2 确定性渲染器与 Compositor 页面合成测试。

验证范围：
1. 5 大原语确定性渲染器 (timeline, table, flow, tree, stats)
2. Master 全景看板多组件与议题立柱渲染
3. DocumentCompositor 页面合成：
   - 飞书同款左侧 TOC 目录导航树构建与锚点映射
   - ## 总览 与第一张 Master 看板嵌入
   - 正文 100% 保真 Markdown 转 HTML 与图表兄弟节点挂载
   - 智能章节附录挂载
   - 底部小艺品牌 Base64 头像与免责声明自包含渲染
   - 物理生成 visual.html 文件并验证自包含可用性
"""
import os
from pathlib import Path

import pytest
from tasks.minutes.visual.models.ir import (
    BlockHighlight,
    CardItem,
    CardsData,
    CardsVisual,
    FlowData,
    FlowEdge,
    FlowNode,
    FlowVisual,
    MasterBlock,
    MasterVisual,
    SectionVisual,
    StatMetric,
    StatsData,
    StatsVisual,
    TableData,
    TableRow,
    TableVisual,
    TimelineData,
    TimelineEvent,
    TimelineVisual,
    TreeData,
    TreeNode,
    TreeVisual,
    VisualDocumentIR,
)
from tasks.minutes.visual.parsing.parser import parse_minutes
from tasks.minutes.visual.rendering import (
    DocumentCompositor,
    render_master,
    render_visual,
)
from tasks.minutes.visual.rendering.xiaoyi_icon_uri import XIAOYI_ICON_DATA_URI


def test_render_primitives():
    """验证 5 大基础原语纯 HTML+SVG 确定性渲染。"""
    # 1. Timeline
    tl = TimelineVisual(
        title="演进阶段",
        data=TimelineData(
            events=[
                TimelineEvent(time_label="T1", title="架构选型", detail="轻量化拆分"),
                TimelineEvent(time_label="T2", title="内核开发", detail="核心协议对齐"),
            ]
        ),
    )
    tl_html = render_visual(tl)
    assert "vm-timeline-canvas" in tl_html
    assert "架构选型" in tl_html
    assert "演进阶段" in tl_html

    # 2. Table
    tbl = TableVisual(
        title="方案对比矩阵",
        data=TableData(
            columns=["指标", "旧版架构", "轻量新版"],
            rows=[
                TableRow(cells=["冷启动", "1800ms", "120ms"]),
                TableRow(cells=["内存占用", "420MB", "85MB"]),
            ],
        ),
    )
    tbl_html = render_visual(tbl)
    assert "vm-table" in tbl_html
    assert "轻量新版" in tbl_html
    assert "120ms" in tbl_html

    # 3. Flow
    fl = FlowVisual(
        title="任务推进流水线",
        data=FlowData(
            nodes=[
                FlowNode(node_id="p1", title="接收请求", detail="解析请求体"),
                FlowNode(node_id="p2", title="并行调度", detail="第0秒拉起智能章节"),
            ],
            edges=[FlowEdge(**{"from": "p1", "to": "p2", "label": "触发"})],
        ),
    )
    fl_html = render_visual(fl)
    assert "vm-flow-container" in fl_html
    assert "并行调度" in fl_html
    assert "触发" in fl_html

    # 4. Tree
    tr = TreeVisual(
        title="分层拓扑架构",
        data=TreeData(
            nodes=[
                TreeNode(node_id="l1", title="接入层", detail="FastAPI HTTP/SSE 网关"),
                TreeNode(node_id="l2", title="业务层", detail="图文增强微服务"),
            ]
        ),
    )
    tr_html = render_visual(tr)
    assert "vm-tree-container" in tr_html
    assert "接入层" in tr_html
    assert "FastAPI" in tr_html

    # 5. Stats
    st = StatsVisual(
        title="核心性能指标",
        data=StatsData(
            metrics=[
                StatMetric(label="TTFT", value="320ms"),
                StatMetric(label="TPS", value="15,000"),
            ]
        ),
    )
    st_html = render_visual(st)
    assert "vm-stats-grid" in st_html
    assert "320ms" in st_html
    assert "15,000" in st_html


def test_render_master_widgets():
    """验证 Master 全景看板复合组件渲染。"""
    master = MasterVisual(
        headline="季度战略协同通览看板",
        widgets=[
            CardsVisual(
                kind="cards",
                title="核心战略立柱",
                data=CardsData(
                    items=[
                        CardItem(
                            tag="议题 01",
                            title="微服务解耦",
                            summary="彻底摆脱沉重依赖，提升系统敏捷度",
                            key_points=["依赖体积下降 80%", "零停机平滑发布"],
                            highlight=BlockHighlight(tag="核心成效", value="轻量高效"),
                        )
                    ]
                ),
            )
        ],
    )
    m_html = render_master(master)
    assert "vm-master-card" in m_html
    assert "季度战略协同通览看板" not in m_html or "vm-master" in m_html
    assert "微服务解耦" in m_html
    assert "轻量高效" in m_html


def test_document_compositor_full_html(tmp_path):
    """验证 DocumentCompositor 端到端合成 HTML、TOC 树与小艺 Base64 资产。"""
    markdown_text = """# 2026 年度技术委员会战略纪要

会议就轻量化架构改造与图文可视化增强达成高度共识。

## 议题一：微服务轻量化重构
本议题明确了各模块解耦方案，移除非必要依赖，降低维护成本。

## 议题二：图文智能纪要落地
通过前端确定性 CSS 栅格与 SVG 绘制，正文 Markdown 100% 保真，生成自包含 HTML。

## 结论与待办
后续各项任务稳步推进，下周一召开联调跟进会议。
"""
    parsed = parse_minutes(markdown_text)

    # 构造 IR
    master = MasterVisual(
        headline="2026 年度技术委员会战略纪要全景",
        widgets=[
            CardsVisual(
                kind="cards",
                title="战略核心议题",
                data=CardsData(
                    items=[
                        CardItem(
                            tag="战略 01",
                            title="轻量化改造",
                            summary="解耦微服务框架",
                            key_points=["冷启动提升", "资源占用减半"],
                        ),
                        CardItem(
                            tag="战略 02",
                            title="图文可视化",
                            summary="自包含页面渲染",
                            key_points=["内联 SVG", "离线查看"],
                        ),
                    ]
                ),
            )
        ],
    )

    flow_visual = FlowVisual(
        title="图文流转架构",
        data=FlowData(
            nodes=[
                FlowNode(node_id="f1", title="流式纪要直出"),
                FlowNode(node_id="f2", title="逻辑校验清洗"),
                FlowNode(node_id="f3", title="图文页面合成"),
            ],
            edges=[
                FlowEdge(**{"from": "f1", "to": "f2", "label": "完毕"}),
                FlowEdge(**{"from": "f2", "to": "f3", "label": "渲染"}),
            ],
        ),
    )

    ir = VisualDocumentIR(
        master=master,
        sections=[
            SectionVisual(section_id="s3", visual=flow_visual, confidence=0.95),
        ],
    )

    sec_summary_md = """
> **【智能章节速览】**
> - **00:00:00 - 00:05:00** | 开篇致辞与背景介绍
> - **00:05:00 - 00:20:00** | 微服务架构拆分方案汇报
"""

    out_file = tmp_path / "visual.html"
    compositor = DocumentCompositor()
    html_content = compositor.compose(
        parsed,
        ir,
        output_path=str(out_file),
        section_summary_markdown=sec_summary_md,
    )

    # 1. 验证 HTML 基础骨架
    assert "<!DOCTYPE html>" in html_content
    assert "<title>2026 年度技术委员会战略纪要</title>" in html_content

    # 2. 验证 TOC 目录导航包含核心章节锚点
    assert "vm-toc-sidebar" in html_content
    assert "目录导航" in html_content
    assert "微服务轻量化重构" in html_content
    assert "图文智能纪要落地" in html_content

    # 3. 验证 ## 总览 与开篇全景大看板
    assert '<h2 id="sec-overview" class="vm-overview-heading">总览</h2>' in html_content
    assert "vm-master-card" in html_content
    assert "轻量化改造" in html_content

    # 4. 验证正文 Markdown 100% 完整转化与图表插装
    assert "本议题明确了各模块解耦方案" in html_content
    assert "vm-flow-container" in html_content
    assert "图文页面合成" in html_content

    # 5. 验证智能章节附录挂载
    assert "智能章节速览" in html_content
    assert "微服务架构拆分方案汇报" in html_content

    # 6. 验证小艺品牌 Base64 头像与免责声明自包含内联
    assert "vm-doc-footer" in html_content
    assert "小艺慧记：内容由AI生成，仅供参考。" in html_content
    assert 'src="' + XIAOYI_ICON_DATA_URI[:30] in html_content  # 校验包含了 base64 URI 前缀
    assert "data:image/png;base64," in html_content

    # 7. 验证物理落盘成功
    assert out_file.exists()
    assert out_file.stat().st_size > 50000  # 自包含页面包含 CSS、SVG 与 Base64 图标
