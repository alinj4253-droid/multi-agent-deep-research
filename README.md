# 多智能体深度研究助手系统

> 基于 LangGraph DeepAgents 框架构建的多智能体协作研究系统，面向学术研究场景，支持**网络检索、数据分析、学术文献检索**三类任务，并通过 WebSocket 实时展示多智能体执行过程。

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
┌────────────┐ ┌────────────┐ ┌────────────────────┐
│ 网络检索    │ │ 数据分析    │ │ 学术文献            │
│ 助手        │ │ 助手        │ │ 助手                │
│ SearXNG    │ │ Python沙箱  │ │ arXiv/OpenAlex/    │
│ (+ddgs降级) │ │ 统计/可视化 │ │ Crossref 三源检索   │
└────────────┘ └────────────┘ └────────────────────┘
```

> 说明：早期版本的“私有文档 / RAGFlow 助手”相关代码保留在仓库中（`app/ragflow/`、
> `knowledge_demo.py`），但**当前主链路未接线**；第三个在线助手为学术文献助手。

## 三个组成部分

| 目录 | 角色 | 技术 | 端口 |
| --- | --- | --- | --- |
| `deepsearch-agents/` | 后端 | FastAPI + DeepAgents + LangGraph(SQLite) | 8001 |
| `frontend-demo/` | 前端 | React 19 + TypeScript + Vite + Tailwind | 5173 |
| `searxng/` | 自建元搜索 | WSL2 Docker 部署 SearXNG（免费聚合多引擎） | 8888 |

## 核心技术亮点

### 1. 多智能体协作架构
- **主智能体**：负责任务理解、步骤规划、子智能体调度和最终结果汇总
- **网络检索助手**：以**自建 SearXNG 元搜索**（WSL2 Docker，聚合 Bing/Yandex/搜狗/360 等）为主、免费 `ddgs` 为降级，内置检索次数预算、语义缓存与结果重排序
- **数据分析助手**：在子进程沙箱中执行 Python，完成数据统计与可视化（numpy/pandas/matplotlib/scipy）
- **学术文献助手**：并发检索 **arXiv + OpenAlex + Crossref**，跨源去重、按引用/年份排序，返回作者/年份/引用/DOI/链接

### 2. 会话持久化
- 基于 LangGraph Checkpointer，将会话状态持久化到 **SQLite**
- 服务重启后可恢复历史会话上下文；前端 thread_id 存 localStorage，刷新页面可恢复同一会话

### 3. 检索链路治理：自建免费引擎 + 调用预算 + 相关性优化
- **零成本检索**：主用本地自建的 SearXNG（聚合多个免费搜索引擎、无需付费 Key），DuckDuckGo(`ddgs`) 作为降级；学术侧用 arXiv/OpenAlex/Crossref 免费 API
- **检索次数硬预算（Tool-level Budget）**：工具层按会话对“实际对外检索”计数并设上限（默认 3 次），超限即短路返回，从机制上杜绝反复换关键词的调用循环
- **调用纪律双层约束**：主智能体提示词限定“同一助手单次任务只派单一次”，网络助手提示词限定检索角度数，形成“软约束 + 硬兜底”
- **语义缓存**：标准化查询后 LRU 缓存，命中直接返回且不消耗预算，空结果不缓存
- **结果重排序**：基于关键词在标题/摘要的命中度二次排序，提升 Top-K 相关性
- **熔断与传输自适应**：连续失败触发熔断器；Windows 侧自动在“HTTP 直连”与“WSL 桥接脚本”两种通道间选择

### 4. 沙箱安全执行与防循环
- Python 代码在独立子进程中执行，**30 秒超时**，工作目录限定在当前会话目录
- 每次执行回传【工作目录】与【本次产物】文件清单，模型无需自行探测文件是否落盘
- **每会话调用硬上限 12 次**（按 thread_id 计数，超限强制收敛），配合“一次成型、出错才重试”的提示词，修复了数据分析反复探测/重复出图的不终止循环

## 技术栈

| 层级 | 技术选型 |
|------|----------|
| 智能体框架 | LangGraph + DeepAgents |
| 大模型 | DeepSeek（OpenAI 兼容接口，模型 `deepseek-flash`；可在 `.env` 切换任意 OpenAI 兼容服务） |
| 后端 | FastAPI + Uvicorn |
| 前端 | React 19 + TypeScript + Vite + Tailwind CSS |
| 会话存储 | SQLite（LangGraph 检查点） |
| 网络搜索 | 自建 SearXNG（WSL2 Docker）为主 + DuckDuckGo(ddgs) 降级 + 检索预算/缓存/熔断 |
| 学术搜索 | arXiv + OpenAlex + Crossref（免费，OpenAlex 可选配免费 Key） |
| 数据分析 | 子进程沙箱：numpy / pandas / matplotlib / scipy / openpyxl / Pillow |
| 知识库 | RAGFlow（可选，当前未接线） |

## 快速启动

### 1. 启动 SearXNG（WSL2 + Docker）
```bash
# 在 WSL Ubuntu 内，进入项目 searxng/ 目录后执行
cd searxng
bash deploy.sh            # 首次部署 / 改完 settings.yml 后重建
```
Windows 侧通过 `http://localhost:8888` 访问。长时间挂机演示可用
`keep_wsl_alive.sh` 防止 WSL 空闲关停（用法见 `deepsearch-agents/README.md`）。

> 国内网络下默认启用 Bing(cn)/Yandex/搜狗/360，禁用被 DNS 污染或触发验证码的
> Google/DDG/Brave/Wikipedia/百度；配好出网代理后可在 `settings.yml` 重新启用。

### 2. 后端（端口 8001）
```bash
cd deepsearch-agents
python -m venv .venv
# Windows: .venv\Scripts\activate   |   macOS/Linux: source .venv/bin/activate
pip install -e .          # 或 pip install -r requirements.txt
cp .env.example .env      # 按下方说明填写
python -m uvicorn app.api.server:app --host 0.0.0.0 --port 8001
```
接口文档：`http://localhost:8001/docs`。

### 3. 前端（端口 5173）
```bash
cd frontend-demo
npm install
npm run dev               # 打开 http://localhost:5173
```

### 环境变量配置 (deepsearch-agents/.env)
```
# 大模型（OpenAI 兼容接口；当前使用 DeepSeek）
OPENAI_BASE_URL=https://api.deepseek.com/v1
OPENAI_API_KEY=sk-你的密钥
LLM_MAIN_MODEL=deepseek-flash
LLM_FAST_MODEL=deepseek-flash

# 学术检索：OpenAlex 免费 Key（可选，避免共享 IP 触发 429）
OPENALEX_API_KEY=

# SearXNG（默认值即可，一般无需修改；WSL 桥接脚本默认自动定位到项目根 searxng/ 下）
SEARXNG_URL=http://localhost:8888
WSL_DISTRO=Ubuntu-20.04
# SEARXNG_WSL_SCRIPT=D:/path/to/your/repo/searxng/wsl_query.sh   # 仅自定义位置时需要
SEARXNG_TRANSPORT=auto

# 私有文档助手为可选项：不配置 RAGFlow 时该助手以空壳方式占位（当前主链路未接线）
RAGFLOW_API_KEY=
RAGFLOW_API_URL=
```

## 测试与回归
```bash
cd deepsearch-agents
.venv\Scripts\python.exe -m pytest -q          # 单元测试
# 端到端（需先启动后端与 SearXNG），走真实 HTTP + WebSocket：
.venv\Scripts\python.exe scripts\e2e_run.py 常识直答 "用一句话解释什么是光合作用"
.venv\Scripts\python.exe scripts\e2e_run.py 网络检索 "检索 Python 3.13 的主要新特性，带来源链接"
.venv\Scripts\python.exe scripts\e2e_run.py 数据分析 "用 Python 生成正态分布随机数并画直方图保存为 PNG"
.venv\Scripts\python.exe scripts\e2e_run.py 学术文献 "检索 4D Gaussian Splatting 的代表性论文"
```

## 目录结构

```
.
├── deepsearch-agents/          # 后端项目
│   ├── app/
│   │   ├── agent/              # 智能体定义（主智能体 + 网络/数据/学术子智能体）
│   │   │   ├── subagents/
│   │   │   ├── prompts.py      # 提示词加载
│   │   │   └── main_agent.py   # 主智能体与 run_deep_agent
│   │   ├── tools/
│   │   │   ├── searxng_search.py        # SearXNG 检索（HTTP/WSL 双通道）
│   │   │   ├── ddg_search.py            # DuckDuckGo 降级检索
│   │   │   ├── web_search_tool.py       # 网络搜索工具（预算 + 缓存 + 熔断 + 重排）
│   │   │   ├── academic_sources.py      # arXiv/OpenAlex/Crossref 三源
│   │   │   ├── academic_search_tool.py  # 学术检索工具（支持 year_from 时效限定）
│   │   │   ├── python_exec_tool.py      # Python 沙箱（超时/产物清单/调用上限）
│   │   │   ├── upload_file_read_tool.py # 上传附件读取（md/docx/pdf/xlsx/csv）
│   │   │   └── ragflow_tools.py         # RAGFlow 知识库（可选，延迟导入，未接线）
│   │   ├── api/                # API 层（FastAPI + WebSocket + 文件上传下载）
│   │   │   ├── server.py       # 任务/取消/上传/文件/下载/历史会话/WebSocket 接口
│   │   │   ├── threads.py      # 历史会话列表与多轮问答还原（只读 SQLite）
│   │   │   ├── monitor.py      # 事件推送（tool/assistant/result/error）
│   │   │   └── context.py      # ContextVar 保存 thread_id 与 session_dir
│   │   ├── ragflow/            # RAGFlow 配置与示例（可选能力）
│   │   ├── utils/              # 路径解析、Markdown/PDF 转换
│   │   ├── prompt/             # 提示词配置 prompts.yml
│   │   ├── checkpoints.db      # 会话持久化数据库（AsyncSqliteSaver）
│   │   ├── output/             # 运行时生成：各会话产物
│   │   └── updated/            # 运行时生成：上传文件会话暂存
│   ├── docs/images/            # README 引用的截图与架构图
│   ├── scripts/e2e_run.py      # 端到端回归脚本
│   ├── tests/                  # 单元测试（检索治理/学术源/会话还原/接口层）
│   ├── pyproject.toml
│   └── requirements.txt
├── frontend-demo/              # 前端项目（React 19 + Vite + TS + Tailwind）
│   └── src/（App.tsx 多轮对话与历史侧边栏、hooks/ WebSocket hook、lib/ API 封装、types.ts）
├── searxng/                    # 自建 SearXNG（docker-compose、settings、部署/保活脚本）
├── 改造方案.md                  # 详细改造设计文档（含落地修订记录）
├── 审查记录与待决策问题.md        # 第一轮全量审查记录
├── 第二轮修复记录与待决策问题.md  # 第二轮修复记录（6 项反馈问题 + 清理）
├── 项目总结报告.md               # 系统架构与完整流程逻辑链
└── README.md
```

## 简历项目描述示例

> **多智能体深度研究助手系统** | 个人项目
>
> 基于 LangGraph DeepAgents 框架设计并实现了一个多助手协作的深度研究系统，支持网络检索、数据分析与学术文献检索。
>
> - 设计并实现了「主智能体 + 多专家助手」的分层协作架构与任务路由，常识问题直接作答、专业任务自动分派到对应专家助手
> - 基于 SQLite Async Checkpointer 实现会话状态异步持久化，支持服务重启后的历史会话恢复，解决了内存存储易丢失、同步 Saver 不支持异步流式输出的问题
> - 针对多智能体检索易陷入“反复搜索”循环的问题，在工具层设计按会话计数的检索次数硬预算（Tool-level Budget）与熔断器，配合主/子智能体双层提示词纪律，将单次任务网络搜索从 15+ 次收敛到 3 次以内；并以本地自建 SearXNG 元搜索（WSL2/Docker 聚合多免费引擎）+ DuckDuckGo 降级 + LRU 语义缓存 + 关键词重排，在零检索成本下保证结果相关性与可用性
> - 接入 arXiv/OpenAlex/Crossref 三源并发学术检索，实现跨源去重与按引用/年份排序
> - 为数据分析助手实现基于子进程的 Python 沙箱（30 秒超时、会话目录隔离），并通过“产物清单回传 + 每会话调用硬上限 + 一次成型提示词”修复了 Agent 反复探测、重复出图的不终止循环
> - 构建基于 React + FastAPI 的前后端分离架构，通过 WebSocket 实时推送“子智能体调用 / 工具执行 / 结果产出”事件流，并支持图表等产物的在线下载，实现多智能体研究过程的可观测性
