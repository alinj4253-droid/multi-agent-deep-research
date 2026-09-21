# Benchmark：可复现评测

本目录包含**两套目的不同**的评测，二者不要混为一谈：

1. **离线工程正确性评测**（`run_benchmark.py`）：对真实代码做确定性断言，秒级完成，
   不依赖网络与 LLM，CI 必跑。
2. **Online E2E Runtime Benchmark（在线端到端运行时基线，`run_e2e_benchmark.py`）**：
   像前端一样先连 WebSocket 再 POST `/api/task`，走真实 HTTP + WebSocket + LLM + 检索源，
   记录每个用例的成败、延迟、各类工具调用次数、最终答案与产物数，并对每个用例施加
   **确定性 expectations**（工具路由 / 调用预算 / 来源 / 数值结果）。需要先启动后端并配置好
   LLM 凭据（`.env` 中的 `OPENAI_API_KEY`）与检索源；**不进 CI，由人工触发**。

> 命名说明：在线评测衡量的是“整条运行时链路在真实模型下是否按预期工作”，不是回答质量打分。
> 没有人工标注的 ground truth，因此**不存在“回答准确率 X%”这类指标**；只有用例级
> passed/failed 与可复现的运行时元数据。

**不手填任何数字**：所有汇总数字都必须先跑出 JSON 再引用。

## 三层验证

| 层级 | 内容 | 触发 | 依赖网络/LLM |
|---|---|---|---|
| 单元 / 集成测试 | `pytest`（含 schemas 归类、expectations、TaskManager、路径/预算/缓存等） | 每次改动、CI | 否（用 fake/mock） |
| 离线工程回归 | `run_benchmark.py`，6 类 24 例（其中 4 个 e2e 用例离线 skip） | CI 必跑 | 否 |
| 在线 E2E 运行时基线 | `run_e2e_benchmark.py`，4 个真实 LLM 用例 | 人工触发 | 是 |

## 运行

```powershell
# 1) 离线确定性评测（无需任何外部服务）
.\.venv\Scripts\python.exe benchmarks\run_benchmark.py

# 2) 真实在线 E2E（先在另一个终端启动后端：
#    .\.venv\Scripts\python.exe -m uvicorn app.api.server:app --host 0.0.0.0 --port 8001）
.\.venv\Scripts\python.exe benchmarks\run_e2e_benchmark.py
# 可选参数：--base-url http://localhost:8001  --timeout 600
```

- 离线结果写入 `benchmarks/results/YYYY-MM-DD.json`（gitignore，不入库），以退出码反映是否全过。
- 在线结果每次写入 `benchmarks/results/e2e-<时间戳>.json`（gitignore，不入库）。
- **基线快照**：在线评测达到全 passed 时，把该次结果固化为
  `benchmarks/results/baseline-<git短SHA>.json` 并提交入库（仅此命名模式被 git 跟踪），
  作为对应 commit 的可追溯证据；普通 `e2e-*.json` 仍被忽略。
- 连不上后端时在线 runner 以退出码 2（环境错误）结束；用例未全部 passed 时退出码 1；
  全部 passed 退出码 0。

## 在线 E2E 的确定性 expectations

每个用例在 `cases.json` 中声明 `expectations`，由 `schemas.evaluate_expectations` 纯函数
判定（离线单测见 `tests/test_e2e_benchmark.py`），终态为 passed 但违反任一 expectation
仍改判 failed：

| 用例 | 任务 | 关键 expectations |
|---|---|---|
| e2e-01 | 常识直答 | 网页/学术/Python 调用均为 0 |
| e2e-02 | 网页检索带来源 | 网页 ≥1 且 ≤3、不跑 Python、答案含 URL 或 DOI |
| e2e-03 | 学术检索论文 | 学术 ≥1 且 ≤2、不跑 Python、答案含论文 URL 或 DOI |
| e2e-04 | 现场执行代码 | Python ≥1 且 ≤12、不检索、答案含 F(60)=1548008755920 |

- 主管家可在预算内**扇出多个专家**（如网页任务同时派单学术），因此只强制“调对专家 +
  不超预算 + 研究类带来源 + 计算类不检索”，不强制跨专家调用数为 0。
- 来源可以是 `http(s)` 链接，也可以是论文 DOI（`10.xxxx/...`）；数值期望忽略千分位分隔符。
- **用例隔离**：thread_id 形如 `e2e-<case>-<run_id>`，每次运行生成新的 `run_id`，
  保证用例之间、多次运行之间不复用后端 SQLite checkpoint 历史（否则智能体会“记得”
  上一次已检索/已计算而跳过工具，导致结果不可复现）。
- 进程内 TTL 检索缓存命中时上报“缓存命中”事件，仍计入对应专家路由；“预算已用完”
  这类空操作不计为一次检索。

## 最近一次在线基线

| 项 | 值 |
|---|---|
| 基线文件 | `results/baseline-c8f6a24.json` |
| 对应 commit | `c8f6a24`（LLM 懒加载 + 空答案契约后的运行时版本） |
| 运行环境 | 干净 Python 3.11.9（与 CI 同版本，依赖取自 `requirements-lock.txt`），Windows |
| 逐用例延迟 | e2e-01 1.6s / e2e-02 46.6s（web3+acad1）/ e2e-03 23.3s（acad1）/ e2e-04 15.6s（py3，1 产物） |
| 模型 | main=fast=`deepseek-flash`（DeepSeek，`https://api.deepseek.com/v1`） |
| 用例结果 | **4 passed / 4 total**（success=true，退出码 0） |
| 每任务预算 | 网页 3 / 学术 2 / Python 12 |

> 该结果只代表上述 commit、模型与运行环境下的一次真实快照（元数据含 Python 版本、OS、
> cases 文件 sha256、run_id）。在线结果受真实模型与外部检索源影响，允许波动；
> 复跑请重新生成基线，**不得为了凑 100% 删改用例或篡改结果**。

## 离线用例分类（6 类，共 24 例）

| 类别 | 数量 | 验证内容 | 是否需要 LLM |
|---|---|---|---|
| 会话路径边界 path_safety | 4 | `../`、Unix/Windows 绝对路径越界被拒；合法相对路径放行 | 否 |
| thread_id 白名单 thread_validator | 4 | UUID/合法串通过；`../`、超长被拒 | 否 |
| 查询缓存 TTL 与完整 key cache_ttl | 4 | TTL 内命中、过期未命中、max_results 不串、大小写归一命中 | 否 |
| 每任务工具预算 tool_budget | 4 | Python 第 13 次硬限制、reset 恢复、网页 3 次/学术 2 次预算 | 否 |
| 检索降级与熔断 web_fallback | 4 | SearXNG 优先、失败降级 DDG、双源空结果不误报故障、熔断只对异常计数 | 否 |
| 端到端 e2e_llm | 4 | 常识直答、网页/学术检索带来源、现场执行代码（离线统一 skip） | 是 |

## 最近一次离线运行结果

下表是离线模式（offline）的确定性结果；该回归在 GitHub Actions 的 Backend CI 中每次
push/PR 自动执行，**面向公众的可复现证据以 CI 运行结果为准**。本地运行另写
`benchmarks/results/YYYY-MM-DD.json`（已被 `.gitignore` 忽略、不入库），仅用于本地核对；
若下表与 CI 不符，请以 CI 为准并在干净 Python 3.11 环境重新运行：

| 类别 | 通过 | 失败 | 跳过 |
|---|---|---|---|
| path_safety | 4 | 0 | 0 |
| thread_validator | 4 | 0 | 0 |
| cache_ttl | 4 | 0 | 0 |
| tool_budget | 4 | 0 | 0 |
| web_fallback | 4 | 0 | 0 |
| e2e_llm | 0 | 0 | 4（离线不跑） |
| **合计** | **20** | **0** | **4** |

> 离线用例 **20/20 通过**；4 个 e2e 用例在离线模式下统一记为 skipped，需要真实链路时改用
> `run_e2e_benchmark.py` 执行（不再支持 `run_benchmark.py --e2e` 参数）。

## 设计原则

- 所有指标来自真实代码执行，断言写在 `evaluator.py` / `schemas.py`，可逐行核对。
- 检索降级用例通过注入假函数（`searxng_fn` / `ddg_fn`）验证编排逻辑，不依赖真实网络。
- 端到端事件归类逻辑（`schemas.py`）对未知 / 畸形事件健壮处理，并有纯离线单测覆盖
  （见 `tests/test_e2e_benchmark.py`，其中还包含一个经真实 ASGI HTTP+WebSocket 的确定性
  冒烟测试，用 fake agent 替代 LLM）。
- 结果文件记录真实模型、预算、Python 版本、OS、git SHA 与 cases 哈希，但**绝不包含任何
  API Key / Token**（`runtime_config.py` 内置密钥守卫并有测试）。
- 不把“看起来应该”的数字写进文档；任何汇总数字都必须先跑出 JSON 再回填。
