# Multi-Agent Deep Research —— 项目最终总结（Final Project Summary）

> 本文是项目收尾时的**单一权威说明**，供新接手者快速了解：项目目标、最终架构、如何运行、
> 如何验证、数据与费用边界、已知限制以及仓库的隐私边界。各子目录 README 是细节补充，
> 如与早期过程文档冲突，以本文与代码 / CI 的实际状态为准。
>
> 最近一次收尾提交对应的运行时基线见文末「验证结果」。

---

## 1. 项目目标与最终架构

构建一个**多智能体深度研究（Deep Research）系统**：用户用自然语言提问，系统自动完成
公开网络检索、学术文献检索、受控 Python 数据分析、多源交叉核对，并产出带来源的
Markdown / PDF 报告，支持多轮会话、任务取消、历史会话恢复与文件产物下载。

### 架构总览

```
浏览器（React SPA, :5173）
   │  REST（/api/task、/api/threads、/api/upload、/api/files…）
   │  WebSocket（/ws/{thread_id}：task_start / tool / task_result / error / task_cancelled）
   ▼
FastAPI + Uvicorn 后端（:8001）
   │
   ├── 主智能体 MainAgent（DeepAgents + LangGraph，规划 / 路由 / 汇总，持有文档工具）
   │      └── LangGraph Checkpointer → SQLite（app/checkpoints.db，按 thread_id 持久化）
   ├── 网络检索子智能体 ── 工具：internet_search
   ├── 学术文献子智能体 ── 工具：academic_paper_search
   └── 数据分析子智能体 ── 工具：execute_python_code（独立子进程）
                │
                ├─ 网络：自建 SearXNG（:8888，WSL2 Docker）为主 → DuckDuckGo(ddgs) 兜底
                ├─ 学术：arXiv + OpenAlex + Crossref（免费官方 API）
                └─ 计算：本地受控 Python 子进程（numpy/pandas/matplotlib/scipy/openpyxl/Pillow）
```

- **编排模型**：1 个主智能体 + 3 个专职子智能体；主智能体在预算内可扇出多个专家
  （例如研究类问题同时派网络与学术），子智能体多轮 ReAct 后回传，主智能体统一汇总。
- **模型**：统一接入 DeepSeek 的 OpenAI 兼容接口（默认主 / 子均为 `deepseek-flash`），
  可在 `.env` 切换任意 OpenAI 兼容服务。**模型在服务启动（lifespan）时才懒加载创建，
  import 阶段不读取密钥、不初始化客户端**（见 `app/agent/llm.py`）。
- **会话与并发**：同一会话同一时刻只允许一个活跃任务，新任务先取消并等待旧任务结束；
  thread_id 贯穿 LangGraph 检查点、任务编排、检索预算与文件工作区。
- **前端**：React 19 + TypeScript + Vite + Tailwind CSS，Markdown 渲染 + WebSocket 实时事件。

### 关键工程机制

- **检索治理**：每任务硬预算（网页 3 次 / 学术 2 次，缓存命中不消耗）；进程级、按数据源的
  熔断器（仅网络错误 / 超时 / 5xx / 解析异常计数，HTTP 200 空结果不误报宕机）；字面精确
  匹配的 LRU 缓存（网页 TTL 20 分钟、学术 2 小时，空结果不缓存，非语义 / 向量缓存）；
  标题摘要命中度重排序。
- **受控计算**：Python 在独立子进程执行，30 秒超时、每任务最多 12 次、唯一临时脚本、
  取消即 kill 并回收、`finally` 清理；回传工作目录与产物清单。
- **安全边界**：thread_id 白名单 `^[A-Za-z0-9_-]{1,64}$`；Agent 读写文件经
  `resolve_session_path` 强制限定在 `output/session_{id}` 内（拒绝 `../`、绝对路径、盘符、
  符号链接逃逸）；文件浏览 / 下载仅限 `output/`；上传文件名去路径穿越。
- **执行契约**：`run_deep_agent` 成功返回 `AgentRunResult`；普通异常与「graph 正常结束却
  没有非空最终回答（空串 / 纯空白）」都会先推送 `error` 事件再抛出，取消推送
  `task_cancelled` 后传播 `CancelledError`——空结果绝不会被误标为 completed。

---

## 2. 最终文件结构（仓库跟踪范围）

```
Agent/
├── README.md                     # 项目门面：特性、技术栈、快速启动、测试、目录
├── LICENSE / CREDITS.md / AGENTS.md
├── .github/workflows/ci.yml      # CI：后端 pytest + 离线回归；前端 lint + build
├── searxng/                      # 自建 SearXNG（仅这部分容器化）
│   ├── docker-compose.yml        #   端口 8888:8080，WSL2 部署脚本 + settings.yml
│   ├── settings.yml / deploy.sh / start_wait.sh / keep_wsl_alive.sh / 桥接脚本
├── frontend-demo/                # React + TS + Vite 前端
│   ├── src/（api/ ws/ components/ pages/）、package.json、vite.config.ts、oxlint…
│   └── package-lock.json         # 前端依赖锁（CI 用 npm ci）
└── deepsearch-agents/            # 后端（FastAPI + DeepAgents/LangGraph）
    ├── app/
    │   ├── api/      # server.py（REST+WS、lifespan）、task_manager、monitor、context、threads、security
    │   ├── agent/    # main_agent、llm（懒加载）、result、prompts、subagents/（3 个子智能体）
    │   ├── tools/    # web_search / academic_search / python_exec / markdown / pdf / upload_file_read …
    │   └── utils/    # 路径安全、校验等
    ├── benchmarks/   # cases.json、run_benchmark.py（离线）、run_e2e_benchmark.py（在线）、
    │   │             #   e2e_client、evaluator、schemas、runtime_config、results/
    │   └── results/  # 仅 baseline-<commit>.json 入库；e2e-*.json / YYYY-MM-DD.json 均 gitignore
    ├── tests/        # 18 个测试文件，pytest 共 242 项（含参数化），不依赖外网 / LLM
    ├── docs/images/  # 架构图与界面截图
    ├── requirements.txt          # 开发用宽松依赖约束
    ├── requirements-lock.txt     # CI 用可复现依赖锁（干净 Python 3.11 验证后 pip freeze）
    └── .env.example               # 环境变量模板（真实 .env 不入库）
```

> 运行时目录 `app/output/`、`app/updated/`、`app/checkpoints.db*`、各类日志、`.venv*/`、
> 根目录的内部过程记录 `.md` 均被 `.gitignore` 忽略，不属于交付物。

---

## 3. 如何运行

### 3.1 后端（pip + venv，Python 3.11）

```bash
cd deepsearch-agents
python -m venv .venv
# Windows
.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env            # 填入 OPENAI_API_KEY（DeepSeek）等
.venv\Scripts\python.exe -m uvicorn app.api.server:app --reload --host 0.0.0.0 --port 8001
# macOS / Linux：source .venv/bin/activate 后 pip install -r requirements.txt
```

- 日常开发用宽松的 `requirements.txt`；**CI 安装 `requirements-lock.txt`**（在干净
  Python 3.11 跑通 pytest + 离线回归后由 `pip freeze` 锁定，文件头含复现步骤）。
- `.env` 关键项：`OPENAI_API_KEY`、`OPENAI_BASE_URL`、`LLM_MAIN_MODEL`、`LLM_FAST_MODEL`、
  `SEARXNG_URL` / `WSL_DISTRO` / `SEARXNG_TRANSPORT`，以及可选的 `OPENALEX_API_KEY`、
  `ACADEMIC_CONTACT_EMAIL`。RAGFlow 相关变量当前为空占位，未接入。

### 3.2 前端（Node 20）

```bash
cd frontend-demo
npm install
npm run dev        # http://localhost:5173，已配置 /api 与 /ws 代理到 :8001
```

### 3.3 SearXNG（可选，仅检索增强；不启动会自动降级 DuckDuckGo）

在 WSL2 Ubuntu 内进入 `searxng/` 执行 `bash deploy.sh`，Windows 侧经
`http://localhost:8888` 访问；`start_wait.sh` 做就绪检查，`keep_wsl_alive.sh` 防止 WSL
空闲关停。**仓库只容器化 SearXNG，没有后端镜像 / 根级 compose**；后端始终以 venv + uvicorn 运行。

---

## 4. 测试与验证（如何复现）

| 验证项 | 命令（在 `deepsearch-agents/`） | 性质 | 最近结果 |
|---|---|---|---|
| 单元 / 契约测试 | `python -m pytest -q` | 离线、确定性、不触网不调 LLM、无需密钥 | **242 passed** |
| 离线工程回归 | `python benchmarks/run_benchmark.py` | 离线确定性断言，CI 必跑 | **20 pass / 0 fail / 4 skip**，退出码 0 |
| 在线端到端 | `python benchmarks/run_e2e_benchmark.py`（需先启动后端 + 配好凭据） | 真实 HTTP+WS+LLM+检索，**人工触发，不进 CI** | **4/4 passed**，见 `results/baseline-c8f6a24.json` |
| 前端 | `npm run lint`、`npm run build` | CI 自动执行 | 以 GitHub Actions 为准 |

- **CI**（`.github/workflows/ci.yml`）：后端在干净 Python 3.11 安装
  `requirements-lock.txt` → `pytest` → 离线 benchmark；前端 Node 20 `npm ci` → oxlint → build。
  push / PR 到 `main` 触发。**离线回归面向公众的可复现证据以 CI 运行结果为准**；本地
  `results/YYYY-MM-DD.json` 已 gitignore，不作为仓库证据。
- **在线 E2E** 衡量“整条运行时链路在真实模型下是否按预期工作”，逐用例校验工具路由 /
  调用预算 / 来源 URL 或 DOI / 数值结果，记录延迟、工具次数、答案、产物与 git SHA、模型、
  预算；**唯一入库的真实运行快照是 `results/baseline-<commit>.json`**，不提供“回答准确率”，
  不得为凑 100% 删改用例或篡改结果。
- 本轮收尾在**干净 Python 3.11.9** 环境（与 CI 同版本）完整复现：安装锁文件 → pytest
  242 通过 → 离线回归 20/20；并在含运行时改动的提交上重跑在线 E2E，4/4 通过后原样固化新基线。

---

## 5. 数据源、费用与免费 / 付费边界

- **唯一需要付费的是 LLM**：DeepSeek（OpenAI 兼容），按 token 计费；密钥仅存在本地
  `.env`，绝不入库、不写入 benchmark 结果（`runtime_config.py` 显式剔除）。
- **网络检索零成本**：自建 SearXNG（免费、自托管、聚合 Bing/Yandex/搜狗/360 等）为主，
  DuckDuckGo（`ddgs` 非官方包）为兜底，均无需付费 Key。
- **学术检索零成本**：arXiv、OpenAlex、Crossref 均为免费官方 API；`OPENALEX_API_KEY`
  与联系邮箱仅用于避免共享 IP 触发 429 / 进入 polite pool，属可选项。
- **数据分析零外部成本**：本地子进程使用随依赖安装的开源科学计算栈。
- 文档生成（Markdown / PDF）全部本地完成，无外部付费服务。

---

## 6. 已知限制、未完成项与边界

- **无后端容器镜像 / 根级 docker compose**：仅 SearXNG 容器化；后端定位本地 / 单机部署，
  用 venv + uvicorn 运行，CORS 默认仅放行本机 5173 调试源。
- **Python 子进程不是 OS 级安全沙箱**：它不阻止代码打开绝对路径、读环境变量或访问网络；
  真正强制会话路径边界的是 Agent 文件工具的 `resolve_session_path`。不要把它当作不可信代码
  的强隔离沙箱。
- **在线 E2E 不进 CI**：依赖真实模型与外部检索源、消耗 token、结果允许波动，只能人工触发；
  它没有人工 ground truth，不回答“准确率”，只验证链路契约与可观测行为。
- **检索缓存是字面精确匹配（LRU），不是语义 / 向量缓存**；换近义词不命中。
- **锁文件在 Windows + Python 3.11 生成**：未包含仅 Unix 的 `uvloop`（uvicorn 自动回退
  asyncio 事件循环，不影响测试与运行）；其余包均提供 manylinux cp311 wheel，CI 在 Ubuntu
  3.11 实测可复现。
- **SearXNG 在 Windows 上依赖 WSL2 Docker**；未启动时后端降级到 DuckDuckGo，源覆盖与
  稳定性弱于自建元搜索。
- **RAGFlow 占位变量未接入**；`.env` 中相关字段当前为空。

---

## 7. 仓库隐私与忽略边界（什么不会被上传）

- `.env`（DeepSeek / OpenAlex 密钥、联系邮箱）被 `.gitignore` 忽略且从未入库；已对全部
  跟踪文件做密钥 / 邮箱扫描，仅测试中的假密钥（`sk-…DEADBEEF…`，用于断言密钥不外泄）与
  `example.com` 占位邮箱。
- 运行时产物 `app/output/`、`app/updated/`、`app/checkpoints.db*`、`*.log`、各 `.venv*/`、
  `node_modules/`、`dist/` 均不入库。
- benchmark 结果目录只放行 `baseline-<commit>.json`；`e2e-*.json`、`YYYY-MM-DD.json` 一律
  gitignore；结果元数据由 `runtime_config.collect_metadata()` 生成并剔除密钥，
  `tests/test_benchmark_metadata.py` 对该不变量做回归。
- `searxng/settings.yml` 中仅保留占位 `secret_key`（`CHANGE_ME_…`），无真实密钥。
- 根目录的内部过程记录 `.md`（评审 / 计划 / 复盘）被忽略，不作为公开交付文档。

---

## 8. GitHub 同步状态

- 远端：`origin` → GitHub `main` 分支；CI 工作流见 `.github/workflows/ci.yml`。
- 本轮收尾按问题拆分为独立提交（LLM 懒加载修复 CI、空最终答案契约、依赖锁与 CI 切换、
  文档证据口径、在线基线重发），每个提交都在干净 Python 3.11 验证通过后再提交。
- 推送后以 GitHub Actions 上 **Backend（pytest + offline benchmark）** 与
  **Frontend（lint + build）** 两个 job 全绿为准；在线 E2E 不参与 CI，其证据是入库的
  `results/baseline-c8f6a24.json`。
