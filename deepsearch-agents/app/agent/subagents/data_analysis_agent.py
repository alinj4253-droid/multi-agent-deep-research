"""
数据分析子智能体配置模块

将 app/prompt/prompts.yml 中的 data_analysis 配置与 Python 代码执行工具组装成
DeepAgents 可识别的字典式子智能体。主智能体后续会根据 description
决定是否把数据分析任务分派给它。

注意：这里只提供 build_data_analysis_agent() 构建函数，模型在 runtime 调用时
才经 get_fast_model() 创建，import 本模块不产生初始化 LLM 的副作用（CI 友好）。
"""

from app.agent.llm import get_fast_model
from app.agent.prompts import sub_agents_content
from app.tools.python_exec_tool import execute_python_code


def build_data_analysis_agent():
    """在 runtime 组装数据分析助手（受控 Python 子进程完成统计/可视化/数据处理）。"""
    return {
        "name": sub_agents_content["data_analysis"]["name"],
        "description": sub_agents_content["data_analysis"]["description"],
        "system_prompt": sub_agents_content["data_analysis"]["system_prompt"],
        "tools": [execute_python_code],
        "model": get_fast_model(),
    }
