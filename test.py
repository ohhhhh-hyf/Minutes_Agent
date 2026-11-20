"""
Mem0 独立连通性与隔离测试脚本（测试完可随时删除）
"""
import os
from mem0 import Memory

# ============================================================
# 1. 配置参数（根据你当前使用的是 vLLM 还是 DeepSeek 自行切换）
# ============================================================


# 方式 B：使用 DeepSeek 官方公网 API（如果服务器 vLLM 还没启动，用这个快速验证）
DEEPSEEK_CONFIG = {
    "vector_store": {
        "provider": "qdrant",
        "config": {
            "path": "data/test_user/memory/qdrant",
            "on_disk": True,
            "embedding_model_dims": 1024,
        }
    },
    "llm": {
        "provider": "openai",
        "config": {
            "model": "deepseek-chat",
            "api_key": "sk-e4af842f80ca4304859e44b875331d88",
            "openai_base_url": "https://api.deepseek.com/v1",
            "temperature": 0.1
        }
    },
    "embedder": {
        "provider": "openai",
        "config": {
            "model": "BAAI/bge-m3",
            "api_key": "sk-lwshjzqubvprqpznxacrypvfrymxbijaykxkmlytfwlltawy",
            "openai_base_url": "https://api.siliconflow.cn/v1"
        }
    }
}


def run_test():
    print(">>> 1. 正在初始化 Mem0 实例...")
    # 优先选你有可用 key 的配置（这里默认先试 DEEPSEEK_CONFIG，如果是自建 vLLM 就换 VLLM_CONFIG）
    active_config = DEEPSEEK_CONFIG 
    mem = Memory.from_config(active_config)
    print(">>> Mem0 初始化成功！")

    # ============================================================
    # 2. 模拟存入会议记忆（带上系列标识 series 和状态 status）
    # ============================================================
    print("\n>>> 2. 正在写入会议记忆...")
    write_result = mem.add(
        messages="王工负责在下周五前完成向量数据库的性能压测并给出评估报告",
        user_id="user_dev_01",
        metadata={
            "series": "技术架构双周会",
            "category": "action",
            "topic": "向量数据库选型",
            "meeting_time": "2026-11-20 14:00",
            "owner": "王工",
            "status": "open"
        }
    )
    print(f"写入结果：{write_result}")

    # ============================================================
    # 3. 模拟带条件的记忆检索（前置捞取历史待办）
    # ============================================================
    print("\n>>> 3. 正在检索【技术架构双周会】的未闭环待办...")
    search_res = mem.search(
        query="数据库压测和性能相关的任务",
        filters={
            "user_id": "user_dev_01",
            "series": "技术架构双周会",
            "status": "open"
        },
        limit=3
    )

    memories = search_res.get("results", [])
    print(f"检索到 {len(memories)} 条相关记忆：")
    for item in memories:
        meta = item.get("metadata", {})
        print(f" - [ID: {item.get('id')}] 记忆内容: {item.get('memory')}")
        print(f"   来源会议: {meta.get('meeting_time')} | 议题: {meta.get('topic')} | 负责人: {meta.get('owner')} | 状态: {meta.get('status')}")

    # ============================================================
    # 4. 模拟状态闭环更新（比如下一场会议此任务已完成）
    # ============================================================
    if memories:
        target_id = memories[0]["id"]
        print(f"\n>>> 4. 模拟更新记忆状态为 closed (ID: {target_id})...")
        mem.update(
            memory_id=target_id,
            data="王工已于11月25日完成向量数据库性能压测，QPS达到5000+",
        )
        print(">>> 更新完成！")


if __name__ == "__main__":
    run_test()