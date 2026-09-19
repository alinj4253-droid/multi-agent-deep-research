"""
RAGFlow 知识库子智能体配置模块

注意：当前未启用 RAGFlow 服务，此子智能体暂时为空实现。
后续接入 RAGFlow 后取消注释即可恢复。
"""

from app.agent.prompts import sub_agents_content

# TODO: 接入 RAGFlow 后取消以下注释
# from app.tools.ragflow_tools import create_ask_delete, get_assistant_list

# 私有文档助手（当前未启用，tools 为空，主智能体不会调用）
knowledge_base_agent = {
    "name": sub_agents_content["ragflow"]["name"],
    "description": sub_agents_content["ragflow"]["description"],
    "system_prompt": sub_agents_content["ragflow"]["system_prompt"],
    # TODO: 接入 RAGFlow 后改为 [get_assistant_list, create_ask_delete]
    "tools": [],
}
