# 多智能体深度研究助手系统

> 基于 LangGraph DeepAgents 框架构建的多智能体协作研究系统，面向学术研究场景，支持网络检索、数据分析和私有文档查询三类任务。

## 项目架构

```
┌─────────────────────────────────────────────────────────┐
│                      用户请求 (Web UI)                   │
└───────────────────────────┬─────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────┐
│                    主智能体 (Orchestrator)               │
│         负责任务规划、路由分发、结果汇总、文档生成          │
└──────┬──────────────┬──────────────────┬────────────────┘
       │              │                  │
       ▼              ▼                  ▼
┌────────────┐ ┌────────────┐ ┌────────────┐
│ 网络检索    │ │ 数据分析    │ │ 私有文档    │
│ 助手        │ │ 助手        │ │ 助手        │
│(DuckDuckGo)│ │ (Python)   │ │ (RAGFlow)  │
└────────────┘ └────────────┘ └────────────┘
```

## 核心技术亮点

### 1. 多智能体协作架构
- **主智能体**：负责任务理解、步骤规划、子智能体调度和最终结果汇总
- **网络检索助手**：基于免费的 DuckDuckGo 进行互联网公开信息检索，内置检索次数预算、语义缓存与结果重排序
- **数据分析助手**：在沙箱中执行 Python 代码，完成数据统计和可视化
- **私有文档助手**：通过 RAGFlow 查询用户上传的本地知识库

### 2. 会话持久化
- 基于 LangGraph Checkpointer 机制，将会话状态持久化到 SQLite
- 服务重启后可恢复历史会话上下文，支持多轮对话

### 3. 检索链路治理：免费引擎 + 调用预算 + 相关性优化
- **零成本检索**：采用免费、无需 API Key 的 DuckDuckGo 搜索引擎（ddgs），不依赖任何付费检索服务即可稳定联网
- **检索次数硬预算（Tool-level Budget）**：在工具层按会话对"实际对外检索"计数并设置上限（默认 3 次），达到上限即短路返回、引导子智能体立即基于已有结果总结。相比仅靠提示词约束的"软限制"，从机制上杜绝了 Agent 反复换关键词检索导致的调用循环，单次研究任务的搜索次数从 15+ 次收敛到 3 次以内，端到端耗时显著下降
- **调用纪律双层约束**：主智能体提示词限定"同一助手单次任务只派单一次"，网络助手提示词限定检索角度数，配合工具层硬预算形成"软约束 + 硬兜底"
- **语义缓存**：LRU 缓存相同查询（标准化大小写/空格后匹配），命中直接返回且不消耗预算，空结果不缓存以避免故障污染
- **结果重排序**：基于关键词在标题/摘要的命中度对结果二次排序，提升 Top-K 相关性

### 4. 沙箱安全执行
- Python 代码在独立子进程中执行，30 秒超时保护
- 工作目录限定在当前会话目录，防止越权访问

## 技术栈

| 层级 | 技术选型 |
|------|----------|
| 智能体框架 | LangGraph + DeepAgents |
| 大模型 | Qwen 系列（阿里云百炼，OpenAI 兼容接口，可切换 qwen3.8-27b / flash / max 等） |
| 后端 | FastAPI + Uvicorn |
| 前端 | React + TypeScript + Vite + Tailwind CSS |
| 数据库 | SQLite (会话持久化) |
| 搜索 | DuckDuckGo（ddgs，免费、无需 Key）+ 检索次数预算 |
| 知识库 | RAGFlow（可选） |

## 快速启动

### 后端
```bash
cd deepsearch-agents
python -m venv .venv
# Windows: .venv\Scripts\activate   |   macOS/Linux: source .venv/bin/activate
pip install -e .
# 配置 .env 文件（见下方）
python -m uvicorn app.api.server:app --host 0.0.0.0 --port 8001
```

### 前端
```bash
cd frontend-demo
npm install
npm run dev
```

### 环境变量配置 (.env)
```
# 大模型（OpenAI 兼容接口；模型名可按需切换）
LLM_QWEN_MAX=qwen3.8-27b
OPENAI_API_KEY=your_api_key
OPENAI_BASE_URL=https://your-endpoint/compatible-mode/v1

# 网络检索使用免费 DuckDuckGo，无需任何 Key

# 私有文档助手为可选项：不配置 RAGFlow 时该助手自动以空壳方式占位
RAGFLOW_API_KEY=your_ragflow_key
RAGFLOW_API_URL=http://localhost:9380/api/v1
```

## 目录结构

```
.
├── deepsearch-agents/          # 后端项目
│   ├── app/
│   │   ├── agent/              # 智能体定义
│   │   │   ├── subagents/      # 子智能体
│   │   │   ├── prompts.py      # 提示词加载
│   │   │   └── main_agent.py   # 主智能体
│   │   ├── tools/              # 工具集
│   │   │   ├── web_search_tool.py  # 网络搜索（DuckDuckGo + 检索预算 + 缓存 + 重排序）
│   │   │   ├── ddg_search.py    # DuckDuckGo 检索封装
│   │   │   ├── python_exec_tool.py  # Python 沙箱执行
│   │   │   └── ragflow_tools.py     # RAGFlow 知识库（可选）
│   │   ├── api/                # API 层（FastAPI + WebSocket）
│   │   └── prompt/             # 提示词配置
│   ├── tests/                  # 单元测试（pytest，覆盖缓存/重排序/检索预算）
│   └── pyproject.toml
├── frontend-demo/               # 前端项目
│   ├── src/
│   │   ├── App.tsx
│   │   ├── hooks/              # WebSocket hook
│   │   └── lib/                # API 封装
│   └── package.json
└── 改造方案.md                  # 详细改造设计文档
```

## 简历项目描述示例

> **多智能体深度研究助手系统** | 个人项目
> 
> 基于 LangGraph DeepAgents 框架设计并实现了一个三助手协作的深度研究系统，支持网络检索、数据分析和私有文档查询。
> 
> - 设计并实现了「主智能体 + 三助手」的分层协作架构，通过任务路由机制实现不同类型研究任务的自动分派，常识类问题直接作答、专业类任务自动路由到对应专家助手
> - 基于 SQLite Async Checkpointer 实现会话状态异步持久化，支持服务重启后历史会话恢复，解决了内存存储易丢失、同步 Saver 不支持异步流式输出的问题
> - 针对多智能体检索易陷入"反复搜索"循环的问题，在工具层设计按会话计数的检索次数硬预算机制（Tool-level Budget），配合主/子智能体双层提示词调用纪律，将单次研究任务的网络搜索次数从 15+ 次收敛到 3 次以内；并基于免费 DuckDuckGo 引擎、LRU 语义缓存与关键词重排序，在零检索成本下保证结果相关性
> - 为数据分析助手实现基于子进程的 Python 沙箱执行环境，配合 30 秒超时与会话目录隔离，保障代码执行安全
> - 构建了基于 React + FastAPI 的前后端分离架构，通过 WebSocket 实时推送"子智能体调用 / 工具执行 / 结果产出"事件流，实现多智能体研究过程的可观测性
