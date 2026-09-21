<div align="center">
  <h1>「深度研搜」对话式多智能体研究系统 · 后端</h1>
  <h4><b>deepsearch-agents</b></h4>
  <p><em>FastAPI + DeepAgents(LangGraph) 编排一主三从多智能体，支持网络检索、学术文献检索、受控 Python 数据分析与文件交付，通过 WebSocket 实时回传执行过程。</em></p>
</div>

![深度研搜前端首页](docs/images/deepsearch-agent-home.jpg)

> 本后端在开源教程 [didilili/ai-agents-from-zero](https://github.com/didilili/ai-agents-from-zero)（MIT）的实战项目基础上做了本地化与工程化改造：上游的 Tavily 付费搜索、MySQL 教学库、RAGFlow 私有知识库在本仓库均**已移除或替换**，改造清单见根目录 [CREDITS.md](../CREDITS.md)。配套前端在同级目录 `../frontend-demo/`。

## 能力概览

- **一主三从多智能体**：主智能体负责任务规划、子智能体调度与最终汇总；三个子智能体分别处理网络检索、学术文献、数据分析。
- **多来源检索**：网络侧自建 SearXNG 元搜索为主、DuckDuckGo(`ddgs`) 兜底；学术侧并发检索 arXiv / OpenAlex / Crossref 并做跨源字段级融合。
- **受控 Python 数据分析**：在独立子进程中执行代码，可现场统计、计算、出图；带超时、每任务调用上限与产物清单回传。
- **文件交付闭环**：上传附件 → 复制进会话工作区 → 工具读取 → 生成 Markdown / PDF → 前端下载。
- **会话持久化**：LangGraph SQLite 检查点，刷新页面可恢复同一会话，侧边栏展示真实历史会话与多轮问答。
- **实时可观察**：工具调用、子智能体派单、工作目录、最终结果、取消、异常都经 WebSocket 按 `thread_id` 定向推送。

## 目录与端口

```text
Agent/
├── deepsearch-agents/   # 本目录：FastAPI + DeepAgents 后端，端口 8001
├── frontend-demo/       # React 19 + Vite + TypeScript 前端，端口 5173
└── searxng/             # 可选：WSL2 Docker 部署的 SearXNG，容器端口 8888
```

## 快速启动

### 1. 配置环境变量

```bash
cp .env.example .env
```

至少填写 OpenAI 兼容端点与密钥（当前默认接 DeepSeek）：

```bash
OPENAI_BASE_URL=https://api.deepseek.com/v1
OPENAI_API_KEY=sk-你的密钥
LLM_MAIN_MODEL=deepseek-flash     # 主智能体模型
LLM_FAST_MODEL=deepseek-flash     # 子智能体模型
```

可选项：`OPENALEX_API_KEY`（避免共享出口 IP 触发 OpenAlex 429）、`ACADEMIC_CONTACT_EMAIL`
（填入后以 mailto 进入 OpenAlex/Crossref 礼貌池，留空则不发送）、SearXNG 相关变量默认即可。

### 2. 安装依赖（pip + venv）

```bash
python -m venv .venv
# Windows PowerShell
.venv\Scripts\python.exe -m pip install -r requirements.txt
# macOS / Linux
# source .venv/bin/activate && pip install -r requirements.txt
```

> 日常开发用宽松约束的 `requirements.txt`；**CI 安装的是 `requirements-lock.txt`**——它在干净
> Python 3.11 跑通全部 pytest 与离线回归后由 `pip freeze` 锁定完整传递依赖（文件头部含复现步骤），
> 保证 CI 可复现，不会因上游发版而“今天绿明天红”。

### 3.（可选）启动 SearXNG

SearXNG 不是必需依赖：未启动时后端会自动降级到 DuckDuckGo。需要更稳定的多引擎聚合时，
在 WSL Ubuntu 中进入项目根的 `searxng/` 目录执行 `bash deploy.sh`，Windows 侧通过
`http://localhost:8888` 访问；后端在「HTTP 直连」与「WSL 桥接脚本」两种通道间自动选择
（`SEARXNG_TRANSPORT=auto`）。

### 4. 启动后端（端口 8001）

```bash
# Windows PowerShell
.venv\Scripts\python.exe -m uvicorn app.api.server:app --host 0.0.0.0 --port 8001
# macOS / Linux
.venv/bin/python -m uvicorn app.api.server:app --host 0.0.0.0 --port 8001
```

接口文档：`http://localhost:8001/docs`。

### 5. 启动前端（端口 5173）

```bash
cd ../frontend-demo
npm install      # 首次
npm run dev
```

打开 `http://localhost:5173`。前端 API/WS 地址在 `src/lib/config.ts` 指向 `localhost:8001`，
也可用 `VITE_API_BASE_URL` / `VITE_WS_BASE_URL` 覆盖。

## 系统架构

![深度研搜系统架构图](docs/images/deepsearch-system-architecture.svg)

采用 Orchestrator-Workers 模式：主智能体是调度中心，三个专家助手负责信息获取，文件交付工具由主智能体直接掌握。

```text
用户任务
  -> FastAPI 接收请求（thread_id 贯穿全链路，TaskManager 保证同一会话同一时刻只有一个活跃任务）
  -> run_deep_agent 创建会话工作区、复制上传附件、写入 ContextVar、按任务重置预算
  -> 主智能体规划并分派给 网络检索 / 数据分析 / 学术文献 子智能体
  -> 子智能体调用各自工具（检索预算、缓存、熔断在此层生效）
  -> 主智能体汇总并调用文件工具生成 Markdown / PDF
  -> monitor 经 WebSocket 按 thread_id 推送过程与结果
  -> 会话检查点写入 SQLite，侧边栏历史会话可恢复
```

### 智能体与工具

| 归属 | 能力 | 工具 |
| --- | --- | --- |
| 主智能体 | 任务规划、助手调度、结果汇总、文件交付 | `read_file_content`、`generate_markdown`、`convert_md_to_pdf` |
| 网络检索助手 | 公开网页、新闻、技术动态与博客 | `internet_search`（SearXNG 为主，DuckDuckGo 兜底） |
| 数据分析助手 | 统计、计算、可视化 | `execute_python_code`（受控子进程，30s 超时，每任务 ≤12 次） |
| 学术文献助手 | 论文、研究现状、年份/引用/DOI/PDF | `academic_paper_search`（arXiv + OpenAlex + Crossref） |

## 运行时与检索治理（关键工程语义）

- **任务生命周期串行化**：同一 `thread_id` 任意时刻最多一个活跃 Runtime。新任务提交时会先
  `cancel()` 并**等待旧任务真正结束**再启动；旧任务在超时内退不出来则返回 `409 cancelling`，
  绝不并发启动第二个 Runtime 去踩踏同一会话目录与检查点。
- **取消会回收子进程**：数据分析的 Python 子进程在任务被取消时会被显式 kill 并回收管道，
  `CancelledError` 继续向上传播（不会被伪装成一次“成功的工具结果”）；每次执行使用唯一临时
  脚本，并在 `finally` 中清理，不残留垃圾文件。
- **执行契约**：`run_deep_agent` 成功时返回 `AgentRunResult(session_id, final_answer, status,
  artifacts)`；普通异常先推送 `error` 事件再抛出，取消先推送 `task_cancelled` 再抛出；
  若 graph 正常结束却没有产出任何非空最终回答（空串/纯空白），同样先推送 `error` 再抛出
  `RuntimeError("Agent finished without a final answer")`，绝不把空结果标记为 completed。
- **每任务预算（per-task reset）**：网页检索 3 次/任务、学术检索 2 次/任务、Python 执行
  12 次/任务，新一轮提问会重置；缓存命中不消耗预算。
- **查询结果 LRU 缓存（非语义缓存）**：仅对查询词做 strip/lower/合并空白后的字面精确匹配，
  key 含全部影响结果的参数；TTL 网页 20 分钟、学术 2 小时；空结果不缓存。缓存只存检索载荷，
  不携带 `search_no` / 剩余预算等任务元数据，命中时按当前任务重新装饰，避免跨任务串号。
- **熔断只反映数据源可用性**：网络错误 / 超时 / 5xx / 解析异常才计入熔断失败；HTTP 200 但
  结果为空属于“该查询无匹配”，会闭合熔断而不是误判数据源宕机。熔断器是**进程级、按数据源**
  的全局组件（不是按会话隔离）。
- **学术多源融合**：DOI 完全一致为强匹配、归一化标题一致为中匹配（不做模糊匹配以防误合并）；
  合并时引用数取 max，摘要 / PDF / DOI / 期刊 / 作者取更完整的非空字段，并同时给出
  `source` 拼接字符串与 `sources` 列表。

## 安全边界（请注意措辞准确）

- **thread_id 白名单**：`^[A-Za-z0-9_-]{1,64}$` 统一作用于 `/api/task`、`/api/upload`、
  取消、历史会话与 WebSocket 入口，防止 `../` 等路径穿越。
- **会话工作区路径边界**：Agent 的读/写文件工具（read / markdown / pdf）统一经
  `resolve_session_path` 解析，解析后必须仍位于 `output/session_{id}` 之内，拒绝多层 `../`、
  绝对路径、Windows 盘符与符号链接逃逸；文件浏览/下载接口同样限制在 `output/` 内。
- **受控 Python 子进程不是 OS 级安全沙箱**：子进程的工作目录（cwd）只决定**相对路径**在哪里
  解析，并不阻止代码 `open()` 绝对路径、读取环境变量或访问网络。真正强制会话路径边界的是上面
  那条 Agent 文件工具的 `resolve_session_path`。若要运行不可信代码，应在容器 / 微虚拟机等
  强隔离环境中执行。

## HTTP / WebSocket 接口

后端地址 `http://localhost:8001`，交互式文档见 `/docs`。

| 接口 | 说明 |
| --- | --- |
| `POST /api/task` | 启动后台任务，body `{query, thread_id?}`；同 thread 旧任务未退出时返回 409 |
| `POST /api/task/{thread_id}/cancel` | 取消任务，返回 `cancelled` / `cancelling` / 404 |
| `POST /api/upload` | multipart 上传一个或多个文件（含 `thread_id`） |
| `GET /api/files?path=` | 列出输出目录内文件（限制在 `output/` 内） |
| `GET /api/download?path=` | 下载输出目录内文件 |
| `GET /api/threads?limit=&include_test=` | 历史会话列表 |
| `GET /api/threads/{thread_id}` | 单会话多轮问答详情 |
| `WebSocket /ws/{thread_id}` | 推送 `monitor_event`（工具调用 / 子智能体 / 结果 / 取消 / 错误） |

CORS 默认放行 `http://localhost:5173` 与 `http://127.0.0.1:5173`，可用环境变量
`CORS_ORIGINS`（逗号分隔）覆盖。

## 测试与评测

```powershell
# Windows PowerShell（在 deepsearch-agents 目录）
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe benchmarks\run_benchmark.py          # 离线确定性评测（无需网络/LLM）
.venv\Scripts\python.exe benchmarks\run_e2e_benchmark.py       # 真实 HTTP+WebSocket+LLM 端到端（需先启动后端）
```

- **单元测试**：覆盖任务生命周期、子进程取消回收、Agent 执行契约、检索预算 / 缓存 / 熔断、
  学术多源融合、会话还原、thread_id 校验、路径边界与一个经真实 ASGI（HTTP+WebSocket）的
  确定性链路冒烟测试。运行 `pytest -q` 应全部通过（具体条数以当前代码与 CI 为准）。
- **离线 Benchmark**：对真实代码做确定性断言（路径边界、白名单、缓存 TTL/完整 key、每任务
  预算、检索降级/熔断），秒级完成、不触网，CI 必跑；需要 LLM 的 4 个端到端用例在离线模式记为
  skipped。**面向公众的可复现证据以 GitHub Actions 的 Backend CI 运行结果为准**；本地运行
  另写 `benchmarks/results/YYYY-MM-DD.json`（已被 `.gitignore` 忽略，不作为仓库证据）。
- **Online E2E Runtime Benchmark（在线端到端运行时基线，人工触发，不进 CI）**：
  `run_e2e_benchmark.py` 像前端一样先连 WebSocket 再 POST `/api/task`，收集事件直到终态，
  对每个用例施加确定性 expectations（工具路由 / 调用预算 / 来源 URL 或 DOI / 数值结果），
  记录成败、延迟、各类工具调用次数、最终答案、产物数，以及 git commit、真实模型、预算与运行环境；
  全部 passed 退出码 0，否则 1，连不上后端为环境错误 2。每次运行用唯一 `run_id` 隔离会话，
  不复用历史 checkpoint。普通结果写入 gitignore 的 `benchmarks/results/e2e-*.json`；达到全
  passed 时固化为可追溯的 `benchmarks/results/baseline-<git短SHA>.json` 并入库。它衡量“整条
  运行时链路在真实模型下是否按预期工作”，没有人工 ground truth，故不提供“回答准确率”。
  它与离线 Benchmark 是两套不同目的的评测，不要混为一谈；详见 `benchmarks/README.md`。

## 项目结构

```text
deepsearch-agents/
├── app/
│   ├── agent/
│   │   ├── subagents/          # 网络检索 / 数据分析 / 学术文献三个子智能体
│   │   ├── llm.py              # OpenAI 兼容模型工厂（runtime 懒加载，import 不读密钥）
│   │   ├── main_agent.py       # 主智能体组装与 run_deep_agent 执行入口
│   │   ├── result.py           # AgentRunResult 执行结果契约
│   │   └── prompts.py
│   ├── api/
│   │   ├── context.py          # ContextVar：thread_id / session_dir
│   │   ├── monitor.py          # 工具/助手/结果/异常事件 WebSocket 推送
│   │   ├── threads.py          # 历史会话列表与多轮问答还原（只读 SQLite）
│   │   └── server.py           # FastAPI 任务/上传/文件/历史/WebSocket 接口
│   ├── runtime/
│   │   └── task_manager.py     # 任务生命周期：取消并等待、同 thread 串行化
│   ├── tools/                  # 网络/学术检索、受控 Python、文件读写、Markdown、PDF
│   ├── utils/                  # resolve_session_path 路径边界、thread_id 校验等
│   ├── prompt/prompts.yml
│   ├── output/                 # 运行时生成：各会话产物（不入库）
│   └── updated/                # 运行时生成：上传文件会话暂存（不入库）
├── benchmarks/                 # 离线 + 端到端可复现评测（cases/evaluator/schemas/runner）
├── docs/images/                # 本文档引用的截图与架构图
├── tests/                      # 单元测试（不依赖外网与大模型）
├── .env.example
├── requirements.txt            # 开发用宽松依赖约束
└── requirements-lock.txt       # CI 用可复现依赖锁（干净 3.11 验证后 pip freeze 生成）
```

## 能力边界

当前版本聚焦把多智能体工程主链路跑通、跑对、可测试，**不**包含：用户登录 / 多租户权限、
上传内容安全扫描、任务队列与分布式执行、执行事件全量落库与审计、会话产物自动清理、
生产监控告警与灰度发布、以及不可信代码的强隔离执行（容器 / 微虚拟机）。这些适合在主链路稳定
后继续扩展。
