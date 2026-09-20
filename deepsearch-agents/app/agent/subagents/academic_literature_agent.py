"""
学术文献子智能体配置模块

将 app/prompt/prompts.yml 中的 academic 配置与学术检索工具组装成
DeepAgents 可识别的字典式子智能体。主智能体根据 description 把论文/文献
类任务分派给它；底层聚合 arXiv、OpenAlex、Crossref 三个免费官方学术库。
"""

from app.agent.llm import fast_model
from app.agent.prompts import sub_agents_content
from app.tools.academic_search_tool import academic_paper_search

# 学术文献助手：检索论文与科研文献（免费官方学术 API，无需付费 Key）
academic_literature_agent = {
    "name": sub_agents_content["academic"]["name"],
    "description": sub_agents_content["academic"]["description"],
    "system_prompt": sub_agents_content["academic"]["system_prompt"],
    "tools": [academic_paper_search],
    "model": fast_model,
}
