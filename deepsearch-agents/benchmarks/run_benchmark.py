"""
Benchmark 运行入口。

用法：
    python benchmarks/run_benchmark.py            # 仅离线确定性评测（无需网络/LLM，CI 必跑）

真实端到端（HTTP + WebSocket + LLM）评测请改用独立入口：
    python benchmarks/run_e2e_benchmark.py       # 需先启动后端并配置 DEEPSEEK_API_KEY

本脚本中 e2e_llm 类别一律记为 skipped，绝不在这里直接调用 run_deep_agent。
结果写入 benchmarks/results/YYYY-MM-DD.json。所有指标均为真实运行结果，不手填。
"""

import json
import sys
import time
import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks import evaluator

ROOT = Path(__file__).resolve().parent
CASES = ROOT / "cases.json"
RESULTS_DIR = ROOT / "results"

OFFLINE_EVALUATORS = {
    "path_safety": evaluator.eval_path_safety,
    "thread_validator": evaluator.eval_validator,
    "cache_ttl": evaluator.eval_cache_ttl,
    "tool_budget": evaluator.eval_tool_budget,
    "web_fallback": evaluator.eval_web_fallback,
}


def main():
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    started = time.time()
    records = []
    per_category = {}

    for cat in cases["categories"]:
        cid = cat["id"]
        passed = failed = skipped = 0

        if cid == "e2e_llm":
            # 真实端到端在 run_e2e_benchmark.py（HTTP+WebSocket）执行；离线 runner 只标记 skip
            for c in cat["cases"]:
                skipped += 1
                records.append({
                    "category": cid, "id": c["id"], "name": c["name"],
                    "status": "skip", "note": "use run_e2e_benchmark.py for real e2e",
                })
        else:
            for case_id, ok in OFFLINE_EVALUATORS[cid](cat["cases"]):
                status = "pass" if ok else "fail"
                passed += int(ok)
                failed += int(not ok)
                name = next(c["name"] for c in cat["cases"] if c["id"] == case_id)
                records.append({"category": cid, "id": case_id, "name": name, "status": status})

        per_category[cid] = {
            "passed": passed, "failed": failed, "skipped": skipped,
            "total": passed + failed + skipped,
        }

    total = sum(v["total"] for v in per_category.values())
    summary = {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "duration_seconds": round(time.time() - started, 3),
        "mode": "offline",
        "total": total,
        "passed": sum(v["passed"] for v in per_category.values()),
        "failed": sum(v["failed"] for v in per_category.values()),
        "skipped": sum(v["skipped"] for v in per_category.values()),
        "pass_rate": round(
            sum(v["passed"] for v in per_category.values()) / max(1, total) * 100, 1
        ),
        "per_category": per_category,
        "cases": records,
    }

    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / f"{datetime.date.today().isoformat()}.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"=== Benchmark ({summary['mode']}) ===")
    for cid, v in per_category.items():
        print(f"  {cid:18s} pass={v['passed']} fail={v['failed']} skip={v['skipped']}")
    print(f"  total={summary['total']} pass={summary['passed']} "
          f"fail={summary['failed']} skip={summary['skipped']} "
          f"pass_rate={summary['pass_rate']}%")
    print(f"  results -> {out}")
    # 离线用例任一失败即非零退出，便于 CI
    offline_failed = sum(
        v["failed"] for c, v in per_category.items() if c != "e2e_llm"
    )
    sys.exit(1 if offline_failed else 0)



if __name__ == "__main__":
    main()
