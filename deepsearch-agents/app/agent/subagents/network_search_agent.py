"""
网络检索子智能体配置模块

将 app/prompt/prompts.yml 中的网络检索助手配置与网络搜索工具组装成
DeepAgents 可识别的字典式子智能体。主智能体根据 description 决定是否
把公开网络信息查询任务分派给它；底层以本地自建 SearXNG 元搜索为主、
DuckDuckGo 为降级。

注意：这里只提供 build_network_search_agent() 构建函数，模型在 runtime 调用时
才经 get_fast_model() 创建，import 本模块不产生初始化 LLM 的副作用（CI 友好）。
"""

from app.agent.llm import get_fast_model
from app.agent.prompts import sub_agents_content
from app.tools.web_search_tool import internet_search


def build_network_search_agent():
    """在 runtime 组装网络检索助手（SearXNG 为主、DuckDuckGo 兜底，均免费无需 Key）。"""
    return {
        "name": sub_agents_content["network_search"]["name"],
        "description": sub_agents_content["network_search"]["description"],
        "system_prompt": sub_agents_content["network_search"]["system_prompt"],
        "tools": [internet_search],
        "model": get_fast_model(),
    }
