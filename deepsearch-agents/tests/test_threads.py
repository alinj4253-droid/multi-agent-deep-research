"""
历史会话模块单元测试（不依赖外网、不依赖大模型）

覆盖：
1. 纯函数：strip_workspace_instruction / make_title / is_test_thread / _checkpoint_time
2. list_threads：测试会话过滤、时间倒序、标题提取、session_path 存在性判定、库缺失降级
3. build_turns / get_thread_detail：多轮还原、子智能体派单消息排除、
   中间过程消息不作为最终答案、只取主线程（checkpoint_ns）、重复消息去重
4. path_utils：updated/ 分支以 app 目录为基准解析

测试用合成 SQLite 库（schema 与 langgraph AsyncSqliteSaver 完全一致），
消息体用 langgraph 自带 JsonPlusSerializer 序列化为 msgpack，与真实库同构。
"""

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from app.api import threads as th
import app.utils.path_utils as pu  # noqa: E402

SERDE = JsonPlusSerializer()

# 与真实库一致的建表语句
_SCHEMA = """
CREATE TABLE checkpoints (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    parent_checkpoint_id TEXT,
    type TEXT,
    checkpoint BLOB,
    metadata BLOB,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);
CREATE TABLE writes (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    channel TEXT NOT NULL,
    type TEXT,
    value BLOB,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);
"""

# UUID6 时间戳偏移：1582-10-15 到 1970-01-01 之间的 100ns 间隔数
_OFFSET = 0x01B21DD213814000


def make_uuid6(unix_seconds: float) -> str:
    """由 Unix 秒构造时间有序的 UUID6 字符串（seq 越大时间越晚）"""
    timestamp_100ns = int(unix_seconds * 1e7) + _OFFSET
    time_high = (timestamp_100ns >> 28) & 0xFFFFFFFF
    time_mid = (timestamp_100ns >> 12) & 0xFFFF
    time_low = timestamp_100ns & 0xFFF

    int_value = (
        (time_high << 96)
        | (time_mid << 80)
        | (0x6 << 76)          # version 6
        | (time_low << 64)
        | (0b10 << 62)         # RFC 4122 variant
        | (0 << 48)            # clock_seq
        | 0x123456789ABC       # node
    )
    return str(uuid.UUID(int=int_value))


def make_db(path: Path) -> sqlite3.Connection:
    """按 langgraph schema 建一个空的合成库"""
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    return conn


def seed_message(
    conn: sqlite3.Connection,
    thread_id: str,
    message,
    unix_seconds: float,
    ns: str = "",
) -> str:
    """
    往合成库写入一条消息（同时补 checkpoints 行，使 list_threads 能发现该会话）

    :return: 该消息使用的 checkpoint_id
    """
    checkpoint_id = make_uuid6(unix_seconds)
    message_type, blob = SERDE.dumps_typed(message)

    conn.execute(
        "INSERT OR IGNORE INTO checkpoints "
        "(thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, type, checkpoint, metadata) "
        "VALUES (?, ?, ?, NULL, 'empty', NULL, NULL)",
        (thread_id, ns, checkpoint_id),
    )
    conn.execute(
        "INSERT OR REPLACE INTO writes "
        "(thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, value) "
        "VALUES (?, ?, ?, 'task', 0, 'messages', ?, ?)",
        (thread_id, ns, checkpoint_id, message_type, blob),
    )
    return checkpoint_id


@pytest.fixture()
def db_path(tmp_path) -> Path:
    return tmp_path / "checkpoints.db"


# ============================================================
# 纯函数
# ============================================================
class TestPureHelpers:
    def test_strip_workspace_instruction_removes_injected_block(self):
        raw = "检索2026年自动驾驶数据融合进展\n    【工作环境指令】\n    工作目录: output/session_x"
        assert th.strip_workspace_instruction(raw) == "检索2026年自动驾驶数据融合进展"

    def test_strip_workspace_instruction_without_marker(self):
        assert th.strip_workspace_instruction("用一句话解释光合作用") == "用一句话解释光合作用"

    def test_strip_workspace_instruction_empty(self):
        assert th.strip_workspace_instruction("") == ""
        assert th.strip_workspace_instruction(None) == ""

    def test_make_title_collapses_whitespace(self):
        assert th.make_title("第一行\n\n  第二行  ") == "第一行 第二行"

    def test_make_title_truncates_long_text(self):
        long_text = "很长的问题" * 30
        title = th.make_title(long_text, max_chars=42)
        assert len(title) == 43          # 42 字 + 省略号
        assert title.endswith("…")

    def test_make_title_keeps_short_text(self):
        assert th.make_title("短问题") == "短问题"

    def test_is_test_thread_recognizes_automation_prefixes(self):
        for tid in ("e2e-abc", "diag-data-002", "test-x", "smoke-1", "verify-ab47a"):
            assert th.is_test_thread(tid) is True

    def test_is_test_thread_keeps_real_sessions(self):
        assert th.is_test_thread("9d6a2c2c-1475-4e9d-9171-7ef9dbb6de5a") is False
        assert th.is_test_thread("") is False

    def test_checkpoint_time_decodes_uuid6(self):
        # 1700000000 Unix 秒 == 2023-11-14T22:13:20Z
        cid = make_uuid6(1_700_000_000)
        parsed = th._checkpoint_time(cid)

        assert parsed is not None
        assert parsed.astimezone(timezone.utc).replace(microsecond=0) == datetime(
            2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc
        )

    def test_checkpoint_time_rejects_non_uuid6(self):
        assert th._checkpoint_time(str(uuid.uuid4())) is None   # version 4
        assert th._checkpoint_time("not-a-uuid") is None


# ============================================================
# list_threads
# ============================================================
class TestListThreads:
    def test_missing_db_returns_empty(self, tmp_path):
        result = th.list_threads(db_path=tmp_path / "nope.db")
        assert result == {"threads": [], "total": 0, "returned": 0}

    def test_extracts_title_and_orders_by_time_desc(self, db_path):
        conn = make_db(db_path)
        old = "11111111-1111-1111-1111-111111111111"
        new = "22222222-2222-2222-2222-222222222222"

        seed_message(conn, old, HumanMessage(content="较早的问题\n【工作环境指令】\n工作目录: x"), 1_700_000_000)
        seed_message(conn, new, HumanMessage(content="较新的问题\n【工作环境指令】\n工作目录: y"), 1_800_000_000)
        conn.commit()
        conn.close()

        result = th.list_threads(db_path=db_path)

        assert result["total"] == 2
        assert [t["thread_id"] for t in result["threads"]] == [new, old]
        assert result["threads"][0]["title"] == "较新的问题"
        assert result["threads"][0]["has_title"] is True

    def test_filters_test_threads_by_default(self, db_path):
        conn = make_db(db_path)
        seed_message(conn, "e2e-automated", HumanMessage(content="自动化测试\n【工作环境指令】"), 1_700_000_000)
        seed_message(conn, "verify-abc123", HumanMessage(content="验证脚本\n【工作环境指令】"), 1_700_000_100)
        real = "33333333-3333-3333-3333-333333333333"
        seed_message(conn, real, HumanMessage(content="真实用户问题\n【工作环境指令】"), 1_700_000_200)
        conn.commit()
        conn.close()

        default = th.list_threads(db_path=db_path)
        assert default["total"] == 1
        assert default["threads"][0]["thread_id"] == real

        with_test = th.list_threads(db_path=db_path, include_test=True)
        assert with_test["total"] == 3

    def test_limit_is_respected(self, db_path):
        conn = make_db(db_path)
        for i in range(5):
            tid = f"{i:08d}-0000-0000-0000-000000000000"
            seed_message(conn, tid, HumanMessage(content=f"问题{i}\n【工作环境指令】"), 1_700_000_000 + i)
        conn.commit()
        conn.close()

        result = th.list_threads(db_path=db_path, limit=2)
        assert result["total"] == 5
        assert result["returned"] == 2
        assert len(result["threads"]) == 2

    def test_limit_is_clamped_to_valid_range(self, db_path):
        conn = make_db(db_path)
        seed_message(conn, "44444444-4444-4444-4444-444444444444",
                     HumanMessage(content="q\n【工作环境指令】"), 1_700_000_000)
        conn.commit()
        conn.close()

        # limit=0 会被抬到 1，不应抛异常或返回空
        assert th.list_threads(db_path=db_path, limit=0)["returned"] == 1

    def test_fallback_title_when_no_human_message(self, db_path):
        conn = make_db(db_path)
        tid = "55555555-5555-5555-5555-555555555555"
        # 只有 AI 消息、没有用户消息
        seed_message(conn, tid, AIMessage(content="我直接回答了"), 1_700_000_000)
        conn.commit()
        conn.close()

        result = th.list_threads(db_path=db_path)
        assert result["total"] == 1
        assert result["threads"][0]["has_title"] is False
        assert result["threads"][0]["title"] == "会话 55555555"

    def test_session_path_present_only_when_dir_exists(self, db_path, tmp_path, monkeypatch):
        conn = make_db(db_path)
        tid_with_dir = "66666666-6666-6666-6666-666666666666"
        tid_without = "77777777-7777-7777-7777-777777777777"
        seed_message(conn, tid_with_dir, HumanMessage(content="有产物\n【工作环境指令】"), 1_700_000_000)
        seed_message(conn, tid_without, HumanMessage(content="无产物\n【工作环境指令】"), 1_700_000_100)
        conn.commit()
        conn.close()

        # 把 app 目录指向 tmp_path，只给其中一个会话建 output 目录
        monkeypatch.setattr(th, "APP_DIR", tmp_path)
        (tmp_path / "output" / f"session_{tid_with_dir}").mkdir(parents=True)

        result = {t["thread_id"]: t for t in th.list_threads(db_path=db_path)["threads"]}

        assert result[tid_with_dir]["session_path"].endswith(f"output/session_{tid_with_dir}")
        assert "\\" not in result[tid_with_dir]["session_path"]   # 统一正斜杠
        assert result[tid_without]["session_path"] == ""


# ============================================================
# build_turns / get_thread_detail：多轮还原
# ============================================================
class TestBuildTurns:
    def test_restores_multiple_turns(self, db_path):
        conn = make_db(db_path)
        tid = "88888888-8888-8888-8888-888888888888"

        seed_message(conn, tid, HumanMessage(content="第一轮问题\n【工作环境指令】\n工作目录: a"), 1_700_000_000)
        seed_message(conn, tid, AIMessage(content="第一轮答案"), 1_700_000_010)
        seed_message(conn, tid, HumanMessage(content="第二轮问题\n【工作环境指令】\n工作目录: a"), 1_700_000_020)
        seed_message(conn, tid, AIMessage(content="第二轮答案"), 1_700_000_030)
        conn.commit()

        turns = th.build_turns(conn, tid)
        conn.close()

        assert len(turns) == 2
        assert turns[0] == {"query": "第一轮问题", "answer": "第一轮答案"}
        assert turns[1] == {"query": "第二轮问题", "answer": "第二轮答案"}

    def test_excludes_subagent_dispatch_messages(self, db_path):
        """子智能体派单产生的 human 消息不带【工作环境指令】，必须被排除"""
        conn = make_db(db_path)
        tid = "99999999-9999-9999-9999-999999999999"

        seed_message(conn, tid, HumanMessage(content="真实用户问题\n【工作环境指令】\n工作目录: a"), 1_700_000_000)
        seed_message(conn, tid, HumanMessage(content="请检索关于3DGS的最新进展，工作目录为 output/x"), 1_700_000_005)
        seed_message(conn, tid, AIMessage(content="最终答案"), 1_700_000_010)
        conn.commit()

        turns = th.build_turns(conn, tid)
        conn.close()

        assert len(turns) == 1
        assert turns[0]["query"] == "真实用户问题"

    def test_skips_intermediate_ai_with_tool_calls(self, db_path):
        """带 tool_calls 的 AI 消息是中间过程，不能作为最终答案"""
        conn = make_db(db_path)
        tid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

        seed_message(conn, tid, HumanMessage(content="问题\n【工作环境指令】"), 1_700_000_000)
        seed_message(
            conn, tid,
            AIMessage(content="我先制定规划", tool_calls=[{"name": "task", "args": {}, "id": "c1"}]),
            1_700_000_005,
        )
        seed_message(conn, tid, AIMessage(content="真正的最终答案"), 1_700_000_010)
        conn.commit()

        turns = th.build_turns(conn, tid)
        conn.close()

        assert turns[0]["answer"] == "真正的最终答案"

    def test_keeps_last_final_answer_in_a_turn(self, db_path):
        """同一轮有多条终态回复时保留最后一条（更完整的结论）"""
        conn = make_db(db_path)
        tid = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

        seed_message(conn, tid, HumanMessage(content="问题\n【工作环境指令】"), 1_700_000_000)
        seed_message(conn, tid, AIMessage(content="初步结论"), 1_700_000_005)
        seed_message(conn, tid, AIMessage(content="最终完整结论"), 1_700_000_010)
        conn.commit()

        turns = th.build_turns(conn, tid)
        conn.close()

        assert turns[0]["answer"] == "最终完整结论"

    def test_turn_without_answer_returns_empty_string(self, db_path):
        """任务被取消/出错导致没有终态回复时，answer 为空串而非报错"""
        conn = make_db(db_path)
        tid = "cccccccc-cccc-cccc-cccc-cccccccccccc"

        seed_message(conn, tid, HumanMessage(content="被取消的问题\n【工作环境指令】"), 1_700_000_000)
        conn.commit()

        turns = th.build_turns(conn, tid)
        conn.close()

        assert len(turns) == 1
        assert turns[0]["answer"] == ""

    def test_ignores_subagent_namespace_messages(self, db_path):
        """checkpoint_ns 非空的子智能体消息不能混进主线"""
        conn = make_db(db_path)
        tid = "dddddddd-dddd-dddd-dddd-dddddddddddd"

        seed_message(conn, tid, HumanMessage(content="主线问题\n【工作环境指令】"), 1_700_000_000)
        # 子智能体命名空间里的终态回复，不应被当作主线答案
        seed_message(conn, tid, AIMessage(content="子智能体内部结论"), 1_700_000_005, ns="tools:abc-123")
        seed_message(conn, tid, AIMessage(content="主线最终答案"), 1_700_000_010)
        conn.commit()

        turns = th.build_turns(conn, tid)
        conn.close()

        assert len(turns) == 1
        assert turns[0]["answer"] == "主线最终答案"

    def test_dedups_messages_written_by_multiple_checkpoints(self, db_path):
        """同一条消息会被多个 checkpoint 重复写入 writes，必须按 id 去重"""
        conn = make_db(db_path)
        tid = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"

        message = HumanMessage(content="只问了一次\n【工作环境指令】", id="fixed-msg-id")
        # 同一条消息写入两次（模拟多次 checkpoint）
        seed_message(conn, tid, message, 1_700_000_000)
        seed_message(conn, tid, message, 1_700_000_001)
        conn.commit()

        turns = th.build_turns(conn, tid)
        conn.close()

        assert len(turns) == 1

    def test_multimodal_content_is_flattened(self, db_path):
        """content 为多模态分块列表时应拼接其中的文本部分"""
        conn = make_db(db_path)
        tid = "ffffffff-ffff-ffff-ffff-ffffffffffff"

        seed_message(
            conn, tid,
            HumanMessage(content=[
                {"type": "text", "text": "看这张图"},
                {"type": "image_url", "image_url": {"url": "http://x"}},
                {"type": "text", "text": "并分析\n【工作环境指令】"},
            ]),
            1_700_000_000,
        )
        seed_message(conn, tid, AIMessage(content="图片分析结论"), 1_700_000_010)
        conn.commit()

        turns = th.build_turns(conn, tid)
        conn.close()

        assert "看这张图" in turns[0]["query"]


class TestGetThreadDetail:
    def test_missing_db_returns_not_found(self, tmp_path):
        detail = th.get_thread_detail("whatever", db_path=tmp_path / "nope.db")
        assert detail == {"thread_id": "whatever", "found": False, "turns": [], "turn_count": 0}

    def test_unknown_thread_returns_not_found(self, db_path):
        conn = make_db(db_path)
        conn.commit()
        conn.close()

        detail = th.get_thread_detail("nonexistent-thread", db_path=db_path)
        assert detail["found"] is False
        assert detail["turn_count"] == 0

    def test_known_thread_returns_turns(self, db_path):
        conn = make_db(db_path)
        tid = "12121212-1212-1212-1212-121212121212"
        seed_message(conn, tid, HumanMessage(content="问题\n【工作环境指令】"), 1_700_000_000)
        seed_message(conn, tid, AIMessage(content="答案"), 1_700_000_010)
        conn.commit()
        conn.close()

        detail = th.get_thread_detail(tid, db_path=db_path)

        assert detail["found"] is True
        assert detail["thread_id"] == tid
        assert detail["turn_count"] == 1
        assert detail["turns"] == [{"query": "问题", "answer": "答案"}]


# ============================================================
# path_utils：严格 Session Workspace 边界（旧宽松 resolve_path 已删除）
# ============================================================
class TestStrictResolverWorkspaceBoundary:
    def test_relative_path_resolves_inside_session(self, tmp_path):
        import app.utils.path_utils as pu

        session = tmp_path / "output" / "session_z"
        resolved = pu.resolve_session_path("report.md", session)
        assert resolved == (session / "report.md").resolve()

    def test_virtual_workspace_prefix_is_stripped_inside(self, tmp_path):
        import app.utils.path_utils as pu

        session = tmp_path / "output" / "session_a"
        resolved = pu.resolve_session_path("/workspace/report.md", session)
        assert resolved == (session / "report.md").resolve()

    def test_path_into_updated_dir_outside_workspace_rejected(self, tmp_path):
        import app.utils.path_utils as pu

        # output/session_b 为当前工作区，updated/ 是其外部目录，严格解析器必须拒绝
        root = tmp_path / "output" / "session_b"
        target = tmp_path / "updated" / "session_a" / "f.pdf"
        with pytest.raises(pu.PathEscapeError):
            pu.resolve_session_path(str(target), root)

    def test_legacy_resolve_path_removed(self):
        """旧的宽松解析器（绝对路径直接放行）不应再存在。"""
        import app.utils.path_utils as pu

        assert not hasattr(pu, "resolve_path")
        assert not hasattr(pu, "_fix_nested_session_path")
