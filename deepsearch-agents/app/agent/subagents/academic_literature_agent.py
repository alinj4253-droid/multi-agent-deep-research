"""
学术文献子智能体配置模块

将 app/prompt/prompts.yml 中的 academic 配置和学术检索工具组装成
DeepAgents 可识别的字典式子智能体。主智能体根据 description 把论文/文献
类任务分派给它；底层聚合 arXiv、OpenAlex、Crossref 三个免费官方学术库。

注意：这里只提供 build_academic_literature_agent() 构建函数，模型在 runtime
调用时才经 get_fast_model() 创建，import 本模块不产生初始化 LLM 的副作用（CI 友好）。
"""

from app.agent.llm import get_fast_model
from app.agent.prompts import sub_agents_content
from app.tools.academic_search_tool import academic_paper_search


def build_academic_literature_agent():
    """在 runtime 组装学术文献助手（arXiv + OpenAlex + Crossref，免费官方 API）。"""
    return {
        "name": sub_agents_content["academic"]["name"],
        "description": sub_agents_content["academic"]["description"],
        "system_prompt": sub_agents_content["academic"]["system_prompt"],
        "tools": [academic_paper_search],
        "model": get_fast_model(),
    }
