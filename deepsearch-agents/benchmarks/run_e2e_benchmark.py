"""
端到端 Benchmark 入口（真实 HTTP + WebSocket + LLM + 检索）。

与离线 benchmark（run_benchmark.py）严格分开：
- run_benchmark.py       离线确定性断言，CI 必跑，不触网、不调 LLM；
- run_e2e_benchmark.py   真实链路质量评测，需要你先启动后端并配置好凭据。

前置：
    1. 启动后端：  python -m uvicorn app.api.server:app --port 8001
       （或 python -m app.api.server）
    2. .env 配好 DEEPSEEK_API_KEY；检索数据源（SearXNG / DuckDuckGo / 学术三源）可用。

用法：
    python benchmarks/run_e2e_benchmark.py
    python benchmarks/run_e2e_benchmark.py --base-url http://localhost:8001 --timeout 240

结果写入 benchmarks/results/e2e-<timestamp>.json，含 git commit、模型、预算配置与
每个用例的成败/延迟/工具调用次数/最终答案/产物数，全部为真实运行结果，不手填。
"""

import argparse
import asyncio
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parents[0]))

from benchmarks.e2e_client import run_case, wait_for_server  # noqa: E402
from benchmarks.schemas import build_report  # noqa: E402

CASES = ROOT / "cases.json"
RESULTS_DIR = ROOT / "results"


def git_commit_sha() -> str:
    """获取当前代码版本的短 SHA（取不到时返回 unknown，不手填）。"""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT.parents[1],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def model_name() -> str:
    """从环境变量读取实际配置的模型名（与 app/agent/llm.py 的 env 对齐）。"""
    return (
        os.getenv("DEEPSEEK_MODEL")
        or os.getenv("OPENAI_MODEL")
        or os.getenv("MODEL_NAME")
        or "default"
    )


def load_e2e_cases() -> list[dict]:
    data = json.loads(CASES.read_text(encoding="utf-8"))
    for cat in data["categories"]:
        if cat["id"] == "e2e_llm":
            return cat["cases"]
    return []


async def main_async(base_url: str, timeout: float) -> int:
    cases = load_e2e_cases()
    if not cases:
        print("cases.json 中没有 e2e_llm 用例。")
        return 2

    print(f"探测后端 {base_url} ...")
    if not await wait_for_server(base_url, timeout=10.0):
        print(
            "无法连接后端。请先启动：\n"
            "  python -m uvicorn app.api.server:app --port 8001\n"
            "并确认 .env 已配置 DEEPSEEK_API_KEY。"
        )
        return 2

    results = []
    for case in cases:
        print(f"[e2e] {case['id']} {case['name']} ...", flush=True)
        r = await run_case(case, base_url=base_url, timeout=timeout)
        print(
            f"      -> {r.status}  latency={r.latency_seconds}s  "
            f"tools={r.tool_calls}(web={r.web_calls},acad={r.academic_calls},"
            f"py={r.python_calls}) artifacts={r.artifact_count}"
        )
        results.append(r)

    report = build_report(
        results,
        mode="e2e",
        git_commit=git_commit_sha(),
        model=model_name(),
        config={
            "base_url": base_url,
            "timeout_seconds": timeout,
            "budget": {"web_per_task": 3, "academic_per_task": 2, "python_per_task": 12},
        },
        generated_at=datetime.datetime.now().isoformat(timespec="seconds"),
    )

    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = RESULTS_DIR / f"e2e-{stamp}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    s = report["summary"]
    print(
        f"=== E2E Benchmark ===\n"
        f"  total={s['total']} pass={s['passed']} fail={s['failed']} "
        f"cancel={s['cancelled']} timeout={s['timeout']} unknown={s['unknown']} "
        f"pass_rate={s['pass_rate']}%\n  git={report['git_commit']} model={report['model']}\n"
        f"  results -> {out}"
    )
    return 0 if s["failed"] == 0 and s["timeout"] == 0 and s["passed"] > 0 else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.getenv("E2E_BASE_URL", "http://localhost:8001"))
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()
    sys.exit(asyncio.run(main_async(args.base_url, args.timeout)))


if __name__ == "__main__":
    main()
