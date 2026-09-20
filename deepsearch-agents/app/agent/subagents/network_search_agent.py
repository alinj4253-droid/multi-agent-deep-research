"""
网络检索子智能体配置模块

将 app/prompt/prompts.yml 中的网络检索助手配置与网络搜索工具组装成
DeepAgents 可识别的字典式子智能体。主智能体根据 description 决定是否
把公开网络信息查询任务分派给它；底层以本地自建 SearXNG 元搜索为主、
DuckDuckGo 为降级。
"""

from app.agent.llm import fast_model
from app.agent.prompts import sub_agents_content
from app.tools.web_search_tool import internet_search

# 网络检索助手：SearXNG 为主、DuckDuckGo 兜底（均免费、无需 API Key）
network_search_agent = {
    "name": sub_agents_content["network_search"]["name"],
    "description": sub_agents_content["network_search"]["description"],
    "system_prompt": sub_agents_content["network_search"]["system_prompt"],
    "tools": [internet_search],
    "model": fast_model,
}
