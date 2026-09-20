"""
端到端驱动脚本：经真实 HTTP + WebSocket 跑一个完整研究任务，
打印子智能体路由、工具调用序列与最终结果。

用法（在 deepsearch-agents 目录下，先启动后端 uvicorn）：
  python scripts/e2e_run.py <标签> "<问题>"

示例：
  python scripts/e2e_run.py 常识直答 "用一句话解释什么是光合作用"
  python scripts/e2e_run.py 网络检索 "检索 DeepSeek V4 的最新消息，带来源链接"
  python scripts/e2e_run.py 数据分析 "用 Python 生成正态分布随机数并画直方图保存为 PNG"
  python scripts/e2e_run.py 学术文献 "检索 4D Gaussian Splatting 的代表性论文"

每次运行都会生成全新的 thread（e2e-xxxx），避免 SQLite 历史记忆干扰回归结果。
"""

import asyncio
import json
import sys
import time
import uuid

import httpx
import websockets

API = "http://localhost:8001"


async def run_case(query: str, tag: str, timeout: int = 300):
    tid = f"e2e-{uuid.uuid4().hex[:10]}"  # 全新 thread，避免 SQLite 历史记忆干扰
    print(f"\n########## {tag} | thread={tid} ##########")
    print("QUERY:", query)
    final, tool_seq = None, []

    async with websockets.connect(f"ws://localhost:8001/ws/{tid}") as ws:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(f"{API}/api/task", json={"query": query, "thread_id": tid})
            print("POST /api/task ->", r.json())

        start = time.time()
        while time.time() - start < timeout:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            except asyncio.TimeoutError:
                print("!! WS recv 超时")
                break
            evt = json.loads(raw)
            et = evt.get("event")
            data = evt.get("data", {})
            msg = evt.get("message", "")

            if et == "assistant_call":
                print(f"  >> 路由到助手: {data.get('assistant_name')}")
            elif et == "tool_start":
                tn = data.get("tool_name")
                tool_seq.append(tn)
                print(f"  >> 工具: {tn} | {json.dumps(data.get('args', {}), ensure_ascii=False)[:160]}")
            elif et == "error":
                print("  >> ERROR:", msg)
                break
            elif et == "task_result":
                final = data.get("result") or msg
                break

    elapsed = time.time() - start
    print(f"--- 总耗时: {elapsed:.1f}s | 工具调用序列: {tool_seq}")
    if final:
        print("--- 最终结果（前 1500 字）---")
        print(final[:1500])
    else:
        print("!! 未收到 task_result")
    return final


if __name__ == "__main__":
    tag = sys.argv[1] if len(sys.argv) > 1 else "学术路由"
    query = (
        sys.argv[2]
        if len(sys.argv) > 2
        else "检索 3D Gaussian Splatting 动态场景重建方向的代表性论文，按年份和引用梳理发展脉络"
    )
    asyncio.run(run_case(query, tag))
