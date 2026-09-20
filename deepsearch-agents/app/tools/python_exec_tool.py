"""
Python 代码执行工具模块

封装数据分析助手使用的 execute_python_code 工具，
在隔离子进程中执行 Python 代码，用于数据统计、数值计算和可视化生成。

设计要点（用于让数据分析助手“一次成型”，避免反复探测工作目录 / 重复生成）：
1. 子进程工作目录固定为当前会话目录，相对路径保存的文件都会落到这里；
2. 每次执行后自动回传【工作目录】与【本次产物清单】（文件名 + 大小），
   模型无需再用 os.listdir / os.path.exists 反复确认文件是否保存成功；
3. 每个会话有工具调用次数硬上限（MAX_CALLS_PER_SESSION），超过后强制收敛，
   防止模型陷入“生成—验证—再生成”的不终止循环。
"""

import asyncio
import os
import subprocess
import sys
from typing import Dict, Optional, Tuple

from langchain_core.tools import tool

from app.api.context import get_session_context, get_thread_context
from app.api.monitor import monitor

# 单个会话内 execute_python_code 的最大调用次数（硬兜底，正常任务 1-4 次即可完成）
MAX_CALLS_PER_SESSION = 12

# 每个会话的工具调用计数：key 为 thread_id（缺失时退化为会话目录）
_session_call_counts: Dict[str, int] = {}

# 不计入产物清单的临时/缓存文件名前缀与后缀
_IGNORE_NAMES = {"_temp_exec.py"}
_IGNORE_SUFFIX = (".pyc",)
_IGNORE_DIRS = {"__pycache__"}


def _snapshot_dir(session_dir: str) -> Dict[str, Tuple[int, float]]:
    """记录会话目录内各文件的 (大小, 修改时间)，用于执行前后 diff 出产物。"""
    snap: Dict[str, Tuple[int, float]] = {}
    try:
        for name in os.listdir(session_dir):
            if name in _IGNORE_NAMES or name in _IGNORE_DIRS:
                continue
            if name.endswith(_IGNORE_SUFFIX):
                continue
            full = os.path.join(session_dir, name)
            if os.path.isfile(full):
                try:
                    stat = os.stat(full)
                    snap[name] = (stat.st_size, stat.st_mtime)
                except OSError:
                    continue
    except OSError:
        pass
    return snap


def _format_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes / (1024 * 1024):.2f} MB"


@tool
async def execute_python_code(
    code: str,
    description: str = "",
) -> str:
    """
    在隔离沙箱中执行 Python 代码，用于数据分析、统计计算和可视化生成。

    沙箱工作目录固定为当前会话目录：用相对路径（如 chart.png）保存的图片/数据
    都会自动落到该目录，执行结果会自动列出本次新生成或修改的文件，无需自行探测。
    常用库已可用：numpy、pandas、matplotlib、scipy、openpyxl、Pillow。

    :param code: 要执行的 Python 代码；画图请使用 matplotlib 的 Agg 后端并 savefig 到相对路径
    :param description: 这段代码要做什么的简要说明
    """
    # 埋点：工具被调用时上报，前端可展示当前正在执行的代码片段
    monitor.report_tool(
        tool_name="Python代码执行工具",
        args={"description": description, "code_length": len(code)},
    )

    # 获取当前会话工作目录，代码执行会被限定在这个目录内
    session_dir = get_session_context()
    if not session_dir:
        return "错误：未设置工作目录，无法执行代码"

    # 会话级调用次数硬上限，防止不终止循环
    session_key = get_thread_context() or session_dir
    used = _session_call_counts.get(session_key, 0) + 1
    _session_call_counts[session_key] = used
    # 简单防护：字典过大时清理最早的一批（按插入顺序），避免长期运行后内存无限增长
    if len(_session_call_counts) > 200:
        for k in list(_session_call_counts.keys())[:100]:
            _session_call_counts.pop(k, None)
    if used > MAX_CALLS_PER_SESSION:
        return (
            f"【已达工具调用上限】本会话已执行 {MAX_CALLS_PER_SESSION} 次 Python 代码，"
            "这是系统的硬上限。禁止再次调用本工具，也不要重复生成文件。"
            "请立即基于已有的统计结果和已保存的文件，向主智能体汇报最终结论和产物文件名。"
        )

    # 在会话目录下创建临时 Python 文件
    temp_code_path = os.path.join(session_dir, "_temp_exec.py")
    before = _snapshot_dir(session_dir)
    with open(temp_code_path, "w", encoding="utf-8") as f:
        f.write(code)

    try:
        # 使用 subprocess 启动隔离的 Python 子进程
        # 限制：执行目录限定在 session_dir，超时 30 秒
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            temp_code_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=session_dir,  # 工作目录限定在当前会话目录
        )

        try:
            # 等待执行完成，最多 30 秒
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=30.0,
            )
        except asyncio.TimeoutError:
            process.kill()
            # 必须等待子进程真正退出并回收管道，避免残留僵尸进程/资源泄漏
            try:
                await process.communicate()
            except Exception:
                pass
            try:
                os.remove(temp_code_path)
            except OSError:
                pass
            return "错误：代码执行超时（超过30秒），已终止执行。请优化代码或减少计算量。"

        stdout_text = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")

        # 执行后快照，diff 出本次新生成 / 修改的文件
        after = _snapshot_dir(session_dir)
        artifacts = []
        for name, meta in after.items():
            if name not in before or before[name] != meta:
                artifacts.append((name, meta[0]))

        # 清理临时文件
        try:
            os.remove(temp_code_path)
        except OSError:
            pass

        # 构造返回结果
        result_parts = [f"【工作目录】{session_dir}（相对路径保存的文件都在这里）"]
        if description:
            result_parts.append(f"【任务描述】{description}")
        if stdout_text.strip():
            result_parts.append(f"【输出】\n{stdout_text.strip()}")
        if stderr_text.strip():
            result_parts.append(f"【错误】\n{stderr_text.strip()}")

        # 确定性的产物确认，替代模型自行 os.listdir 探测
        if artifacts:
            lines = [f"- {name}（{_format_size(size)}）" for name, size in sorted(artifacts)]
            result_parts.append(
                "【本次产物】以下文件已确认保存到工作目录，可直接引用文件名，无需再验证：\n"
                + "\n".join(lines)
            )
        elif not stderr_text.strip():
            result_parts.append("【本次产物】本次执行未在工作目录生成或修改文件。")

        if len(result_parts) == 1 and not stdout_text.strip():
            result_parts.append("代码执行完成，无输出。")

        return "\n\n".join(result_parts)

    except Exception as e:
        # 清理临时文件
        try:
            os.remove(temp_code_path)
        except OSError:
            pass
        return f"代码执行出错：{str(e)}"


def reset_session_call_count(session_key: Optional[str] = None) -> None:
    """重置会话工具调用计数（测试或新会话时使用）。"""
    if session_key is None:
        _session_call_counts.clear()
    else:
        _session_call_counts.pop(session_key, None)


if __name__ == "__main__":
    # 本地调试入口：直接运行本文件可验证代码执行工具是否正常
    import asyncio

    async def test():
        from app.api.context import set_session_context
        import tempfile

        test_dir = tempfile.mkdtemp()
        set_session_context(test_dir)

        result = await execute_python_code.ainvoke(
            {
                "code": (
                    "import matplotlib\nmatplotlib.use('Agg')\n"
                    "import matplotlib.pyplot as plt\n"
                    "print('2 + 3 =', 2 + 3)\n"
                    "plt.plot([1, 2, 3], [1, 4, 9])\nplt.savefig('demo.png')"
                ),
                "description": "测试基本输出与产物清单",
            }
        )
        print(result)

    asyncio.run(test())
