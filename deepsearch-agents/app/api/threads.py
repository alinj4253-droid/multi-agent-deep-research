"""
历史会话列表模块

从 langgraph 的 SQLite checkpointer 数据库（app/checkpoints.db）聚合历史会话，
供前端侧边栏展示与一键恢复。

设计要点：
1. **只读连接**：绝不干扰主智能体持有的 AsyncSqliteSaver 写连接；只读失败时降级普通连接
2. **时间排序**：checkpoint_id 是 UUID6（时间有序），字符串序即时间序，
   取 MAX(checkpoint_id) 即该会话最近活动时间，无需额外时间列
3. **标题提取**：取该 thread 最早一条 HumanMessage，并剥离运行时注入的「工作环境指令」，
   否则侧边栏会显示一大段路径噪声
4. **不阻塞事件循环**：本模块只提供同步实现，由 API 层放入线程池调用
"""

import re
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# app/api/threads.py -> parents[1] 即 app 目录，与 main_agent.py 的库路径保持一致
APP_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = APP_DIR / "checkpoints.db"

# UUID6 时间戳基准：1582-10-15（格里高利历元）到 1970-01-01 之间的 100ns 间隔数
_UUID6_UNIX_OFFSET = 0x01B21DD213814000

# 运行时由 main_agent 注入到用户消息尾部的工作环境指令标记，标题里必须剥离
_WORKSPACE_MARKER = "【工作环境指令】"

# 自动化测试/诊断脚本产生的会话前缀，默认不在侧边栏展示
_TEST_THREAD_PREFIXES = ("e2e-", "diag-", "test-", "smoke-", "verify-")

# 侧边栏空间有限，标题超长截断；完整内容通过 preview 字段返回
TITLE_MAX_CHARS = 42

# 单个 thread 最多扫描多少条 messages 写入来找首条用户消息
_SCAN_LIMIT = 12


def _checkpoint_time(checkpoint_id: str) -> Optional[datetime]:
    """
    把 UUID6 形式的 checkpoint_id 解析为本地时区时间

    checkpoint_id 不是标准时间列，但 UUID6 的高位就是时间戳，
    因此可以直接还原出该检查点的生成时刻。
    """
    try:
        parsed = uuid.UUID(checkpoint_id)
    except (ValueError, AttributeError, TypeError):
        return None

    if parsed.version != 6:
        return None

    # 直接按 RFC 9562 UUIDv6 位布局还原 60-bit 时间戳（单位 100ns，历元 1582-10-15）。
    # 不能用 stdlib 的 parsed.time：Python 3.14 才支持对 version 6 解码 .time，
    # 3.11/3.12 上会抛 ValueError，导致历史会话接口在干净 CI 环境（Python 3.11）直接失败。
    try:
        timestamp_100ns = (
            (parsed.time_low << 28)
            | (parsed.time_mid << 12)
            | (parsed.time_hi_version & 0x0FFF)
        )
    except (ValueError, AttributeError, TypeError):
        return None

    unix_100ns = timestamp_100ns - _UUID6_UNIX_OFFSET
    if unix_100ns < 0:
        return None

    try:
        # astimezone() 转为本地时区，前端展示更直观
        return datetime.fromtimestamp(unix_100ns / 1e7).astimezone()
    except (OverflowError, OSError, ValueError):
        return None


def strip_workspace_instruction(text: str) -> str:
    """剥离运行时注入的工作环境指令，只保留用户真正输入的问题（纯函数，便于单测）"""
    if not text:
        return ""
    return text.split(_WORKSPACE_MARKER)[0].strip()


def make_title(text: str, max_chars: int = TITLE_MAX_CHARS) -> str:
    """把用户问题压缩成单行侧边栏标题（纯函数，便于单测）"""
    cleaned = strip_workspace_instruction(text)
    # 折叠换行与连续空白，侧边栏只显示一行
    collapsed = re.sub(r"\s+", " ", cleaned).strip()
    if len(collapsed) > max_chars:
        return collapsed[:max_chars].rstrip() + "…"
    return collapsed


def _decode_value(blob: Any) -> Any:
    """用 langgraph 自带序列化器解 writes.value，失败返回 None"""
    if blob is None:
        return None

    # 兼容不同 sqlite 驱动返回 bytes 或 str 的情况
    if isinstance(blob, str):
        blob = blob.encode("utf-8", "surrogateescape")

    try:
        # 延迟导入：仅在真正需要解析时才依赖 langgraph 内部模块
        from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

        return JsonPlusSerializer().loads_typed(("msgpack", blob))
    except Exception:
        return None


def _extract_human_text(obj: Any) -> Optional[str]:
    """从反序列化结果中取出首条人类消息文本"""
    candidates = obj if isinstance(obj, (list, tuple)) else [obj]
    for message in candidates:
        if getattr(message, "type", None) != "human":
            continue
        content = getattr(message, "content", None)
        if isinstance(content, str) and content.strip():
            return content
        # content 也可能是多模态分块列表，拼接其中的文本部分
        if isinstance(content, list):
            parts = [
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            joined = " ".join(p for p in parts if p).strip()
            if joined:
                return joined
    return None


def _open_readonly(db_path: Path) -> sqlite3.Connection:
    """优先以只读模式打开，避免与主智能体的写连接争锁；失败则降级普通连接"""
    try:
        return sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=5.0)
    except sqlite3.Error:
        return sqlite3.connect(str(db_path), timeout=5.0)


def _first_human_message(conn: sqlite3.Connection, thread_id: str) -> Optional[str]:
    """扫描该会话最早的若干条 messages 写入，返回首条用户消息原文"""
    rows = conn.execute(
        "SELECT value FROM writes "
        "WHERE thread_id = ? AND channel = 'messages' AND type = 'msgpack' "
        "ORDER BY checkpoint_id ASC, idx ASC LIMIT ?",
        (thread_id, _SCAN_LIMIT),
    ).fetchall()

    for (blob,) in rows:
        text = _extract_human_text(_decode_value(blob))
        if text:
            return text
    return None


def is_test_thread(thread_id: str) -> bool:
    """判断是否为自动化测试/诊断会话（纯函数，便于单测）"""
    lowered = (thread_id or "").lower()
    return lowered.startswith(_TEST_THREAD_PREFIXES)


def list_threads(
    db_path: Optional[Path] = None,
    limit: int = 30,
    include_test: bool = False,
) -> dict:
    """
    聚合历史会话列表（同步实现，API 层需放入线程池调用）

    :param db_path: checkpoints.db 路径，默认 app/checkpoints.db
    :param limit: 最多返回条数
    :param include_test: 是否包含 e2e-/diag- 等自动化测试会话
    :return: {"threads": [...], "total": int, "returned": int}
    """
    path = Path(db_path) if db_path else DEFAULT_DB_PATH

    # 首次启动或库文件缺失时返回空列表，而不是让接口 500
    if not path.exists():
        return {"threads": [], "total": 0, "returned": 0}

    limit = max(1, min(int(limit), 200))

    conn = _open_readonly(path)
    conn.row_factory = sqlite3.Row
    try:
        # MAX(checkpoint_id) 即最近活动时间（UUID6 字符串序 == 时间序，已实测验证）
        rows = conn.execute(
            "SELECT thread_id, MAX(checkpoint_id) AS latest, COUNT(*) AS steps "
            "FROM checkpoints GROUP BY thread_id"
        ).fetchall()

        summaries = []
        for row in rows:
            thread_id = row["thread_id"]
            if not include_test and is_test_thread(thread_id):
                continue
            summaries.append(
                {
                    "thread_id": thread_id,
                    "latest": row["latest"],
                    "steps": row["steps"],
                }
            )

        total = len(summaries)
        # 按最近活动时间倒序；无法解析时间的排到最后
        summaries.sort(
            key=lambda item: item["latest"] or "",
            reverse=True,
        )
        summaries = summaries[:limit]

        threads = []
        for item in summaries:
            raw_text = _first_human_message(conn, item["thread_id"])
            updated_at = _checkpoint_time(item["latest"])
            title = make_title(raw_text) if raw_text else ""

            # 会话工作目录：前端据此恢复该会话已生成的产物（PDF/PNG/Markdown 等）。
            # 只有目录真实存在时才返回，避免前端拿着不存在的路径去请求文件列表。
            candidate_dir = APP_DIR / "output" / f"session_{item['thread_id']}"
            session_path = (
                str(candidate_dir).replace("\\", "/") if candidate_dir.is_dir() else ""
            )

            threads.append(
                {
                    "thread_id": item["thread_id"],
                    # 提取不到标题时用 thread_id 前缀兜底，避免侧边栏出现空白行
                    "title": title or f"会话 {item['thread_id'][:8]}",
                    "preview": strip_workspace_instruction(raw_text) if raw_text else "",
                    "has_title": bool(title),
                    "steps": item["steps"],
                    "session_path": session_path,
                    "updated_at": updated_at.isoformat() if updated_at else None,
                }
            )

        return {"threads": threads, "total": total, "returned": len(threads)}
    finally:
        conn.close()


# ============================================================
# 单个会话详情：还原多轮问答，供前端点击历史会话后恢复
# ============================================================

# langgraph 用 checkpoint_ns 区分主线程与子智能体；主线程恒为空串。
# 只取主线程才能排除子智能体内部的派单消息与中间回复。
_MAIN_NS = ""


def _message_text(message: Any) -> str:
    """取出消息文本，兼容 str 与多模态分块列表两种 content 形态"""
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return " ".join(p for p in parts if p).strip()
    return ""


def _iter_main_messages(conn: sqlite3.Connection, thread_id: str):
    """
    按时间顺序产出主线程的 human / ai 消息（已按 message id 去重）

    同一条消息会被多个 checkpoint 重复写入 writes 表，因此必须按 id 去重，
    否则会话详情里会出现大量重复气泡。
    """
    rows = conn.execute(
        "SELECT value FROM writes "
        "WHERE thread_id = ? AND checkpoint_ns = ? "
        "AND channel = 'messages' AND type = 'msgpack' "
        "ORDER BY checkpoint_id ASC, idx ASC",
        (thread_id, _MAIN_NS),
    ).fetchall()

    seen: set[str] = set()
    for (blob,) in rows:
        obj = _decode_value(blob)
        if obj is None:
            continue
        for message in (obj if isinstance(obj, (list, tuple)) else [obj]):
            kind = getattr(message, "type", None)
            if kind not in ("human", "ai"):
                continue
            message_id = getattr(message, "id", None)
            if message_id is not None:
                if message_id in seen:
                    continue
                seen.add(message_id)
            yield kind, message, _message_text(message)


def build_turns(conn: sqlite3.Connection, thread_id: str) -> list[dict]:
    """
    把主线程消息还原为「一问一答」的轮次列表（纯逻辑，便于单测）

    判别规则（均已对真实库验证）：
    1. **真实用户输入**：human 消息且含运行时注入的【工作环境指令】标记。
       子智能体派单产生的 human 消息不带该标记，据此排除。
    2. **该轮最终答案**：下一条真实输入之前，最后一条「无 tool_calls 且有正文」的
       ai 消息。带 tool_calls 的 ai 消息是中间过程，不作为答案。
    3. 若某轮找不到符合条件的 ai 消息（如任务被取消或中途出错），
       answer 返回空串，前端展示为「该轮未产生最终回答」。
    """
    turns: list[dict] = []
    current: Optional[dict] = None

    for kind, message, text in _iter_main_messages(conn, thread_id):
        if kind == "human":
            # 不含工作环境指令标记的是子智能体派单消息，跳过
            if _WORKSPACE_MARKER not in text:
                continue
            if current is not None:
                turns.append(current)
            current = {
                "query": strip_workspace_instruction(text),
                "answer": "",
            }
            continue

        # ai 消息：仅在已有真实提问、且自身是终态回复时作为答案
        if current is None:
            continue
        if getattr(message, "tool_calls", None):
            continue
        stripped = text.strip()
        if not stripped:
            continue
        # 同一轮可能有多条终态回复，保留最后一条（后续会覆盖更完整的结论）
        current["answer"] = stripped

    if current is not None:
        turns.append(current)

    return turns


def get_thread_detail(
    thread_id: str,
    db_path: Optional[Path] = None,
) -> dict:
    """
    读取单个历史会话的多轮问答详情（同步实现，API 层需放入线程池调用）

    :param thread_id: 会话 ID
    :param db_path: checkpoints.db 路径，默认 app/checkpoints.db
    :return: {"thread_id", "found", "turns": [{"query","answer"}], "turn_count"}
    """
    path = Path(db_path) if db_path else DEFAULT_DB_PATH

    if not path.exists():
        return {"thread_id": thread_id, "found": False, "turns": [], "turn_count": 0}

    conn = _open_readonly(path)
    conn.row_factory = sqlite3.Row
    try:
        exists = conn.execute(
            "SELECT 1 FROM checkpoints WHERE thread_id = ? LIMIT 1", (thread_id,)
        ).fetchone()

        if not exists:
            return {"thread_id": thread_id, "found": False, "turns": [], "turn_count": 0}

        turns = build_turns(conn, thread_id)
        return {
            "thread_id": thread_id,
            "found": True,
            "turns": turns,
            "turn_count": len(turns),
        }
    finally:
        conn.close()
