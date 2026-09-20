"""
SearXNG 自建元搜索引擎检索模块（双 transport 自适应）

SearXNG 在本地 WSL2 的 Docker 中运行，聚合 Google / Bing / DuckDuckGo /
Wikipedia 等 70+ 搜索引擎并去重，通过原生 JSON API 返回，无 Key、无配额。

Windows 后端访问 WSL 中容器有两种通道，本模块按可用性自动选择：
1. http：直接访问 http://localhost:8888，标准方式、最快；
   但当代理软件开启 TUN 模式（虚拟网卡劫持 WSL 网段路由）时，Windows 无法
   连入 WSL，localhost 与 WSL IP 均不通。
2. wsl ：通过 `wsl -d <distro> -- bash <wsl_query.sh>` 让查询在 WSL 内部经
   127.0.0.1 执行，从命令行标准输出取回 JSON，绕过被劫持的 Windows->WSL 路由。

可由环境变量 SEARXNG_TRANSPORT 指定 auto/http/wsl，默认 auto（记忆上次成功通道）。
返回结构与 ddg_search.duckduckgo_search 对齐（query / results / engine）。
"""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

import requests

HEADERS = {"User-Agent": "deepresearch-agent/1.0 (searxng client)"}
DEFAULT_BASE_URL = "http://localhost:8888"
DEFAULT_DISTRO = "Ubuntu-20.04"
# wsl_query.sh 位于项目根的 searxng/ 目录（本文件在 deepsearch-agents/app/tools/ 下）
DEFAULT_WSL_SCRIPT = Path(__file__).resolve().parents[3] / "searxng" / "wsl_query.sh"

# 记忆上次成功的 transport 及其时间，避免 auto 模式每次都先在不可用通道上等待超时
_last_good: dict[str, Optional[float]] = {"transport": None, "ts": 0.0}
_GOOD_TTL = 120.0  # 秒：超过后重新探测，以应对网络环境变化


def get_searxng_base_url() -> str:
    return os.getenv("SEARXNG_URL", DEFAULT_BASE_URL).rstrip("/")


def _to_wsl_path(path: Path) -> str:
    """Windows 路径转 WSL 内路径，如 D:\\dir\\f.sh -> /mnt/d/dir/f.sh"""
    p = str(Path(path).resolve())
    if len(p) > 1 and p[1] == ":":
        return "/mnt/" + p[0].lower() + p[2:].replace("\\", "/")
    return p.replace("\\", "/")


def _normalize(data: dict, query: str, transport: str) -> dict:
    """把 SearXNG 原始 JSON 归一化为与 ddg 对齐的结构"""
    results = []
    for item in data.get("results", []):
        results.append(
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "content": item.get("content", ""),
            }
        )
    return {
        "query": query,
        "results": results,
        "engine": "searxng",
        "transport": transport,
        "engines_used": sorted(
            {eng for item in data.get("results", []) for eng in item.get("engines", [])}
        ),
        "unresponsive_engines": [
            e[0] if isinstance(e, list) else e
            for e in data.get("unresponsive_engines", [])
        ],
    }


def _search_http(
    query: str,
    max_results: int,
    base_url: str,
    language: str,
    timeout: float,
) -> dict:
    """经 HTTP 直接访问 SearXNG JSON API"""
    params = {
        "q": query,
        "format": "json",
        "categories": "general",
        "pageno": 1,
        "language": language,
        "safesearch": 1,
    }
    resp = requests.get(
        base_url + "/search", params=params, headers=HEADERS, timeout=timeout
    )
    resp.raise_for_status()
    try:
        data = resp.json()
    except ValueError as e:
        raise ValueError("SearXNG 未返回 JSON（确认 settings.yml 已开启 json 格式）") from e
    return _normalize(data, query, "http")


def _search_via_wsl(
    query: str,
    encoded: str,
    max_results: int,
    distro: str,
    script: Path,
    timeout: float,
) -> dict:
    """经 wsl 命令通道在 WSL 内部访问 SearXNG（绕过 Windows->WSL 路由劫持）"""
    if not shutil.which("wsl"):
        raise RuntimeError("未找到 wsl 命令，无法使用 WSL 通道")
    if not Path(script).exists():
        raise FileNotFoundError(f"WSL 查询脚本不存在: {script}")

    wsl_script = _to_wsl_path(Path(script))
    # 每次先消除可能的 CRLF，再执行脚本；encoded 为纯 ASCII 百分号编码，无注入风险
    inner = f'sed -i "s/\\r$//" "{wsl_script}" 2>/dev/null; bash "{wsl_script}" "{encoded}"'
    proc = subprocess.run(
        ["wsl", "-d", distro, "--", "bash", "-c", inner],
        capture_output=True,
        timeout=timeout,
    )
    stdout = proc.stdout.decode("utf-8", errors="replace").strip()
    if not stdout:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()[:160]
        raise RuntimeError(f"WSL 通道无输出(returncode={proc.returncode}) {stderr}")

    # 仅截取首个 { 到末尾 } 之间的 JSON，防御性剔除任何混入的非 JSON 文本
    l, r = stdout.find("{"), stdout.rfind("}")
    if l == -1 or r == -1 or r <= l:
        raise RuntimeError("WSL 通道返回内容不是有效 JSON")
    data = json.loads(stdout[l : r + 1])
    return _normalize(data, query, "wsl")


def searxng_search(
    query: str,
    max_results: int = 5,
    base_url: Optional[str] = None,
    language: str = "zh-CN",
    timeout: float = 90.0,
    transport: Optional[str] = None,
    distro: Optional[str] = None,
    wsl_script: Optional[Path] = None,
) -> dict:
    """
    通过本地 SearXNG 检索网页，自动在 http / wsl 两个通道间故障转移。

    :param query: 搜索关键词或自然语言问题
    :param max_results: 返回的最大结果数
    :param timeout: 总超时（秒）。wsl 通道内的 wsl_query.sh 自带 15 次轮询重试，
        用于吸收 WSL/容器冷启动（开机或 WSL 空闲关停后首次查询约 10-40s），
        因此这里给到 90s，保证冷启动也能在一次调用内取到结果。
    :param transport: auto/http/wsl，默认取环境变量 SEARXNG_TRANSPORT 或 auto
    :return: 归一化结果 dict；所有通道均失败时抛出异常
    """
    from urllib.parse import quote

    base_url = (base_url or get_searxng_base_url()).rstrip("/")
    distro = distro or os.getenv("WSL_DISTRO", DEFAULT_DISTRO)
    wsl_script = Path(wsl_script or os.getenv("SEARXNG_WSL_SCRIPT", DEFAULT_WSL_SCRIPT))
    transport = (transport or os.getenv("SEARXNG_TRANSPORT", "auto")).lower()
    encoded = quote(query, safe="")

    if transport == "http":
        order = ["http"]
    elif transport == "wsl":
        order = ["wsl"]
    else:
        # auto：若上次成功通道仍在 TTL 内，优先使用
        if (
            _last_good["transport"]
            and time.time() - _last_good["ts"] < _GOOD_TTL
        ):
            order = [_last_good["transport"], "wsl" if _last_good["transport"] == "http" else "http"]
        else:
            order = ["http", "wsl"]

    errors = []
    for name in order:
        try:
            if name == "http":
                # http 通道探活用较短超时，避免在被劫持环境长时间等待
                result = _search_http(
                    query, max_results, base_url, language,
                    timeout=min(timeout, 6.0),
                )
            else:
                result = _search_via_wsl(
                    query, encoded, max_results, distro, wsl_script, timeout
                )
            # SearXNG JSON API 不支持结果条数参数，统一在客户端按 max_results 截断，
            # 使两个通道返回条数与上层约定一致
            result["results"] = result["results"][:max_results]
            if not result["results"]:
                raise RuntimeError(f"{name} 通道返回空结果")
            _last_good["transport"] = name
            _last_good["ts"] = time.time()
            return result
        except Exception as e:  # 单通道失败则尝试下一通道
            errors.append(f"{name}: {type(e).__name__}: {str(e)[:100]}")

    raise RuntimeError("SearXNG 所有通道均失败 -> " + " | ".join(errors))


if __name__ == "__main__":
    from pprint import pprint

    pprint(searxng_search("3D Gaussian Splatting 最新进展", max_results=3))
