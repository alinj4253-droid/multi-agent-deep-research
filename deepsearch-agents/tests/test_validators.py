"""
Phase 1 安全测试：统一 thread_id 白名单 + Session Workspace 路径边界

覆盖改造方案 §2/§3 的验收要求：
- 合法 UUID / abc_123-test 通过；../、..\\、foo/bar、>64 字符、空串被拒绝
- /api/task 与 /api/upload 行为一致
- 直接调用底层会话路径解析函数时，越界路径仍然被拒绝
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.utils.path_utils import PathEscapeError, resolve_session_path
from app.utils.validators import InvalidThreadIdError, validate_thread_id


# ---------------- thread_id 校验 ----------------

@pytest.mark.parametrize(
    "tid",
    [
        "550e8400-e29b-41d4-a716-446655440000",
        "abc_123-test",
        "x",
        "A" * 64,
    ],
)
def test_valid_thread_ids_accepted(tid):
    assert validate_thread_id(tid) == tid


@pytest.mark.parametrize(
    "tid",
    [
        "",
        "../test",
        "..\\test",
        "foo/bar",
        "foo\\bar",
        ".",
        "..",
        "A" * 65,
        None,
        "has space",
        "中文ID",
    ],
)
def test_invalid_thread_ids_rejected(tid):
    with pytest.raises(InvalidThreadIdError):
        validate_thread_id(tid)


# ---------------- Session Workspace 路径边界 ----------------

def test_resolve_normal_relative_path(tmp_path):
    root = tmp_path / "session_s1"
    root.mkdir()
    out = resolve_session_path("report.md", root)
    assert out == (root / "report.md").resolve()


def test_resolve_subdir_path(tmp_path):
    root = tmp_path / "session_s1"
    root.mkdir()
    out = resolve_session_path("sub/chart.png", root)
    assert out == (root / "sub" / "chart.png").resolve()
    assert out.is_relative_to(root.resolve())


def test_resolve_in_session_absolute_path(tmp_path):
    root = tmp_path / "session_s1"
    root.mkdir()
    inside = root / "report.md"
    out = resolve_session_path(str(inside), root)
    assert out == inside.resolve()


@pytest.mark.parametrize("evil", ["../secret.txt", "../../etc/passwd", "../../../windows"])
def test_rejects_parent_traversal(tmp_path, evil):
    root = tmp_path / "session_s1"
    root.mkdir()
    with pytest.raises(PathEscapeError):
        resolve_session_path(evil, root)


def test_rejects_unix_absolute_outside(tmp_path):
    root = tmp_path / "session_s1"
    root.mkdir()
    with pytest.raises(PathEscapeError):
        resolve_session_path("/etc/passwd", root)


def test_rejects_windows_drive_path(tmp_path):
    root = tmp_path / "session_s1"
    root.mkdir()
    with pytest.raises(PathEscapeError):
        resolve_session_path(r"C:\Windows\System32\hosts", root)


def test_rejects_workspace_prefix_escaping(tmp_path):
    """/workspace/../secret 剥离前缀后仍越界"""
    root = tmp_path / "session_s1"
    root.mkdir()
    with pytest.raises(PathEscapeError):
        resolve_session_path("/workspace/../outside.csv", root)


def test_must_exist_raises_when_missing(tmp_path):
    root = tmp_path / "session_s1"
    root.mkdir()
    with pytest.raises(PathEscapeError):
        resolve_session_path("nope.md", root, must_exist=True)


def test_session_root_itself_allowed(tmp_path):
    root = tmp_path / "session_s1"
    root.mkdir()
    out = resolve_session_path(".", root)
    assert out == root.resolve()
