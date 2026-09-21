"""
统一的输入校验模块

thread_id 会参与拼出 `output/session_{thread_id}`、`updated/session_{thread_id}`
等文件系统路径。如果允许 `../`、反斜杠、盘符或超长 ID，文件边界就会变得不可信。
因此所有接收 thread_id 的入口（HTTP、WebSocket、任务、上传、历史会话、取消）
都必须经过同一个白名单规则，而不是各自散落一份正则。

双层保护：
1. API / Schema 层先用 validate_thread_id 拦截非法值；
2. 底层会话目录创建逻辑仍应复用本规则，避免未来新增入口时绕过。
"""

import re

# 只允许字母、数字、下划线、连字符，长度 1-64
THREAD_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class InvalidThreadIdError(ValueError):
    """thread_id 不满足统一白名单规则时抛出"""


def validate_thread_id(thread_id: str) -> str:
    """
    校验 thread_id 是否安全合法，合法时原样返回。

    拒绝：空串、`../`、`..\\`、目录分隔符、盘符、`.`、超长 ID 等。
    自动生成的 UUID 与形如 `abc_123-test` 的 ID 均合法。
    """
    if not isinstance(thread_id, str):
        raise InvalidThreadIdError("thread_id 必须是字符串")
    if not thread_id or not THREAD_ID_PATTERN.fullmatch(thread_id):
        raise InvalidThreadIdError(
            "非法的 thread_id：只允许 1-64 位字母、数字、下划线或连字符，"
            "不允许路径分隔符或 `..`"
        )
    return thread_id
