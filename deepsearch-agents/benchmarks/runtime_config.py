"""
Benchmark 运行时配置采集（离线可测，不触网）。

目的：让端到端 Benchmark 结果文件记录“这次到底用什么跑的”，保证可复现：
- 真实模型配置（LLM_MAIN_MODEL / LLM_FAST_MODEL），而不是去读不存在的
  DEEPSEEK_MODEL / OPENAI_MODEL / MODEL_NAME 然后写成 "default"；
- git commit、Python 版本、OS、每任务检索 / 执行预算、用例文件哈希。

与应用本身使用同一套 .env 加载方式（app/agent/llm.py 也是 load_dotenv(find_dotenv())），
避免“App 读到了 .env、Benchmark 却只读进程环境变量”的不一致。

安全：本模块**绝不**采集 OPENAI_API_KEY 或任何 *KEY/*TOKEN/*SECRET，
结果 JSON 可安全提交到公开仓库。
"""

from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Mapping, Optional

# 与 app/agent/llm.py 的 _DEFAULT_MODEL 保持一致；未配置时使用同一回退默认值
DEFAULT_MODEL = "deepseek-flash"

BENCHMARKS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = BENCHMARKS_DIR.parent
CASES_PATH = BENCHMARKS_DIR / "cases.json"

# 仅用于在导入工具常量失败时的兜底；正常情况下以代码中的真实常量为准
_FALLBACK_BUDGETS = {"web": 3, "academic": 2, "python": 12}


def load_env() -> None:
    """与 app 一致地加载 .env（find_dotenv 从当前目录向上查找）。"""
    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv(), override=False)


def resolve_models(env: Mapping[str, str]) -> dict[str, str]:
    """从环境映射读取主/快模型；未配置时回退默认模型。"""
    return {
        "main": env.get("LLM_MAIN_MODEL") or DEFAULT_MODEL,
        "fast": env.get("LLM_FAST_MODEL") or DEFAULT_MODEL,
    }


def infer_provider(base_url: str) -> str:
    """从 base_url 主机名粗略推断供应商标识（仅用于记录，不参与鉴权）。"""
    host = (base_url or "").lower()
    if "deepseek" in host:
        return "deepseek"
    if "dashscope" in host or "aliyun" in host:
        return "dashscope"
    if "openai" in host:
        return "openai"
    if "anthropic" in host:
        return "anthropic"
    if not host:
        return "unknown"
    return host.split("//")[-1].split("/")[0].split(":")[0]


def resolve_budgets() -> dict[str, int]:
    """
    直接读取代码中真实生效的每任务预算常量，避免在 Benchmark 里复制一份会漂移的数字。
    导入失败时回退到与当前代码一致的默认值并照常返回（不让评测因导入问题崩）。
    """
    try:
        from app.tools.academic_search_tool import academic_budget
        from app.tools.python_exec_tool import MAX_CALLS_PER_TASK
        from app.tools.web_search_tool import search_budget

        return {
            "web": int(search_budget.max_per_session),
            "academic": int(academic_budget.max_per_session),
            "python": int(MAX_CALLS_PER_TASK),
        }
    except Exception:
        return dict(_FALLBACK_BUDGETS)


def git_commit_sha(short: bool = True) -> str:
    """获取当前代码版本的 git SHA（取不到时返回 unknown，不手填）。"""
    try:
        args = ["git", "rev-parse"] + (["--short", "HEAD"] if short else ["HEAD"])
        out = subprocess.run(
            args,
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def file_sha256(path: Path) -> str:
    """计算用例文件的 sha256（前 16 位即可用于判断用例集是否变化）。"""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]
    except Exception:
        return "unknown"


def collect_metadata(
    *,
    env: Optional[Mapping[str, str]] = None,
    cases_path: Optional[Path] = None,
) -> dict:
    """
    采集一次 Benchmark 运行所需的全部复现元数据。

    :param env: 可注入环境映射（测试用）；默认先 load_dotenv 再读 os.environ。
    :param cases_path: 用例文件路径（测试可指向临时文件）。
    :return: 可直接 json.dumps 的 dict，保证不含任何密钥。
    """
    if env is None:
        load_env()
        env = os.environ

    base_url = env.get("OPENAI_BASE_URL") or ""
    models = resolve_models(env)

    metadata = {
        "llm": {
            "provider": infer_provider(base_url),
            "base_url": base_url,
            "main_model": models["main"],
            "fast_model": models["fast"],
        },
        "models": models,
        "budgets": resolve_budgets(),
        "python_version": platform.python_version(),
        "os": f"{platform.system()} {platform.release()}".strip(),
        "git_commit": git_commit_sha(),
        "cases_sha256": file_sha256(cases_path or CASES_PATH),
    }

    # 双保险：递归确认结果里没有任何密钥值
    _assert_no_secret(metadata, env)
    return metadata


def _assert_no_secret(payload: object, env: Mapping[str, str]) -> None:
    """确保采集结果中不包含任何 *KEY/*TOKEN/*SECRET 环境变量的值。"""
    import json

    blob = json.dumps(payload, ensure_ascii=False, default=str)
    for key, value in env.items():
        if not value:
            continue
        ku = key.upper()
        if ku.endswith("KEY") or "TOKEN" in ku or "SECRET" in ku or "PASSWORD" in ku:
            # 长度过短的占位值（如 "x"）不具识别意义，跳过；真实 key 必须不出现
            if len(value) >= 8 and value in blob:
                raise AssertionError(f"benchmark metadata leaked secret from {key}")
