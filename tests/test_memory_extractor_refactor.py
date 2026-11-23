"""
tests/test_memory_extractor_refactor.py
单元测试：验证重构后的 _sanitize_extracted_data 抽屉式清洗、向下兼容性、人名过滤与配额熔断
"""
from __future__ import annotations

import sys
from pathlib import Path
import pytest

AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from tasks.minutes.memory.extractor import _sanitize_extracted_data


def test_sanitize_isolated_schema_success():
    """验证新版顶级三抽屉 (decisions, actions, close) 精准归类与字段约束"""
    input_data = {
        "decisions": [
            {"content": "架构定案：跨机房容灾统一采用双活架构方案。"}
        ],
        "actions": [
            {"content": "完成网关连接池配置调优", "owner": "李雷", "deadline": "11月20日前"},
            {"content": "输出双活网络延迟评估报告", "owner": "王五", "deadline": "12月5日前"}
        ],
        "close": [
            {"content": "李雷网关调优工作正式结项"}
        ]
    }

    result = _sanitize_extracted_data(input_data)
    open_items = result.get("open", [])
    close_items = result.get("close", [])

    assert len(open_items) == 3
    assert len(close_items) == 1

    actions = [i for i in open_items if i["category"] == "action"]
    decisions = [i for i in open_items if i["category"] == "decision"]

    assert len(actions) == 2
    assert len(decisions) == 1

    # 验证决议的 owner 与 deadline 必定为 None
    assert decisions[0]["owner"] is None
    assert decisions[0]["deadline"] is None
    assert "跨机房容灾" in decisions[0]["content"]

    # 验证待办的责任人与截止时间
    assert actions[0]["owner"] == "李雷"
    assert actions[0]["deadline"] == "11月20日前"
    assert actions[1]["owner"] == "王五"

    # 验证办结项
    assert close_items[0]["content"] == "李雷网关调优工作正式结项"


def test_sanitize_owner_and_deadline_cleaning():
    """验证无效责任人 ('无', '全员', 'null', 'none') 和无效截止时间的清洗"""
    input_data = {
        "decisions": [],
        "actions": [
            {"content": "任务1", "owner": "无", "deadline": "null"},
            {"content": "任务2", "owner": "全员", "deadline": "待定"},
            {"content": "任务3", "owner": "NULL", "deadline": "none"},
            {"content": "任务4", "owner": "张三", "deadline": "周五下班前"}
        ],
        "close": []
    }

    result = _sanitize_extracted_data(input_data)
    actions = result["open"]

    assert len(actions) == 4
    assert actions[0]["owner"] is None
    assert actions[0]["deadline"] is None
    assert actions[1]["owner"] is None
    assert actions[1]["deadline"] is None
    assert actions[2]["owner"] is None
    assert actions[2]["deadline"] is None
    assert actions[3]["owner"] == "张三"
    assert actions[3]["deadline"] == "周五下班前"


def test_sanitize_quota_capping():
    """验证配额熔断：action 最多 15 条，decision 最多 10 条"""
    input_data = {
        "decisions": [{"content": f"决议_{i}"} for i in range(20)],
        "actions": [{"content": f"任务_{i}", "owner": f"人员_{i}"} for i in range(30)],
        "close": []
    }

    result = _sanitize_extracted_data(input_data)
    open_items = result["open"]

    actions = [i for i in open_items if i["category"] == "action"]
    decisions = [i for i in open_items if i["category"] == "decision"]

    assert len(actions) == 15
    assert len(decisions) == 10
    assert len(open_items) == 25


def test_sanitize_legacy_schema_backwards_compatibility():
    """验证对旧版 open 数组模式的 100% 向下兼容能力"""
    input_data = {
        "open": [
            {"category": "decision", "content": "采用 Redis 缓存削峰方案", "owner": None, "deadline": None},
            {"category": "决议", "content": "采用 gRPC 作为内部通信协议", "owner": "无", "deadline": None},
            {"category": "action", "content": "实施压测任务", "owner": "赵六", "deadline": "下周一"},
            {"category": "action", "content": "未指派人名的事项", "owner": "", "deadline": None}
        ],
        "close": [{"content": "旧待办已完成"}]
    }

    result = _sanitize_extracted_data(input_data)
    open_items = result["open"]

    decisions = [i for i in open_items if i["category"] == "decision"]
    actions = [i for i in open_items if i["category"] == "action"]

    # category='decision', category='决议', 以及 owner 为空的事项都应归入 decision
    assert len(decisions) == 3
    assert len(actions) == 1
    assert actions[0]["owner"] == "赵六"
    assert result["close"][0]["content"] == "旧待办已完成"


def test_sanitize_defensive_string_items():
    """验证大模型若返回纯字符串数组时的防御性容错解析"""
    input_data = {
        "decisions": [
            "统一使用 UTF-8 编码规范",
            {"content": "数据库统一增加逻辑删除字段"}
        ],
        "actions": [],
        "close": ["已全量上线部署"]
    }

    result = _sanitize_extracted_data(input_data)
    decisions = result["open"]

    assert len(decisions) == 2
    assert decisions[0]["content"] == "统一使用 UTF-8 编码规范"
    assert decisions[0]["category"] == "decision"
    assert decisions[1]["content"] == "数据库统一增加逻辑删除字段"
    assert result["close"][0]["content"] == "已全量上线部署"
