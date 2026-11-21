"""api_test/minutes_memory_stream.py -- 会议纪要流式直出 + 长期记忆双轨增强测试脚本.

用法:
    1. 确保后台服务已启动 (在 Minutes_Agent 根目录下执行: uvicorn main:app --host 127.0.0.1 --port 8000)
    2. 按需调整 USER_ID、SERIES、TIME 或下方 TRANSCRIPT 会议文本
    3. 运行: python api_test/minutes_memory_stream.py
    4. 效果:
       - 阶段一: 实时打字机流式输出正文 (零延迟推流，此时为纯净基础纪要)
       - 阶段二: 流式完成后，展示最终附带句末溯源打标与文末历史卡片的完整 markdown
       - 阶段三: 核验磁盘落盘文件:
         * 基础纪要: minutes.md
         * 记忆增强纪要: minutes_memory.md
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path
import requests

# ── 服务接口配置 ──
BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
STREAM_URL = f"{BASE_URL}/api/agent/v1/stream"

# ── 租户与记忆链接参数 ──
USER_ID = os.getenv("API_USER_ID", "user_payment_prod")
MEMORY_LINK = "支付系统技术周会"           # 核心阀门：携带 memory_link 触发跨场次记忆追踪
MEETING_TIME = "2026/11/20 14:00"      # 会议时间（用于待办追溯与 Calendar 映射）

# ── 会议转写文本（可直接在此替换测试文本）──
# 示例场景：李工在上一场认领了网关连接池排查，本场汇报已修复结项；赵工认领了新的对账缓存重构
TRANSCRIPT = """
张总 00:00:01
各位同学下午好，我们开始本周的支付系统架构技术周会。今天主要聚焦在支付网关的稳定性治理回顾，以及下一阶段对账系统重构的技术方案评审。首先请李工同步一下上周网关连接池超时问题的排查进展。

李工 00:00:25
好的张总。上周我们跟进的支付网关连接池突发超时与偶发丢单问题，经过全链路抓包和压测分析，已经彻底查明原因，主要是在极限突发流量下连接池最大等待队列配置过小，导致请求被快速拒绝。我们已经重新调优了连接池最大连接数与重试回退机制，并在周三完成了生产环境的全量上线。目前监控显示网关错误率已降为0，极限并发压测表现稳定，上周布置的这项调优优化工作已全部完成并正式结项。

张总 00:01:10
非常好，李工这边动作很迅速，网关层稳定性终于有保障了。那接下来进入第二项议题，关于对账系统的架构重构，赵工这边方案考虑得怎么样了？

赵工 00:01:28
张总，目前的对账系统在月底高并发结算时，数据库写入削峰存在瓶颈。我们计划对对账中心进行架构重写，核心是引入 Redis 缓存削峰方案，配合批量异步写入降低数据库压力。目标是在下周五也就是 11 月 28 日前，完成 Redis 缓存削峰模块的设计评审与基准性能测试，由我来主导负责落地。

张总 00:02:05
很好，对账中心的重构非常关键，直接关系到财务结算时效。赵工按这个节奏推进，11月28日前务必拉齐财务和DBA团队过评审。散会！
""".strip()


def main():
    if not TRANSCRIPT.strip():
        print("[提示] TRANSCRIPT 文本为空，请在脚本顶部填入待测试的会议转写内容。")
        return

    # 严格对齐工程 TaskRequest 契约规范，显式携带 memory_link 字段
    payload = {
        "task": "minutes",
        "texts": {
            "transcript": TRANSCRIPT,
            "keypoints": "",
            "notes": "",
            "vision_info": "",
        },
        "docs": [],
        "memory_link": MEMORY_LINK,  # 顶层传递记忆链接名称
        "extra": {
            "template": "general_minutes",  # 指定通用模板（留空则自动智能分类）
            "time": MEETING_TIME,           # 会议时间
        },
        "lang": "zh",
    }

    req_id = uuid.uuid4().hex
    headers = {
        "X-Request-Id": req_id,
        "X-User-Id": USER_ID,
    }

    session = requests.Session()
    # 本地服务禁用代理，避免系统代理劫持本地 127.0.0.1 导致连接超时
    if "127.0.0.1" in BASE_URL or "localhost" in BASE_URL:
        session.trust_env = False

    print("=" * 80)
    print(f"[发起请求] 接口地址 : {STREAM_URL}")
    print(f"[请求元数据] 用户 ID   : {USER_ID}")
    print(f"[请求元数据] 记忆链接 : {MEMORY_LINK} (触发跨场次长期记忆)")
    print(f"[请求元数据] 会议时间 : {MEETING_TIME}")
    print(f"[请求元数据] 请求 ID   : {req_id}")
    print("=" * 80)
    print("\n>>> 阶段一: 实时打字机流式直出 (纯净基础纪要实时推流，零 TTFT 损耗) >>>\n")

    try:
        resp = session.post(
            STREAM_URL,
            json=payload,
            headers=headers,
            stream=True,
            timeout=300,
        )
    except Exception as exc:
        print(f"\n[错误] 请求服务失败: {exc}")
        print("请确认后台服务已启动 (在 Minutes_Agent 根目录下执行: uvicorn main:app --host 127.0.0.1 --port 8000)")
        return

    if resp.status_code != 200:
        print(f"\n[错误] HTTP 请求失败 {resp.status_code}: {resp.text}")
        return

    final_payload_data = {}
    stream_chunks_count = 0
    start_stream_time = time.perf_counter()

    for line in resp.iter_lines():
        if not line:
            continue
        try:
            line_str = line.decode("utf-8").strip()
            if not line_str:
                continue
            event = json.loads(line_str)
        except Exception:
            continue

        evt_type = event.get("type")

        # 1. 实时 Chunk 流式呈现
        if evt_type == "chunk":
            chunk_text = event.get("text") or ""
            sys.stdout.write(chunk_text)
            sys.stdout.flush()
            stream_chunks_count += 1

        # 2. 推流完成事件：携带记忆增强后的最终 markdown 及产物落盘信息
        elif evt_type == "done":
            final_payload_data = event.get("data") or {}
            cost_time = event.get("monitor", {}).get("cost_time", "")
            print("\n")
            print("=" * 80)
            print(f"[流式阶段完成] 实时推流已结束 (接收 Chunk: {stream_chunks_count} 个, 耗时: {cost_time}s)")
            print("=" * 80)

        elif evt_type == "error":
            print(f"\n[错误事件] {event.get('message')}")
            break

    # 3. 最终重新展示附加记忆后的完整 Markdown
    final_full_markdown = (
        final_payload_data.get("content")
        or final_payload_data.get("text")
        or ""
    )

    print("\n" + "#" * 80)
    print("【阶段二：最终呈现 —— 附加跨场次记忆与溯源打标的完整会议纪要 (minutes_memory.md)】")
    print("#" * 80 + "\n")

    if final_full_markdown:
        print(final_full_markdown)
    else:
        print("[警告] 未能获取到最终纪要内容。")

    print("\n" + "=" * 80)
    print("【阶段三：磁盘双份存储落盘核验】")
    print("=" * 80)

    minutes_file_path = final_payload_data.get("minutes_file")
    memory_file_path = final_payload_data.get("memory_file")

    # 核验 1: 原始基础纪要 (minutes.md)
    if minutes_file_path:
        p_min = Path(minutes_file_path)
        if p_min.is_file():
            size = p_min.stat().st_size
            print(f"  [√] 原始基础纪要落盘成功: {p_min}")
            print(f"      - 文件名: {p_min.name} (大小: {size} 字节, 纯净无记忆标)")
        else:
            print(f"  [!] 原始基础纪要路径未找到: {minutes_file_path}")

    # 核验 2: 记忆增强纪要 (minutes_memory.md)
    if memory_file_path:
        p_mem = Path(memory_file_path)
        if p_mem.is_file():
            size = p_mem.stat().st_size
            print(f"  [√] 记忆增强纪要落盘成功: {p_mem}")
            print(f"      - 文件名: {p_mem.name} (大小: {size} 字节, 包含句末插标与文末历史卡片)")
        else:
            print(f"  [!] 记忆增强纪要路径未找到: {memory_file_path}")
    elif MEMORY_LINK:
        print("  [*] 未生成独立的 memory_file（请检查服务日志中记忆快轨执行状态）")

    print("=" * 80)
    print("[测试脚本运行完毕]\n")


if __name__ == "__main__":
    main()
