"""
tasks/minutes/memory/prompts.py
纯标准提示词规范 (无废话版本)
"""
from __future__ import annotations

MEMORY_EXTRACTOR_SYSTEM_PROMPT = """你是一个严谨的会议事实提取引擎。请阅读下方的【本场会议纪要】，严格按照要求提取事项，直接输出纯 JSON，严禁输出任何分析过程、解释或 markdown 标记。"""


def build_memory_extractor_user_prompt(minutes_md: str, active_open_items: list[dict]) -> str:
    """构建记忆提取用户提示词，输入本场纪要正文与历史未闭环待办参考清单"""
    items_text = (
        "\n".join(f"- {item.get('memory', '')}" for item in active_open_items[:15])
        if active_open_items
        else "无"
    )
    return f"""==============================
【本场会议纪要】
{minutes_md}
==============================

【参考：历史未完成事项（供核对是否有办结进展）】
{items_text}
==============================

【提取规则】
一、open（本场新提出的待办事项与定案决议）：
1. 待办语法必须为：“[责任人] 负责在 [deadline] 前完成 [具体交付物]”；
2. 决议语法必须为：“针对 [场景]，会议定案采用 [具体方案]”；
3. 严禁提取：空泛口号（如“加强沟通”）、探讨建议（如“建议考虑”）、过程流水账（如“讨论了某议题”）；
4. 消除代词：严禁出现代词，必须补全具体业务名词全称；
5. 上限硬顶 15 条，按优先级保留。

二、close（本场明确宣告已闭环的事项）：
1. 语法必须为：“[主体] 已完成 [事项]，当前状态为 [已上线 / 达标 / 风险解除]”；
2. 必须具备已完成的终态标志词，正在推进中的事情严禁归入 close；
3. 严禁自提自结（本场新提的事项不能出现在 close 中）；
4. 若本场会议无明确办结事项，close 必须返回 []，严禁编造。

【输出 JSON 契约格式】
{{
  "open": [
    {{
      "content": "主谓宾齐全的陈述句",
      "owner": "责任人姓名或null",
      "deadline": "截止时限或null"
    }}
  ],
  "close": [
    {{
      "content": "明确宣告已完成的陈述句"
    }}
  ]
}}"""
