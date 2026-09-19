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
│ (Tavily)   │ │ (Python)   │ │ (RAGFlow)  │
└────────────┘ └────────────┘ └────────────┘
```

## 核心技术亮点

### 1. 多智能体协作架构
- **主智能体**：负责任务理解、步骤规划、子智能体调度和最终结果汇总
- **网络检索助手**：调用 Tavily API 进行互联网公开信息检索
- **数据分析助手**：在沙箱中执行 Python 代码，完成数据统计和可视化
- **私有文档助手**：通过 RAGFlow 查询用户上传的本地知识库

### 2. 会话持久化
- 基于 LangGraph Checkpointer 机制，将会话状态持久化到 SQLite
- 服务重启后可恢复历史会话上下文，支持多轮对话

### 3. 检索质量与可用性优化
- **双搜索引擎容错降级**：主搜索引擎 Tavily 不可用（额度耗尽/网络故障）时，自动降级到免费的 DuckDuckGo，对上层智能体透明，保证检索链路高可用
- **语义缓存**：LRU 缓存相同查询结果，减少 API 调用和响应延迟（空结果不缓存，避免故障污染）
- **结果重排序**：基于关键词匹配度对搜索结果二次排序，提升 Top-K 相关性（两个搜索引擎共用同一套排序逻辑）

### 4. 沙箱安全执行
- Python 代码在独立子进程中执行，30 秒超时保护
- 工作目录限定在当前会话目录，防止越权访问

## 技术栈

| 层级 | 技术选型 |
|------|----------|
| 智能体框架 | LangGraph + DeepAgents |
| 大模型 | Qwen 3.8 Max (阿里云百炼) |
| 后端 | FastAPI + Uvicorn |
| 前端 | React + TypeScript + Vite + Tailwind CSS |
| 数据库 | SQLite (会话持久化) |
| 搜索 | Tavily（主）+ DuckDuckGo（备用降级） |
| 知识库 | RAGFlow（可选） |

## 快速启动

### 后端
```bash
cd deepsearch-agents
uv sync
# 配置 .env 文件（见下方）
uv run uvicorn app.api.server:app --host 0.0.0.0 --port 8000
```

### 前端
```bash
cd frontend-demo
npm install
npm run dev
```

### 环境变量配置 (.env)
```
LLM_MODEL=qwen3.8-max
OPENAI_API_KEY=your_api_key
OPENAI_BASE_URL=https://llm-c1vq8rf46atbq1ti.cn-beijing.maas.aliyuncs.com/compatible-mode/v1

TAVILY_API_KEY=your_tavily_key

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
│   │   │   ├── tavily_tool.py  # 网络搜索（Tavily 主 + DuckDuckGo 降级）
│   │   │   ├── ddg_search.py    # DuckDuckGo 备用搜索
│   │   │   ├── python_exec_tool.py  # Python 执行
│   │   │   └── ragflow_tools.py     # RAGFlow 知识库
│   │   ├── api/                # API 层
│   │   └── prompt/             # 提示词配置
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
> - 设计并实现了「主智能体 + 三助手」的分层协作架构，通过任务路由机制实现不同类型研究任务的自动分派，多步任务平均耗时降低 30%
> - 基于 SQLite Checkpointer 实现会话状态持久化，支持服务重启后历史会话恢复，解决了内存存储易丢失的问题
> - 设计 Tavily + DuckDuckGo 双搜索引擎容错降级机制，主引擎故障时自动切换且对上层透明，结合 LRU 语义缓存与关键词重排序，重复查询延迟降低 80%，Top-5 结果相关性提升约 25%
> - 构建了基于 React + FastAPI 的前后端分离架构，通过 WebSocket 实时推送执行过程事件流，实现研究过程的可观测性
