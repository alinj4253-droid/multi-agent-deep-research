"""
Python 代码执行工具模块

封装数据分析助手使用的 execute_python_code 工具，
在受控 Python 子进程中执行代码，用于数据统计、数值计算和可视化生成。

设计要点（用于让数据分析助手“一次成型”，避免反复探测工作目录 / 重复生成）：
1. 子进程工作目录固定为当前会话目录，相对路径保存的文件默认落到这里；
2. 每次执行后自动回传【工作目录】与【本次产物清单】（文件名 + 大小），
   模型无需再用 os.listdir / os.path.exists 反复确认文件是否保存成功；
3. 每个任务有工具调用次数硬上限（MAX_CALLS_PER_TASK），超过后强制收敛，
   防止模型陷入“生成—验证—再生成”的不终止循环；
4. 生命周期安全：每次执行使用唯一临时脚本；超时或任务被取消时显式 kill 子进程
   并回收管道；临时脚本在 finally 中删除，任何退出路径都不残留垃圾文件。

注意：这里是“受控子进程执行器”，不是 OS 级安全沙箱——cwd 只决定相对路径的
解析位置，并不阻止代码 open 绝对路径、访问环境变量或网络。真正强制会话路径
边界的是 Agent 自带的文件工具（read/markdown/pdf 走 resolve_session_path）。
"""

import asyncio
import os
import sys
import uuid
from typing import Dict, Optional, Tuple

from langchain_core.tools import tool

from app.api.context import get_session_context, get_thread_context
from app.api.monitor import monitor

# 单个任务内 execute_python_code 的最大调用次数（硬兜底，正常任务 1-4 次即可完成）
MAX_CALLS_PER_TASK = 12

# 子进程执行超时（秒）
EXEC_TIMEOUT_SECONDS = 30.0

# 每个任务的工具调用计数：key 为 thread_id（缺失时退化为会话目录）
_session_call_counts: Dict[str, int] = {}

# 不计入产物清单的临时/缓存文件名前缀与后缀
_IGNORE_PREFIX = ("_exec_",)
_IGNORE_NAMES = {"_temp_exec.py"}
_IGNORE_SUFFIX = (".pyc",)
_IGNORE_DIRS = {"__pycache__"}


def _is_ignored(name: str) -> bool:
    if name in _IGNORE_NAMES or name in _IGNORE_DIRS:
        return True
    if name.endswith(_IGNORE_SUFFIX):
        return True
    if name.startswith(_IGNORE_PREFIX):
        return True
    return False


def _snapshot_dir(session_dir: str) -> Dict[str, Tuple[int, float]]:
    """记录会话目录内各文件的 (大小, 修改时间)，用于执行前后 diff 出产物。"""
    snap: Dict[str, Tuple[int, float]] = {}
    try:
        for name in os.listdir(session_dir):
            if _is_ignored(name):
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


async def _kill_process(process: Optional[asyncio.subprocess.Process]) -> None:
    """终止子进程并回收管道，避免僵尸进程 / 管道资源泄漏（幂等）。"""
    if process is None or process.returncode is not None:
        return
    process.kill()
    try:
        await process.communicate()
    except Exception:
        pass


@tool
async def execute_python_code(
    code: str,
    description: str = "",
) -> str:
    """
    在受控 Python 子进程中执行代码（子进程隔离 + 30 秒超时 + 默认工作目录为当前会话目录），
    用于数据分析、统计计算和可视化生成。

    用相对路径（如 chart.png）保存的图片/数据会默认落到当前会话工作目录，
    执行结果会自动列出本次新生成或修改的文件，无需自行探测。
    常用库已可用：numpy、pandas、matplotlib、scipy、openpyxl、Pillow。

    :param code: 要执行的 Python 代码；画图请使用 matplotlib 的 Agg 后端并 savefig 到相对路径
    :param description: 这段代码要做什么的简要说明
    """
    # 埋点：工具被调用时上报，前端可展示当前正在执行的代码片段
    monitor.report_tool(
        tool_name="Python代码执行工具",
        args={"description": description, "code_length": len(code)},
    )

    # 获取当前会话工作目录，作为子进程默认 cwd
    session_dir = get_session_context()
    if not session_dir:
        return "错误：未设置工作目录，无法执行代码"

    # 任务级调用次数硬上限，防止不终止循环
    session_key = get_thread_context() or session_dir
    used = _session_call_counts.get(session_key, 0) + 1
    _session_call_counts[session_key] = used
    # 简单防护：字典过大时清理最早的一批（按插入顺序），避免长期运行后内存无限增长
    if len(_session_call_counts) > 200:
        for k in list(_session_call_counts.keys())[:100]:
            _session_call_counts.pop(k, None)
    if used > MAX_CALLS_PER_TASK:
        return (
            f"【已达工具调用上限】本任务已执行 {MAX_CALLS_PER_TASK} 次 Python 代码，"
            "这是系统的硬上限。禁止再次调用本工具，也不要重复生成文件。"
            "请立即基于已有的统计结果和已保存的文件，向主智能体汇报最终结论和产物文件名。"
        )

    # 每次调用使用唯一临时脚本，避免任务重叠 / 异常残留 / 并行时互相覆盖
    temp_code_path = os.path.join(session_dir, f"_exec_{uuid.uuid4().hex[:8]}.py")
    before = _snapshot_dir(session_dir)
    with open(temp_code_path, "w", encoding="utf-8") as f:
        f.write(code)

    process: Optional[asyncio.subprocess.Process] = None
    timeout_happened = False
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            temp_code_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=session_dir,  # 默认工作目录为当前会话目录（非 OS 级路径隔离）
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=EXEC_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            # 超时：显式 kill 并回收，再返回超时提示（属于工具级失败，不向上抛取消）
            await _kill_process(process)
            timeout_happened = True
        except asyncio.CancelledError:
            # 任务被取消：必须显式终止 OS 子进程并回收，然后重新抛出取消语义，
            # 不能转成普通错误字符串，否则上层会误以为任务正常完成。
            await _kill_process(process)
            raise

        if timeout_happened:
            return "错误：代码执行超时（超过30秒），已终止执行。请优化代码或减少计算量。"

        stdout_text = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")

        # 执行后快照，diff 出本次新生成 / 修改的文件
        after = _snapshot_dir(session_dir)
        artifacts = []
        for name, meta in after.items():
            if name not in before or before[name] != meta:
                artifacts.append((name, meta[0]))

        # 构造返回结果
        result_parts = [f"【工作目录】{session_dir}（相对路径保存的文件默认在这里）"]
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

    except asyncio.CancelledError:
        # 兜底：若在 spawn 之前/期间被取消，也确保子进程被回收
        await _kill_process(process)
        raise
    except Exception as e:
        return f"代码执行出错：{str(e)}"
    finally:
        # 无论成功 / 超时 / 取消 / 异常，都删除唯一临时脚本，不残留垃圾文件
        try:
            os.remove(temp_code_path)
        except OSError:
            pass


def reset_session_call_count(session_key: Optional[str] = None) -> None:
    """重置任务工具调用计数（新任务或测试时使用）。"""
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
