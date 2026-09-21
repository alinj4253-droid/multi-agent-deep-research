"""
文件路径解析工具

Agent 的读/写文件工具（read / markdown / pdf）统一使用 resolve_session_path，
把模型或工具返回的虚拟路径、相对路径、绝对路径严格解析到“当前会话工作区”之内：

- 无论传入相对路径还是绝对路径，resolve 之后必须仍位于 session_root 之内；
- 指向会话目录之外的多层 ../、/etc/passwd、Windows 盘符路径、updated/ 上传缓存
  目录、符号链接逃逸等一律拒绝（PathEscapeError），而不是“绝对路径就放行”。

说明：cwd（子进程工作目录）只决定相对路径的解析位置，并不阻止代码 open 绝对路径、
读取环境变量或访问网络——真正强制会话路径边界的是本模块的 resolve_session_path，
它只作用于 Agent 自带的文件工具。
"""

import os
from pathlib import Path


class PathEscapeError(ValueError):
    """Agent 文件操作试图离开会话工作区时抛出"""


def resolve_session_path(input_path, session_root, must_exist=False):
    """
    把模型/工具传入的路径严格解析到会话工作区之内。

    :param input_path: 模型/用户传入的路径或文件名
    :param session_root: 当前会话工作区根目录（output/session_{thread_id}）
    :param must_exist: True 时要求最终路径已经存在
    :return: resolve 后的绝对路径 Path 对象
    :raises PathEscapeError: 路径为空，或解析结果不在 session_root 之内
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
