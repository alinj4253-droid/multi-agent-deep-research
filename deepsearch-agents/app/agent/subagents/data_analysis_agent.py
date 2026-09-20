"""
数据分析子智能体配置模块

将 app/prompt/prompts.yml 中的 data_analysis 配置与 Python 代码执行工具组装成
DeepAgents 可识别的字典式子智能体。主智能体后续会根据 description
决定是否把数据分析任务分派给它。
"""

from app.agent.llm import fast_model
from app.agent.prompts import sub_agents_content
from app.tools.python_exec_tool import execute_python_code

# 数据分析助手专注于数据处理和数值计算
# 它通过 Python 代码执行工具完成统计分析、可视化和数据处理
data_analysis_agent = {
    "name": sub_agents_content["data_analysis"]["name"],
    "description": sub_agents_content["data_analysis"]["description"],
    "system_prompt": sub_agents_content["data_analysis"]["system_prompt"],
    "tools": [execute_python_code],
    "model": fast_model,
}
