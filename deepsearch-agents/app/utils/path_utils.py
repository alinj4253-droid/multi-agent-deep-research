"""
文件路径解析工具

负责把模型或工具返回的虚拟路径、上传文件路径和相对路径统一转换为本地绝对路径。

- resolve_path: 兼容旧行为，含 updated/ 缓存目录解析（供历史逻辑与测试使用）。
- resolve_session_path: 严格的 Session Workspace 边界解析，
  Agent 的读/写工具统一使用它，确保任何路径都不能离开当前会话工作区。
"""

import os
from pathlib import Path
from typing import Optional

# app/utils/path_utils.py -> parents[1] 即 app 目录
# updated/ 与 output/ 都挂在 app 目录下，必须以此为基准解析，
# 不能用 Path.resolve()（它相对进程 CWD，而 CWD 通常是 deepsearch-agents/）
_APP_DIR = Path(__file__).resolve().parents[1]


def resolve_path(filename: str, session_dir: Optional[str] = None) -> str:
    """
    解析文件路径，并尽量把任务产物限制在当前会话目录中

    :param filename: 模型、工具或用户传入的文件名/路径
    :param session_dir: 当前任务的会话目录
    :return: 解析后的绝对路径
    """
    path = Path(filename)
    path_str = filename.replace("\\", "/")

    # 大模型常返回 /workspace、/mnt/data 这类沙箱路径，本地项目需要先剥离虚拟前缀
    for prefix in ["/workspace", "/mnt/data", "/home/user"]:
        if path_str.startswith(prefix):
            cleaned = path_str[len(prefix):].lstrip("/")
            path = Path(cleaned)
            path_str = str(path).replace("\\", "/")
            break

    # updated/ 用于存放用户上传文件，应优先按项目根目录下的真实上传路径解析
    if "updated/" in path_str:
        idx = path_str.find("updated/")
        relative_part = path_str[idx:]
        # 以 app 目录为基准拼接，避免相对进程 CWD 解析到不存在的位置
        return str((_APP_DIR / relative_part).resolve())

    if not session_dir:
        return str(path.resolve())

    session_path = Path(session_dir).resolve()
    session_name = session_path.name
    is_unix_abs = path_str.startswith("/")

    if path.is_absolute() or (os.name == "nt" and is_unix_abs):
        # Windows 下 "/xxx" 没有盘符，按会话目录内的相对路径处理
        if os.name == "nt" and is_unix_abs and not path.drive:
            full_path = session_path / path_str.lstrip("/")
        else:
            full_path = path.resolve()

        try:
            if session_path in full_path.parents or full_path == session_path:
                return _fix_nested_session_path(full_path, session_path, session_name)
        except Exception:
            pass

        # 真实绝对路径且不在 session_dir 中时保持原样，避免误改外部资源路径
        return str(full_path)

    parts = path.parts

    # 避免模型把 session 名或 output 前缀重复拼到当前会话目录里
    if session_name in parts:
        return str(session_path / path.name)

    if parts and parts[0] == "output":
        return str(session_path / path.name)

    return str(session_path / path)


class PathEscapeError(ValueError):
    """Agent 文件操作试图离开会话工作区时抛出"""


def resolve_session_path(input_path, session_root, must_exist=False):
    """
    把模型/工具传入的路径严格解析到会话工作区之内。

    与 resolve_path 的区别：这里强制建立 Session Workspace 文件路径边界——
    无论传入相对路径还是绝对路径，resolve 之后必须仍位于 session_root 之内；
    凡是指向会话目录之外的（多层 ../、/etc/passwd、Windows 盘符路径、
    updated/ 缓存目录、符号链接逃逸等）一律拒绝，而不是"绝对路径就放行"。

    :param input_path: 模型/用户传入的路径或文件名
    :param session_root: 当前会话工作区根目录（output/session_{thread_id}）
    :param must_exist: True 时要求最终路径已经存在
    :return: resolve 后的绝对路径 Path 对象
    :raises PathEscapeError: 解析结果不在 session_root 之内
    """
    if not input_path or not isinstance(input_path, str):
        raise PathEscapeError("路径为空")

    root = Path(session_root).resolve()
    cleaned = input_path.replace("\\", "/")

    # 剥离上游教程里的虚拟工作区前缀（/workspace、/mnt/data、/home/user）
    for prefix in ("/workspace", "/mnt/data", "/home/user"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].lstrip("/")
            break

    has_drive = len(cleaned) >= 2 and cleaned[1:2] == ":"
    candidate = Path(cleaned)

    if candidate.is_absolute() or has_drive:
        # Windows 下 "/xxx" 无盘符，按会话内相对路径处理
        if os.name == "nt" and cleaned.startswith("/") and not has_drive:
            candidate = root / cleaned.lstrip("/")
    else:
        candidate = root / candidate

    resolved = candidate.resolve()

    # 核心边界：resolve 之后必须仍在会话根之内（可抵御 ../ 与符号链接逃逸）
    if not resolved.is_relative_to(root):
        raise PathEscapeError(
            "拒绝访问：路径 " + input_path + " 解析后离开了会话工作区 " + str(root)
        )

    if must_exist and not resolved.exists():
        raise PathEscapeError("路径不存在：" + str(resolved))

    return resolved


def _fix_nested_session_path(
    full_path: Path,
    session_path: Path,
    session_name: str,
) -> str:
    """
    修正 session_xxx/session_xxx/file.md 这类重复嵌套路径
    """
    parts = full_path.parts
    for index in range(len(parts) - 1):
        if parts[index] == session_name and parts[index + 1] == session_name:
            return str(session_path / full_path.name)
    return str(full_path)
