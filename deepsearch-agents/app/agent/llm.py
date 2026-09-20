"""
大模型初始化模块

统一从 .env 读取模型配置，创建项目全局复用的模型对象（OpenAI 兼容协议）。

当前统一接入 DeepSeek（OpenAI 兼容端点 https://api.deepseek.com/v1）：
- model       主智能体使用：中心化的任务规划、意图路由、最终报告汇总
- fast_model  子智能体使用：工具调用、检索/计算结果整理（多轮 ReAct，速度优先）

默认主、子智能体都使用 deepseek-flash（响应快、支持 function-calling）；
若希望主智能体用质量更高的模型，只需在 .env 把 LLM_MAIN_MODEL 改为 deepseek-v4-pro。
切换模型或供应商只需改 .env，无需改动业务代码。
"""

import os

from dotenv import find_dotenv, load_dotenv
from langchain.chat_models import init_chat_model

# find_dotenv 会从当前目录向上查找 .env，适合脚本和 Web 服务从不同入口启动的场景
load_dotenv(find_dotenv())

_API_KEY = os.getenv("OPENAI_API_KEY")
_BASE_URL = os.getenv("OPENAI_BASE_URL")
_DEFAULT_MODEL = "deepseek-flash"


def _make_chat_model(env_name: str):
    """按环境变量名创建一个 OpenAI 兼容的聊天模型；未配置时回退默认模型"""
    return init_chat_model(
        model=os.getenv(env_name) or _DEFAULT_MODEL,
        model_provider="openai",
        api_key=_API_KEY,
        base_url=_BASE_URL,
    )


# 主智能体：中心化规划 / 路由 / 最终汇总
model = _make_chat_model("LLM_MAIN_MODEL")

# 子智能体：检索 / 计算 / 信息整理（deepagents 子智能体支持单独指定 model）
fast_model = _make_chat_model("LLM_FAST_MODEL")
