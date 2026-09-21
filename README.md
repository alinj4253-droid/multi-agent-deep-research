# 多智能体深度研究助手系统

> 基于 LangGraph DeepAgents 框架构建的多智能体协作研究系统，面向学术研究场景，支持**网络检索、数据分析、学术文献检索**三类任务，通过 WebSocket 实时展示多智能体执行过程。

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
│ SearXNG    │ │ 受控 Python │ │ arXiv/OpenAlex/    │
│ (+ddgs降级) │ │ 子进程执行  │ │ Crossref 三源检索   │
└────────────┘ └────────────┘ └────────────────────┘
```

## 三个组成部分

| 目录 | 角色 | 技术 | 端口 |
| --- | --- | --- | --- |
| `deepsearch-agents/` | 后端 | FastAPI + DeepAgents + LangGraph(SQLite) | 8001 |
| `frontend-demo/` | 前端 | React 19 + TypeScript + Vite + Tailwind | 5173 |
| `searxng/` | 自建元搜索 | WSL2 Docker 部署 SearXNG（免费聚合多引擎） | 8888 |

## 核心技术亮点

### 1. 多智能体协作架构
- **主智能体**：负责任务理解、步骤规划、子智能体调度和最终结果汇总
- **网络检索助手**：以**自建 SearXNG 元搜索**（WSL2 Docker，引擎可在 settings.yml 配置，当前启用 Bing/Yandex/搜狗/360 等）为主、免费 `ddgs` 为降级，内置检索次数预算、查询结果 LRU 缓存与结果重排序
- **数据分析助手**：在**受控 Python 子进程**中执行代码，完成数据统计与可视化（numpy/pandas/matplotlib/scipy）
- **学术文献助手**：并发检索 **arXiv + OpenAlex + Crossref**，跨源去重、按引用/年份排序，返回作者/年份/引用/DOI/链接

### 2. 会话持久化
- 基于 LangGraph Checkpointer，将会话状态持久化到 **SQLite**
- 服务重启后可恢复历史会话上下文；前端 thread_id 存 localStorage，刷新页面可恢复同一会话
- 历史会话列表由后端 `/api/threads` 读取真实 SQLite，前端侧边栏展示

### 3. 检索链路治理：自建免费引擎 + 调用预算 + 相关性优化
- **零成本检索**：主用本地自建的 SearXNG（无需付费 Key），DuckDuckGo(`ddgs`) 作为降级；学术侧用 arXiv/OpenAlex/Crossref 免费 API
- **检索次数硬预算（Tool-level Budget，按任务）**：工具层对"实际对外检索"计数并设上限（网页 3 次/任务、学术 2 次/任务），超限即短路返回，从机制上杜绝反复换关键词的调用循环
- **调用纪律双层约束**：主智能体提示词限定"同一助手单次任务只派单一次"，网络助手提示词限定检索角度数，形成"软约束 + 硬兜底"
- **查询结果 LRU 缓存**：字面归一（大小写/空白）+ TTL（网页 20 分钟、学术 2 小时），缓存 key 含 query/region/max_results 等全部影响结果的参数，命中直接返回且不消耗预算，空结果不缓存。注意这是字面精确匹配，不是语义/向量缓存
- **结果重排序**：基于关键词在标题/摘要的命中度二次排序，提升 Top-K 相关性
- **熔断与传输自适应**：只有网络错误 / 超时 / 5xx / 解析异常才计入熔断失败，HTTP 200 但结果为空视为"该查询无匹配"并闭合熔断（不误判数据源宕机）；熔断器是进程级、按数据源的全局组件。Windows 侧自动在"HTTP 直连"与"WSL 桥接脚本"两种通道间选择

### 4. 受控子进程执行与防循环
- Python 代码在独立子进程中执行，**30 秒超时**；每次执行使用唯一临时脚本，任务取消时会显式 kill 并回收子进程，结束后清理临时文件
- 每次执行回传【工作目录】与【本次产物】文件清单，模型无需自行探测文件是否落盘
- **每任务调用硬上限 12 次**（每轮任务开始时重置，防止单轮失控，而非永久限制整个历史会话），配合"一次成型、出错才重试"的提示词，修复了数据分析反复探测/重复出图的不终止循环
- 注意：子进程的工作目录（cwd）只决定相对路径在哪里解析，**不是 OS 级安全沙箱**，不阻止代码打开绝对路径、读环境变量或访问网络；真正强制会话路径边界的是下面第 5 点 Agent 文件工具的 `resolve_session_path`

### 5. 安全边界
- **thread_id 白名单校验**（`^[A-Za-z0-9_-]{1,64}$`）统一作用于 `/api/task`、`/api/upload`、取消、历史会话与 WebSocket 入口
- **会话工作区路径边界**：Agent 读/写文件经 `resolve_session_path` 强制解析后必须仍在 `output/session_{id}` 之内，拒绝 `../`、绝对路径、Windows 盘符与符号链接逃逸；文件浏览/下载接口同样限制在 `output/` 内
- **任务生命周期串行化**：同一会话同一时刻只允许一个活跃任务，新任务会先取消并等待旧任务真正结束，旧任务超时退不出则返回 409，避免并发踩踏同一会话目录与检查点

## 技术栈

| 层级 | 技术选型 |
|------|----------|
| 智能体框架 | LangGraph + DeepAgents |
| 大模型 | DeepSeek（OpenAI 兼容接口；可在 `.env` 切换任意 OpenAI 兼容服务） |
| 后端 | FastAPI + Uvicorn |
| 前端 | React 19 + TypeScript + Vite + Tailwind CSS |
| 会话存储 | SQLite（LangGraph 检查点） |
| 网络搜索 | 自建 SearXNG（WSL2 Docker）为主 + DuckDuckGo(ddgs) 降级 + 检索预算/缓存/熔断 |
| 学术搜索 | arXiv + OpenAlex + Crossref（免费，OpenAlex 可选配免费 Key） |
| 数据分析 | 受控子进程：numpy / pandas / matplotlib / scipy / openpyxl / Pillow |

## 快速启动

### 1. 启动 SearXNG（WSL2 + Docker）
```bash
# 在 WSL Ubuntu 内，进入项目 searxng/ 目录后执行
cd searxng
bash deploy.sh            # 首次部署 / 改完 settings.yml 后重建
```
Windows 侧通过 `http://localhost:8888` 访问。长时间挂机演示可用 `keep_wsl_alive.sh` 防止 WSL 空闲关停。

> 国内网络下默认启用 Bing(cn)/Yandex/搜狗/360，禁用被 DNS 污染或触发验证码的源；配好出网代理后可在 `settings.yml` 重新启用。

### 2. 后端（端口 8001）
```bash
cd deepsearch-agents
python -m venv .venv
# Windows: .venv\Scripts\activate   |   macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
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

# SearXNG（默认值即可，一般无需修改）
SEARXNG_URL=http://localhost:8888
WSL_DISTRO=Ubuntu-20.04
SEARXNG_TRANSPORT=auto
```

## 测试与回归

```bash
cd deepsearch-agents
.venv\Scripts\python.exe -m pytest -q                              # 单元测试（以 CI 全绿为准）
.venv\Scripts\python.exe benchmarks\run_benchmark.py              # 离线可复现评测（无需网络/LLM）
.venv\Scripts\python.exe benchmarks\run_e2e_benchmark.py          # 真实端到端（需先启动后端 + LLM 凭据）
```

- **单元测试**：覆盖任务生命周期串行化、子进程取消回收、Agent 执行契约、检索治理（预算/缓存/熔断）、学术多源融合、会话还原、接口层、安全校验与路径边界，另含一个经真实 ASGI（HTTP+WebSocket）的确定性链路冒烟。
- **离线 Benchmark**：6 类共 24 例，对真实代码做确定性断言（路径边界、thread_id 白名单、缓存 TTL 与完整 key、每任务预算、检索降级与熔断）。最近一次离线运行 **20/20 用例通过**，4 个需 LLM 的端到端用例在离线模式记为 skipped（以 `benchmarks/results/*.json` 为准，详见 `benchmarks/README.md`）。
- **端到端 Benchmark**：`run_e2e_benchmark.py` 走真实 HTTP+WebSocket+LLM，记录逐用例成败/延迟/工具调用/答案/产物数与 git SHA、模型、预算配置，与离线评测是两套不同目的的评测。
- **CI**：`.github/workflows/ci.yml` 在 push/PR 时自动跑后端 pytest + 离线 benchmark 与前端 lint + build。

## 目录结构

```
.
├── deepsearch-agents/          # 后端项目
│   ├── app/
│   │   ├── agent/              # 智能体定义（主智能体 + 网络/数据/学术子智能体）
│   │   │   ├── subagents/
│   │   │   ├── prompts.py      # 提示词加载
│   │   │   ├── result.py       # AgentRunResult 执行结果契约
│   │   │   └── main_agent.py   # 主智能体与 run_deep_agent
│   │   ├── tools/              # 检索/学术/Python执行/文件读写/Markdown/PDF 工具
│   │   ├── api/                # FastAPI + WebSocket + 文件上传下载 + 历史会话
│   │   ├── runtime/            # TaskManager：任务取消并等待、同会话串行化
│   │   ├── utils/             # 路径边界 resolve_session_path、thread_id 校验、Markdown/PDF 转换
│   │   ├── prompt/             # 提示词配置 prompts.yml
│   │   ├── checkpoints.db      # 会话持久化数据库（运行时生成）
│   │   ├── output/            # 运行时生成：各会话产物
│   │   └── updated/            # 运行时生成：上传文件会话暂存
│   ├── benchmarks/             # 离线 + 端到端可复现评测（cases/evaluator/schemas/runner）
│   ├── tests/                  # 单元测试
│   └── requirements.txt
├── frontend-demo/              # 前端项目（React 19 + Vite + TS + Tailwind）
├── searxng/                    # 自建 SearXNG（docker-compose、settings、部署脚本）
├── .github/workflows/ci.yml     # CI（后端测试 + 前端 lint/build）
├── LICENSE                     # MIT（承继上游 didilili/ai-agents-from-zero）
├── CREDITS.md                  # 致谢与改造说明
└── README.md
```

## 致谢

本项目基于开源教程 [didilili/ai-agents-from-zero](https://github.com/didilili/ai-agents-from-zero)（MIT）进行本地化与工程化改造，详见 [CREDITS.md](./CREDITS.md)。
