"""
网络检索子智能体配置模块

将 app/prompt/prompts.yml 中的 tavily 配置与网络搜索工具组装成
DeepAgents 可识别的字典式子智能体。主智能体后续会根据 description
决定是否把公开网络信息查询任务分派给它。
"""

from app.agent.prompts import sub_agents_content
from app.tools.web_search_tool import internet_search

# 网络检索助手使用 DuckDuckGo 搜索引擎（免费、无需 API Key）
network_search_agent = {
    "name": sub_agents_content["tavily"]["name"],
    "description": sub_agents_content["tavily"]["description"],
    "system_prompt": sub_agents_content["tavily"]["system_prompt"],
    "tools": [internet_search],
}
