"""
RAGFlow 连接配置加载模块

集中读取 RAGFlow SDK 需要的 API Key 和服务地址，供原始调用示例与
LangChain 工具共用。这样后续如果 .env 字段或读取规则调整，只需要改这一处。

注意：ragflow-sdk 是**可选依赖**（当前环境未安装），因此本模块不在导入期
import SDK，而是由 get_ragflow_client() 在真正需要时才延迟加载并给出友好提示。
"""

import os
from typing import Any, Optional, Tuple

from dotenv import find_dotenv, load_dotenv


class RagflowNotAvailable(RuntimeError):
    """RAGFlow 不可用：SDK 未安装或服务地址/密钥未配置"""


def _load_ragflow_env() -> Tuple[Optional[str], Optional[str]]:
    """
    加载 RAGFlow 环境变量

    使用 python-dotenv 自动向上查找 .env，保持和项目其他配置加载方式一致。
    :return: (api_key, base_url)，缺失配置时对应位置返回 None
    """
    load_dotenv(find_dotenv())

    # RAGFlow SDK 初始化只需要这两个核心字段：认证 API Key 和服务基础地址
    api_key = os.getenv("RAGFLOW_API_KEY")
    base_url = os.getenv("RAGFLOW_API_URL")
    return api_key, base_url


# 客户端只初始化一次并复用，避免每次工具调用都重建 SDK 对象
_client_cache: Optional[Any] = None


def get_ragflow_client() -> Any:
    """
    延迟获取 RAGFlow 客户端

    把 `from ragflow_sdk import RAGFlow` 推迟到函数内部执行，这样即使当前环境
    没有安装 ragflow-sdk，本模块（以及依赖它的工具模块）仍可被正常 import，
    只有真正发起知识库调用时才会报错，且返回的是可理解的中文提示。

    :return: RAGFlow 客户端实例
    :raises RagflowNotAvailable: SDK 未安装，或 .env 缺少 RAGFLOW_API_KEY / RAGFLOW_API_URL
    """
    global _client_cache

    if _client_cache is not None:
        return _client_cache

    api_key, base_url = _load_ragflow_env()

    if not api_key or not base_url:
        raise RagflowNotAvailable(
            "RAGFlow 未配置：请在 .env 中设置 RAGFLOW_API_KEY 和 RAGFLOW_API_URL 后再调用知识库工具。"
        )

    try:
        # 延迟导入：ragflow-sdk 是可选依赖，缺失时不应导致整个模块无法 import
        from ragflow_sdk import RAGFlow
    except ImportError as exc:
        raise RagflowNotAvailable(
            "RAGFlow SDK 未安装：请先执行 `pip install ragflow-sdk`，"
            "或确认当前任务是否需要知识库能力（主链路不依赖 RAGFlow）。"
        ) from exc

    _client_cache = RAGFlow(api_key=api_key, base_url=base_url)
    return _client_cache


def reset_client_cache() -> None:
    """清空客户端缓存，便于测试或修改 .env 后重新初始化"""
    global _client_cache
    _client_cache = None
