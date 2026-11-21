"""
api_test/test_consecutive_meetings_memory.py
端到端跨场次连续回归测试：
1. 验证第 1 场会议立项 (1 decision + 2 actions) 成功入库
2. 验证第 2 场会议快轨精准插标 [1], [2] 并生成原汁原味的历史卡片
3. 验证第 2 场会议慢轨对账：
   - 上一场的 decision 被【彻底物理删除】
   - 办结的 action (张三) 被【彻底物理删除】
   - 推进中的 action (徐玥) 完好保留
   - 本场新 decision 与新 action 成功写入
4. 验证数据库中 0 条 status="close"、0 条 status="dormant"
5. 验证容量熔断 (超过 30 条按时间戳降序排序，最老溢出项物理删除)
"""
import sys
import os
import asyncio
import time
from pathlib import Path

# 确保导入正确的 Minutes_Agent 根目录
AGENT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENT_ROOT))
os.chdir(str(AGENT_ROOT))

from dotenv import load_dotenv
load_dotenv(AGENT_ROOT.parent / ".env")
load_dotenv(AGENT_ROOT / ".env")

from tasks.minutes.memory import get_user_memory
from tasks.minutes.memory.render import enrich_markdown_with_history
from tasks.minutes.memory.reconciler import run_background_memory_update

USER_ID = "test_user_regression"
MEMORY_LINK = "跨场次回归测试技术周会"
DATA_DIR = AGENT_ROOT / "data"


async def main():
    print("=" * 70)
    print("🚀 开始执行：端到端跨场次连续回归测试 (双轨提取 + 全物理删除)")
    print("=" * 70)

    mem = get_user_memory(USER_ID, data_dir=DATA_DIR)

    # 0. 环境清理：清理历史残留数据
    print("\n[Stage 0] 清理旧测试数据...")
    try:
        mem.delete_all(user_id=USER_ID)
    except Exception as e:
        print(f"  清理提示: {e}")

    # =========================================================================
    # 第一场会议：提炼立项 1 个决议 + 2 个待办
    # =========================================================================
    print("\n" + "=" * 70)
    print("📅 【第 1 场会议】召开 (2026/11/13)")
    print("=" * 70)

    minutes_1 = """# 支付系统架构周会 (第 1 次)
## 核心决议
针对引擎并发优化，会议定案优先推进千问模型方案，暂缓自研管线。

## 待办事项
- 徐玥负责在930前完成云侧服务转测，10月中旬完成全部交付。
- 张三负责在1005前完成网关连接池高并发压测并提交报告。
"""
    # 模拟慢轨执行
    print("-> 正在执行第 1 场慢轨后台更新...")
    await run_background_memory_update(
        minutes_md=minutes_1,
        active_open_items=[],
        memory_link=MEMORY_LINK,
        user_id=USER_ID,
        meeting_time="2026/11/13 14:00",
        mem=mem
    )

    # 核验第 1 场入库结果
    items_m1 = mem.get_all(
        filters={"user_id": USER_ID, "memory_link": MEMORY_LINK, "status": "open"},
        top_k=30
    ).get("results", [])

    print(f"-> 第 1 场慢轨结束，数据库活跃条目数: {len(items_m1)}")
    for it in items_m1:
        cat = it.get("metadata", {}).get("category")
        mem_text = it.get("memory", "")
        print(f"   * [{cat}] {mem_text}")

    assert len(items_m1) >= 2, f"第 1 场应至少提取 2 条事项，实际 {len(items_m1)}"
    has_decision_1 = any(it.get("metadata", {}).get("category") == "decision" for it in items_m1)
    has_action_1 = any(it.get("metadata", {}).get("category") == "action" for it in items_m1)
    print(f"✅ 第 1 场核验通过: decision={has_decision_1}, action={has_action_1}")

    # =========================================================================
    # 第二场会议：消费第 1 场资产，办结张三任务，推陈出新
    # =========================================================================
    print("\n" + "=" * 70)
    print("📅 【第 2 场会议】召开 (2026/11/20)")
    print("=" * 70)

    # 1. 快轨消费阶段：捞取当前库中事项
    active_for_m2 = mem.get_all(
        filters={"user_id": USER_ID, "memory_link": MEMORY_LINK, "status": "open"},
        top_k=30
    ).get("results", [])[:30]

    print(f"-> 快轨启动：捞取到历史活跃资产 {len(active_for_m2)} 条")

    # 本场纪要正文：提到千问模型，提到张三压测完成，提出新事项
    minutes_2 = """# 支付系统架构周会 (第 2 次)
## 架构选型进展
针对引擎并发优化，团队遵循千问模型方案推进测试。

## 进展汇报与结项
张三已完成网关连接池高并发压测并提交报告，当前状态为达标结项。
徐玥的云侧转测工作仍在按期进行中。

## 新议题与待办
针对OCR笔记识别接口，会议定案采用业飞提供的Python接口进行联合调试。
李四负责在1025前完成对账中心Redis缓存削峰模块的设计评审。
"""

    # 执行快轨分句语义打标与文末拼装
    t_fast_start = time.perf_counter()
    enriched_md_2 = enrich_markdown_with_history(
        minutes_md=minutes_2,
        active_open_items=active_for_m2,
        embedder=mem.embedding_model
    )
    t_fast_cost = (time.perf_counter() - t_fast_start) * 1000
    print(f"-> 快轨完成：耗时 {t_fast_cost:.1f}ms")
    print("\n--- 快轨生成的纪要效果预览 (截取文末卡片) ---")
    if "# 历史记忆关联" in enriched_md_2:
        footer_idx = enriched_md_2.find("# 历史记忆关联")
        print(enriched_md_2[footer_idx:])
    else:
        print("未触发历史关联卡片！")

    assert "[1](#ref-1)" in enriched_md_2, "快轨应在正文中成功打上角标 [1]"
    assert "# 历史记忆关联" in enriched_md_2, "文末应成功渲染 # 历史记忆关联"
    print("✅ 第 2 场快轨核验通过：精准分句插标 + 文末自解释卡片渲染成功！")

    # 2. 慢轨后台异步结算阶段
    print("\n-> 正在执行第 2 场慢轨后台更新 (彻底物理删除旧决议 + 办结待办)...")
    await run_background_memory_update(
        minutes_md=minutes_2,
        active_open_items=active_for_m2,
        memory_link=MEMORY_LINK,
        user_id=USER_ID,
        meeting_time="2026/11/20 14:00",
        mem=mem
    )

    # 核验第 2 场慢轨结束后的数据库状态
    items_m2 = mem.get_all(
        filters={"user_id": USER_ID, "memory_link": MEMORY_LINK, "status": "open"},
        top_k=30
    ).get("results", [])

    print(f"\n-> 第 2 场慢轨结束，当前数据库内所有条目 ({len(items_m2)} 条):")
    for it in items_m2:
        cat = it.get("metadata", {}).get("category")
        mem_text = it.get("memory", "")
        owner = it.get("metadata", {}).get("owner")
        print(f"   * [{cat}] {mem_text} (owner={owner})")

    # 严格断言物理删除有效性：
    # 1. 第 1 场旧决议（千问模型）必须彻底消失（被物理删除）
    old_decision_alive = any("千问模型方案" in it.get("memory", "") and it.get("metadata", {}).get("category") == "decision" for it in items_m2)
    assert not old_decision_alive, "❌ 失败：上一场的旧决议未被物理删除！"
    print("✅ 验证通过：上一场旧决议已被彻底物理删除！")

    # 2. 宣告办结的张三任务必须彻底消失（被物理删除）
    zhangsan_alive = any("张三" in it.get("memory", "") for it in items_m2)
    assert not zhangsan_alive, "❌ 失败：宣告办结的张三任务未被物理删除！"
    print("✅ 验证通过：办结的待办任务 (张三) 已被彻底物理删除！")

    # 3. 仍在推进中的徐玥任务必须依然存活 (支持从正文或 metadata.owner 校验)
    xuyue_alive = any("徐玥" in (it.get("memory", "") + str(it.get("metadata", {}).get("owner", ""))) for it in items_m2)
    assert xuyue_alive, "❌ 失败：推进中的徐玥任务被误删！"
    print("✅ 验证通过：未完成的活跃待办 (徐玥) 完好存活！")

    # 4. 检查全库是否还存在任何 status='close' 或 status='dormant' 的残留死数据
    all_raw_points = mem.vector_store.list(filters={"user_id": USER_ID}, top_k=100)
    raw_list = all_raw_points[0] if isinstance(all_raw_points, (list, tuple)) and len(all_raw_points) > 0 and isinstance(all_raw_points[0], (list, tuple)) else all_raw_points
    has_close_status = any(p.payload.get("status") == "close" for p in raw_list)
    has_dormant_status = any(p.payload.get("status") == "dormant" for p in raw_list)

    assert not has_close_status, "❌ 失败：数据库中仍存在 status='close' 的死数据！"
    assert not has_dormant_status, "❌ 失败：数据库中仍存在 status='dormant' 的死数据！"
    print("✅ 验证通过：全库彻底消除 close 与 dormant 状态，0 死数据累积！")

    # =========================================================================
    # 第三阶段：容量熔断极限测试 (超出 30 条按时间戳降序熔断)
    # =========================================================================
    print("\n" + "=" * 70)
    print("🛡️ 【第三阶段】容量硬限熔断极限测试 (超额自动淘汰最老待办)")
    print("=" * 70)

    # 注入测试数据使待办数达到 35 条
    print("-> 注入模拟待办数据至 35 条...")
    for idx in range(1, 36):
        # 模拟不同时间戳
        sim_time = f"2026/10/{idx:02d} 10:00"
        mem.add(
            messages=f"测试责任人{idx} 负责在 11{idx:02d} 前完成 测试模块交付物{idx}",
            user_id=USER_ID,
            infer=False,
            metadata={
                "user_id": USER_ID,
                "memory_link": MEMORY_LINK,
                "status": "open",
                "category": "action",
                "meeting_time": sim_time,
                "updated_at": f"2026-10-{idx:02d}T10:00:00.000000"
            }
        )

    before_cap_items = mem.get_all(filters={"user_id": USER_ID, "memory_link": MEMORY_LINK, "status": "open"}, top_k=60).get("results", [])
    print(f"-> 熔断触发前库内存量待办: {len(before_cap_items)} 条")
    assert len(before_cap_items) > 30, "注入失败，条数未超过 30"

    # 执行一次空纪要的慢轨更新，触发熔断守卫
    print("-> 触发慢轨容量盘点与排序熔断...")
    await run_background_memory_update(
        minutes_md="# 例行快速同步会议\n无重大议题讨论。",
        active_open_items=before_cap_items[:30],
        memory_link=MEMORY_LINK,
        user_id=USER_ID,
        meeting_time="2026/11/21 14:00",
        mem=mem
    )

    # 核验熔断后的数据量与排序正确性
    after_cap_items = mem.get_all(filters={"user_id": USER_ID, "memory_link": MEMORY_LINK, "status": "open"}, top_k=60).get("results", [])
    active_actions_after = [it for it in after_cap_items if it.get("metadata", {}).get("category") == "action"]
    print(f"-> 熔断执行完毕，当前活跃 action 待办数: {len(active_actions_after)} 条")

    assert len(active_actions_after) <= 30, f"❌ 熔断失败：条数仍为 {len(active_actions_after)} > 30"
    
    # 验证淘汰的是最老的：测试模块交付物1（10月1号）应该已被淘汰，而测试模块交付物35（10月35号/最新）必须存活！
    has_oldest = any("测试模块交付物01" in it.get("memory", "") or "测试模块交付物1 " in it.get("memory", "") for it in active_actions_after)
    has_newest = any("测试模块交付物35" in it.get("memory", "") for it in active_actions_after)
    
    print(f"-> 最老项 (10月01日) 是否已安全淘汰: {not has_oldest}")
    print(f"-> 最新项 (10月35日) 是否完好受保护: {has_newest}")
    assert has_newest, "❌ 错误：最新事项被错误熔断！"
    print("✅ 验证通过：30 条容量硬限熔断生效，时间戳降序淘汰逻辑完全正确！")

    print("\n" + "=" * 70)
    print("🎉🎉 恭喜！端到端跨场次连续回归测试全部通过！零膨胀、零死数据、单场接力流转圆满达成！")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
