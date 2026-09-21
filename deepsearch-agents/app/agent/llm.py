"""
大模型初始化模块（Runtime 懒加载）

统一从 .env 读取模型配置，按需创建项目复用的 OpenAI 兼容模型对象（当前接 DeepSeek，
端点 https://api.deepseek.com/v1）：
- get_main_model() 主智能体：中心化任务规划、意图路由、最终报告汇总
- get_fast_model() 子智能体：工具调用、检索/计算结果整理（多轮 ReAct，速度优先）

关键设计：**模块 import 时不创建模型、不校验密钥、不初始化任何客户端**。
模型只在真正启动 Agent（FastAPI lifespan 调用 init_main_agent()）时才通过上面两个
工厂函数创建，并由 functools.lru_cache 在进程内复用。这样：

- CI / 单元测试在没有 API Key 的干净环境里也能正常 import 与收集用例（不会在
  pytest 收集阶段就因为 Missing credentials 而中断，exit code 2）；
- 单元测试可以方便地 monkeypatch 工厂函数注入 fake model；
- 缺少必需配置时在这里给出明确错误，而不是抛出底层 SDK 的模糊异常。

默认主、子智能体都使用 deepseek-flash；切换模型或供应商只需改 .env，无需改业务代码。
"""

import os
from functools import lru_cache

from dotenv import find_dotenv, load_dotenv
from langchain.chat_models import init_chat_model

# import 阶段只尝试加载 .env（文件不存在也无妨），不读取/校验密钥、不构造客户端。
# find_dotenv 会从本文件位置向上查找，兼容脚本与 Web 服务从不同入口启动。
load_dotenv(find_dotenv())

_DEFAULT_MODEL = "deepseek-flash"


def _make_chat_model(env_name: str):
    """按环境变量名创建一个 OpenAI 兼容聊天模型；仅在 runtime 被工厂函数调用。"""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        # 在真正需要模型时才报配置错误，错误信息明确指向修复动作；
        # 保证“import 不需要密钥、启动才需要密钥”的边界清晰可测。
        raise RuntimeError(
            "缺少环境变量 OPENAI_API_KEY：请复制 .env.example 为 .env 并填写 LLM 密钥后再启动 Agent"
        )
    return init_chat_model(
        model=os.getenv(env_name) or _DEFAULT_MODEL,
        model_provider="openai",
        api_key=api_key,
        base_url=os.getenv("OPENAI_BASE_URL"),
    )


@lru_cache(maxsize=1)
def get_main_model():
    """主智能体模型（规划 / 路由 / 最终汇总）；进程内首次调用时构造，之后复用。"""
    return _make_chat_model("LLM_MAIN_MODEL")


@lru_cache(maxsize=1)
def get_fast_model():
    """子智能体模型（检索 / 计算 / 信息整理）；进程内首次调用时构造，之后复用。"""
    return _make_chat_model("LLM_FAST_MODEL")
