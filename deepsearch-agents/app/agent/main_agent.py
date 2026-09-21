"""
主智能体组装与异步执行模块

负责把模型、主提示词、文件类工具和三个专家子智能体组装成 DeepAgent，
并提供 run_deep_agent 作为后续 API 层调用的统一入口。

注意：由于使用 AsyncSqliteSaver（异步 SQLite 持久化），
agent 需要在 FastAPI lifespan 中通过 init_main_agent() 异步初始化。
"""

import asyncio
import shutil
from pathlib import Path

import aiosqlite
from deepagents import create_deep_agent
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.agent.llm import get_main_model
from app.agent.prompts import main_agent_content
from app.agent.subagents.academic_literature_agent import build_academic_literature_agent
from app.agent.subagents.data_analysis_agent import build_data_analysis_agent
from app.agent.subagents.network_search_agent import build_network_search_agent
from app.api.context import (
    reset_session_context,
    set_session_context,
    set_thread_context,
)
from app.api.monitor import monitor
from app.tools.academic_search_tool import academic_budget
from app.tools.python_exec_tool import reset_session_call_count
from app.tools.web_search_tool import search_budget
from app.utils.validators import InvalidThreadIdError, validate_thread_id
from app.agent.result import AgentRunResult

# 文件类工具由主智能体直接掌握，负责读取上传附件和生成最终交付文档
from app.tools.markdown_tools import generate_markdown
from app.tools.pdf_tools import convert_md_to_pdf
from app.tools.upload_file_read_tool import read_file_content

# 当前文件位于 app/agent/main_agent.py，parents[1] 即 app 目录
project_root_path = Path(__file__).parents[1].resolve()

# 全局 agent 实例，在 init_main_agent() 中初始化
main_agent = None
_db_conn = None


async def init_main_agent():
    """
    异步初始化主智能体（在 FastAPI lifespan 中调用）

    使用 AsyncSqliteSaver 实现会话状态持久化，
    数据库文件保存在项目根目录 checkpoints.db
    """
    global main_agent, _db_conn

    # 初始化异步 SQLite 连接和 Checkpointer
    db_path = project_root_path / "checkpoints.db"
    _db_conn = await aiosqlite.connect(str(db_path))
    checkpointer = AsyncSqliteSaver(_db_conn)

    # 主智能体是调度中心：
    # 1. tools 只放最终交付相关的文件工具
    # 2. subagents 放网络检索、数据分析、学术文献三类助手
    # 3. checkpointer 通过 thread_id 保存同一会话中的执行上下文（持久化到 SQLite）
    main_agent = create_deep_agent(
        model=get_main_model(),
        system_prompt=main_agent_content["system_prompt"],
        tools=[generate_markdown, convert_md_to_pdf, read_file_content],
        checkpointer=checkpointer,
        subagents=[
            build_data_analysis_agent(),
            build_network_search_agent(),
            build_academic_literature_agent(),
        ],
    )
    print("[MainAgent] 主智能体初始化完成（SQLite 持久化已启用）")


async def close_main_agent():
    """关闭数据库连接（在 FastAPI shutdown 时调用）"""
    global _db_conn
    if _db_conn is not None:
        await _db_conn.close()
        _db_conn = None


async def run_deep_agent(task_query, session_id):
    """
    异步流式执行主智能体

    API 层会为每次任务传入用户问题和 session_id。本函数负责准备会话目录、
    复制上传文件、写入 ContextVar，并在流式执行过程中把关键事件上报给前端。
    :param task_query: 前端提交的原始任务问题
    :param session_id: 当前任务 ID，同时用于 thread_id、输出目录和 WebSocket 定向推送
    """
    if main_agent is None:
        raise RuntimeError("主智能体尚未初始化，请先调用 init_main_agent()")

    # Defense-in-depth：API 层已校验 thread_id，但 Runtime 也可能被 CLI / 测试 /
    # Benchmark / 其他 service 直接调用，因此核心层再校验一次，不假设上游一定安全。
    try:
        validate_thread_id(session_id)
    except InvalidThreadIdError as e:
        raise ValueError(f"非法的 session_id: {e}") from e

    print(f"[MainAgent] 开始执行会话，session_id={session_id}")

    # 每个会话独立使用 output/session_{session_id}，避免不同用户的产物互相覆盖
    session_dir = project_root_path / "output" / f"session_{session_id}"
    session_dir.mkdir(parents=True, exist_ok=True)

    # 前端和工具使用绝对路径；提示词里只给模型相对路径，降低模型误用系统绝对路径的概率
    session_dir_str = str(session_dir).replace("\\", "/")
    relative_session_dir_str = str(session_dir.relative_to(project_root_path)).replace(
        "\\", "/"
    )

    # 上传文件先落在 updated/session_{session_id}，执行前复制到本次 output 工作目录
    updated_dir_path = project_root_path / "updated" / f"session_{session_id}"
    updated_info_prompt = ""
    if updated_dir_path.exists():
        files = [f.name for f in updated_dir_path.iterdir() if f.is_file()]
        if files:
            for filename in files:
                shutil.copy2(updated_dir_path / filename, session_dir / filename)

            updated_info_prompt = (
                "\n    [已上传文件] 已加载到工作目录:\n"
                + "\n".join([f"    - {f}" for f in files])
                + "\n    请优先使用工具（read_file_content）读取并参考这些文件。"
            )

    # ContextVar 让深层工具无需显式传参，也能拿到当前会话目录和 WebSocket thread_id
    session_dir_token = set_session_context(session_dir_str)
    session_id_token = set_thread_context(session_id)

    # 统一按"每任务（per-turn）"重置工具调用预算：
    # 预算目标是防止单次任务内部失控循环，而不是永久限制一整个历史会话。
    # 同一会话的新一轮提问应当拥有全新的预算额度。
    search_budget.reset(session_id)        # 网页检索：每任务 3 次对外上限
    academic_budget.reset(session_id)     # 学术检索：每任务 2 次对外上限
    reset_session_call_count(session_id)  # Python 执行：每任务 12 次上限

    # 前端拿到工作目录后，可以展示本次任务生成的 Markdown/PDF 等产物
    monitor.report_session_dir(session_dir_str)

    # checkpointer 依赖 thread_id 区分会话记忆；同一 session_id 会复用同一条执行上下文
    config = {"configurable": {"thread_id": session_id}}

    # 工作环境指令是运行时动态补充的，约束模型只在当前会话目录读写文件
    path_instruction = f"""
    【工作环境指令】
    工作目录: {relative_session_dir_str}
    {updated_info_prompt}

    规则：
    1. 新生成文件必须保存到工作目录：'{relative_session_dir_str}/filename'
    2. 读取已上传的文件时，请直接将文件名作为 filename 参数传入（read_file_content）读取工具，不要带上任何目录前缀。
    3. 使用相对路径，禁止使用绝对路径
    4. 若存在上传文件，请先分析内容
    """

    final_answer = ""
    try:
        async for chunk in main_agent.astream(
            {"messages": [{"role": "user", "content": task_query + path_instruction}]},
            config=config,
        ):
            for node_name, state in chunk.items():
                if not state or "messages" not in state:
                    continue
                messages = state["messages"]
                if messages and isinstance(messages, list):
                    last_msg = messages[-1]
                    if node_name == "model":
                        if last_msg.tool_calls:
                            for tool_call in last_msg.tool_calls:
                                if tool_call["name"] == "task":
                                    monitor.report_assistant(
                                        tool_call["args"]["subagent_type"],
                                        {
                                            "description": tool_call["args"][
                                                "description"
                                            ]
                                        },
                                    )
                        elif last_msg.content:
                            final_answer = last_msg.content
                            print(
                                f"主智能体执行结果，最终结果：{last_msg.content[:100]}"
                            )
                            monitor.report_task_result(last_msg.content)

        # Final Answer Contract：graph 正常结束但没有产出任何非空最终回答时，
        # 绝不标记为 completed——否则 Runtime 误判成功、WebSocket 不发 task_result，
        # 前端 / E2E 会一直等待。这里 raise 会被下方 except Exception 捕获，
        # 先 emit error 事件再向上抛出，复用既有失败传播与 TaskManager 的 failed 判定。
        if not final_answer or not final_answer.strip():
            raise RuntimeError("Agent finished without a final answer")
        # 汇总本次会话工作区产物（排除临时执行脚本与缓存目录）
        artifacts: list[str] = []
        try:
            for full in sorted(session_dir.iterdir()):
                if full.name.startswith("_exec_") or full.name == "__pycache__":
                    continue
                if full.is_file():
                    artifacts.append(full.name)
        except OSError:
            artifacts = []

        return AgentRunResult(
            session_id=session_id,
            final_answer=final_answer,
            status="completed",
            artifacts=artifacts,
        )
    except asyncio.CancelledError:
        # 取消语义必须继续向上传播，让后台 Task 可被判定为 cancelled
        monitor.report_task_cancelled()
        raise
    except Exception as e:
        # 先推送错误事件，再重新抛出：后台 Task / Benchmark / 测试才能据此判定失败，
        # 而不是把内部失败伪装成正常 return。
        monitor._emit("error", f"执行主智能体时发生异常：{str(e)}")
        raise
    finally:
        reset_session_context(session_dir_token, session_id_token)
