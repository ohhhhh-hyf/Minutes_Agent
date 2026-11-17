"""Step 1 基础底座与数据逻辑层单元测试。

验证范围：
1. config 配置读取与默认值（4个env参数与0.2写死温度）
2. models/ir 强类型 Pydantic IR 数据模型
3. parsing/parser AST 章节提取与容器型提升
4. parsing/retrieval 现场原文相关度加权检索与预算截断
5. validation/validator 表格补齐、死边清理、树父节点自愈、看板保底、8张上限截断
6. llm 模块 JSON 提取与适配器初始化
"""
import pytest
from tasks.minutes.visual import (
    DEFAULT_VISUAL_TEMPERATURE,
    MAX_EXTRACT_CONCURRENCY,
    MAX_SECTION_VISUALS,
    TRANSCRIPT_CHAR_BUDGET,
    VISUAL_MINUTES_ENABLED,
)
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
    PlannerResponse,
    SectionDecision,
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
from tasks.minutes.visual.parsing.retrieval import retrieve_transcript
from tasks.minutes.visual.validation.validator import validate_document
from tasks.minutes.visual.llm import VisualLLM, extract_json_object


def test_visual_config_loaded():
    """验证 .env 4 项配置正确读取且温度固定为 0.2。"""
    assert VISUAL_MINUTES_ENABLED is True
    assert MAX_EXTRACT_CONCURRENCY == 4
    assert TRANSCRIPT_CHAR_BUDGET == 5000
    assert MAX_SECTION_VISUALS == 8
    assert DEFAULT_VISUAL_TEMPERATURE == 0.2


def test_models_ir_primitives():
    """验证 5 大图表原语及 IR 强类型模型序列化与反序列化。"""
    # 1. Timeline
    tl = TimelineVisual(
        title="技术迭代演化",
        data=TimelineData(
            events=[
                TimelineEvent(time_label="Q1", title="阶段一", detail="架构重构", evidence="发言记录"),
                TimelineEvent(time_label="Q2", title="阶段二", detail="压测达标", evidence="发言记录"),
                TimelineEvent(time_label="Q3", title="阶段三", detail="正式商用", evidence="发言记录"),
            ]
        ),
    )
    assert tl.kind == "timeline"
    assert len(tl.data.events) == 3

    # 2. Table
    tbl = TableVisual(
        title="技术方案对比",
        data=TableData(
            columns=["维度", "方案A", "方案B"],
            rows=[
                TableRow(cells=["响应时延", "15ms", "50ms"], evidence="实测"),
                TableRow(cells=["吞吐量", "10k TPS", "3k TPS"], evidence="实测"),
            ],
        ),
    )
    assert tbl.kind == "table"
    assert len(tbl.data.rows) == 2

    # 3. Flow
    fl = FlowVisual(
        title="数据流转处理",
        data=FlowData(
            nodes=[
                FlowNode(node_id="n1", title="接收请求"),
                FlowNode(node_id="n2", title="执行分类"),
                FlowNode(node_id="n3", title="流式下发"),
            ],
            edges=[
                FlowEdge(**{"from": "n1", "to": "n2", "label": "分流"}),
                FlowEdge(**{"from": "n2", "to": "n3", "label": "直出"}),
            ],
        ),
    )
    assert fl.kind == "flow"
    assert fl.data.edges[0].source == "n1"

    # 4. Tree
    tr = TreeVisual(
        title="系统分层架构",
        data=TreeData(
            nodes=[
                TreeNode(node_id="root", title="应用层", parent_id=None),
                TreeNode(node_id="sub1", title="API 网关", parent_id="root"),
                TreeNode(node_id="sub2", title="引擎核心", parent_id="root"),
            ]
        ),
    )
    assert tr.kind == "tree"
    assert tr.data.nodes[1].parent_id == "root"

    # 5. Stats
    st = StatsVisual(
        title="关键性能指标",
        data=StatsData(
            metrics=[
                StatMetric(label="TTFT 首字时延", value="420ms"),
                StatMetric(label="错误率", value="< 0.01%"),
            ]
        ),
    )
    assert st.kind == "stats"
    assert len(st.data.metrics) == 2


def test_markdown_ast_parser():
    """验证 Markdown AST 自适应切分与容器型子议题自动提升。"""
    sample_md = """# 季度技术研讨与业务协同纪要

本会议由架构组与业务团队联合召开。

## 会议核心背景
当前服务正在经历轻量化微服务拆分改造。

## 要点梳理与业务落地
### 引擎轻量化改造方案
主服务解耦，剔除旧有重度依赖，提升冷启动效率。

### 视觉图文纪要增强
通过前端确定性 CSS 网格与 SVG 内联矢量，实现 100% 离线自包含查看。

## 待办行动项
- 架构组完成全量回归用例
- 测试组提供压力测试报告
"""
    parsed = parse_minutes(sample_md)
    assert parsed.document_title == "季度技术研讨与业务协同纪要"
    assert "本会议由架构组与业务团队联合召开" in parsed.sections[0].markdown

    # 验证子议题被提升为独立 section，且带有清晰的层次 path
    sec_titles = [s.heading for s in parsed.sections]
    assert "会议核心背景" in sec_titles
    assert "引擎轻量化改造方案" in sec_titles
    assert "视觉图文纪要增强" in sec_titles
    assert "待办行动项" in sec_titles

    # 验证 path 携带父容器标题
    enhanced_sec = next(s for s in parsed.sections if s.heading == "视觉图文纪要增强")
    assert enhanced_sec.path == ["要点梳理与业务落地", "视觉图文纪要增强"]
    assert enhanced_sec.section_id.startswith("s")


def test_transcript_retrieval():
    """验证逐字稿段落相关性检索与原文时序保留。"""
    transcript = """张三 00:01:10
我们首先讨论一下引擎轻量化微服务改造的背景和目的。

李四 00:03:20
关于视觉图文纪要，我们希望生成的 visual.html 是完全自包含的，内置内联 SVG 和 Base64 头像。

王五 00:06:40
对，而且图文智能章节要和主纪要任务在第 0 秒并发启动，这样完全不阻塞流式输出。

赵六 00:09:00
财务报销流程也需要同步更新，请行政同事下周跟进。
"""
    retrieved = retrieve_transcript("视觉图文纪要 visual.html 智能章节并发启动", transcript, budget=300)
    assert "视觉图文纪要" in retrieved
    assert "李四" in retrieved
    assert "王五" in retrieved
    # 无关段落（如财务报销）在预算有限时不被优先拉取
    assert len(retrieved) <= 300


def test_validator_auto_healing():
    """验证工业级数据校验、自愈修复与图表软上限截断。"""
    md = """# 测试会议
## 第一章节
这是第一章节内容。
## 第二章节
这是第二章节内容。
"""
    parsed = parse_minutes(md)

    # 构造带有瑕疵需要自愈的图表
    # 1. 表格某行少列（少于 columns 长度需补 ""），某行多列（需截断）
    flawed_table = TableVisual(
        title="测试表格",
        data=TableData(
            columns=["列1", "列2", "列3"],
            rows=[
                TableRow(cells=["A1"], evidence="e1"),  # 少2列，需补空
                TableRow(cells=["B1", "B2", "B3", "B4"], evidence="e2"),  # 多1列，需截断
            ],
        ),
    )

    # 2. 流程图带有悬空无效边（需自动过滤）
    flawed_flow = FlowVisual(
        title="测试流程",
        data=FlowData(
            nodes=[
                FlowNode(node_id="n1", title="开始"),
                FlowNode(node_id="n2", title="进行中"),
                FlowNode(node_id="n3", title="结束"),
            ],
            edges=[
                FlowEdge(**{"from": "n1", "to": "n2", "label": "正常边"}),
                FlowEdge(**{"from": "n2", "to": "ghost_node", "label": "孤立边"}),  # 无效边
            ],
        ),
    )

    # 3. 树状图含有未知父节点（需置为 None 提升为根）
    flawed_tree = TreeVisual(
        title="测试树",
        data=TreeData(
            nodes=[
                TreeNode(node_id="r1", title="根节点", parent_id=None),
                TreeNode(node_id="child1", title="子节点1", parent_id="non_exist_root"),  # 父节点不存在
                TreeNode(node_id="child2", title="子节点2", parent_id="r1"),
            ]
        ),
    )

    ir = VisualDocumentIR(
        master=MasterVisual(
            headline="测试主看板",
            widgets=[
                CardsVisual(
                    kind="cards",
                    title="核心议题",
                    data=CardsData(
                        items=[
                            CardItem(
                                tag="01",
                                title="议题一",
                                summary="重点摘要",
                                key_points=["要点1"],
                            )
                        ]
                    ),
                )
            ],
        ),
        sections=[
            SectionVisual(section_id="s1", visual=flawed_table, confidence=0.9),
            SectionVisual(section_id="s2", visual=flawed_flow, confidence=0.85),
        ],
    )

    validated_ir = validate_document(ir, parsed)

    # 验证表格行单元格补齐与截断
    validated_table = validated_ir.sections[0].visual
    assert len(validated_table.data.rows[0].cells) == 3
    assert validated_table.data.rows[0].cells == ["A1", "", ""]
    assert len(validated_table.data.rows[1].cells) == 3
    assert validated_table.data.rows[1].cells == ["B1", "B2", "B3"]

    # 验证流程图过滤掉悬空边
    validated_flow = validated_ir.sections[1].visual
    assert len(validated_flow.data.edges) == 1
    assert validated_flow.data.edges[0].target == "n2"


def test_validator_density_cap():
    """验证全篇图表软上限 (MAX_SECTION_VISUALS=8) 自动截断且保持时序。"""
    # 构造 10 个章节
    md = "\n".join([f"## 章节{i}\n这是章节{i}的内容。" for i in range(1, 11)])
    parsed = parse_minutes(md)

    # 构造 10 个有效图表（超过上限 8）
    sections = []
    for i in range(1, 11):
        tb = TableVisual(
            title=f"表格{i}",
            data=TableData(
                columns=["C1", "C2"],
                rows=[TableRow(cells=["1", "2"]), TableRow(cells=["3", "4"])],
            ),
        )
        sections.append(
            SectionVisual(
                section_id=f"s{i}",
                visual=tb,
                confidence=0.5 + (i * 0.04),  # 置信度各异
            )
        )

    ir = VisualDocumentIR(
        master=MasterVisual(
            headline="全景看板",
            widgets=[CardsVisual(title="议题", data=CardsData(items=[CardItem(title="T")]))],
        ),
        sections=sections,
    )

    validated_ir = validate_document(ir, parsed)
    # 应被严格限制为 8 张
    assert len(validated_ir.sections) == 8
    # 并且应产生警告日志记录被淘汰的章节
    assert any("visual density limit dropped" in w for w in validated_ir.warnings)


def test_llm_json_extractor():
    """验证 JSON 提取工具函数的鲁棒性。"""
    raw_md_json = """Here is your output:
```json
{
  "key": "value",
  "num": 42
}
```
Thank you!"""
    res = extract_json_object(raw_md_json)
    assert res == {"key": "value", "num": 42}

    # 验证直接 JSON
    res2 = extract_json_object('{"hello": "world"}')
    assert res2 == {"hello": "world"}
