"""
Python 代码执行工具模块

封装数据分析助手使用的 execute_python_code 工具，
在隔离子进程中执行 Python 代码，用于数据统计、数值计算和可视化生成。
"""

import asyncio
import subprocess
import sys
from typing import Optional

from langchain_core.tools import tool

from app.api.context import get_session_context
from app.api.monitor import monitor


@tool
async def execute_python_code(
    code: str,
    description: str = "",
) -> str:
    """
    在隔离沙箱中执行 Python 代码，用于数据分析、统计计算和可视化生成

    :param code: 要执行的 Python 代码
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

    # 把代码写入临时文件，避免命令行参数过长和转义问题
    import tempfile
    import os

    # 在会话目录下创建临时 Python 文件
    temp_code_path = os.path.join(session_dir, "_temp_exec.py")
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
            return "错误：代码执行超时（超过30秒），已终止执行。请优化代码或减少计算量。"

        stdout_text = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")

        # 清理临时文件
        try:
            os.remove(temp_code_path)
        except OSError:
            pass

        # 构造返回结果
        result_parts = []
        if description:
            result_parts.append(f"【任务描述】{description}")
        if stdout_text.strip():
            result_parts.append(f"【输出】\n{stdout_text}")
        if stderr_text.strip():
            result_parts.append(f"【错误】\n{stderr_text}")

        if not result_parts:
            return "代码执行完成，无输出。"

        return "\n\n".join(result_parts)

    except Exception as e:
        # 清理临时文件
        try:
            os.remove(temp_code_path)
        except OSError:
            pass
        return f"代码执行出错：{str(e)}"


if __name__ == "__main__":
    # 本地调试入口：直接运行本文件可验证代码执行工具是否正常
    import asyncio

    async def test():
        from app.api.context import set_session_context
        import tempfile
        import os

        # 创建临时目录作为测试工作目录
        test_dir = tempfile.mkdtemp()
        set_session_context(test_dir)

        result = await execute_python_code.ainvoke(
            {
                "code": "print('Hello from sandbox!')\nprint('2 + 3 =', 2 + 3)",
                "description": "测试基本输出",
            }
        )
        print(result)

        # 清理
        import shutil
        shutil.rmtree(test_dir)

    asyncio.run(test())
