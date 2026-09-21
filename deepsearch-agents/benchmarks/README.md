# Benchmark：可复现评测

本目录包含**两套目的不同**的评测，二者不要混为一谈：

1. **离线工程正确性评测**（`run_benchmark.py`）：对真实代码做确定性断言，秒级完成，
   不依赖网络与 LLM，CI 必跑。
2. **端到端 Answer Quality 评测**（`run_e2e_benchmark.py`）：像前端一样先连 WebSocket 再
   POST `/api/task`，走真实 HTTP + WebSocket + LLM + 检索源，记录每个用例的成败、延迟、
   各类工具调用次数、最终答案与产物数。需要先启动后端并配置好 `DEEPSEEK_API_KEY`（即
   `.env` 中的 `OPENAI_API_KEY`）与检索源。

**不手填任何数字**：所有汇总数字都必须先跑出 JSON 再引用。

## 运行

```powershell
# 1) 离线确定性评测（无需任何外部服务）
.\.venv\Scripts\python.exe benchmarks\run_benchmark.py

# 2) 真实端到端评测（先在另一个终端启动后端：
#    .\.venv\Scripts\python.exe -m uvicorn app.api.server:app --host 0.0.0.0 --port 8001）
.\.venv\Scripts\python.exe benchmarks\run_e2e_benchmark.py
# 可选参数：--base-url http://localhost:8001  --timeout 600  --limit N
```

- 离线结果写入 `benchmarks/results/YYYY-MM-DD.json`，并以非零退出码反映离线用例是否全过。
- 端到端结果写入 `benchmarks/results/e2e-<时间戳>.json`，记录 `generated_at`、git 短 SHA、
  模型、预算配置（网页 3 / 学术 2 / Python 12）与逐用例指标；连不上后端时以退出码 2 结束并
  提示启动命令。

## 离线用例分类（6 类，共 24 例）

| 类别 | 数量 | 验证内容 | 是否需要 LLM |
|---|---|---|---|
| 会话路径边界 path_safety | 4 | `../`、Unix/Windows 绝对路径越界被拒；合法相对路径放行 | 否 |
| thread_id 白名单 thread_validator | 4 | UUID/合法串通过；`../`、超长被拒 | 否 |
| 查询缓存 TTL 与完整 key cache_ttl | 4 | TTL 内命中、过期未命中、max_results 不串、大小写归一命中 | 否 |
| 每任务工具预算 tool_budget | 4 | Python 第 13 次硬限制、reset 恢复、网页 3 次/学术 2 次预算 | 否 |
| 检索降级与熔断 web_fallback | 4 | SearXNG 优先、失败降级 DDG、双源空结果不误报故障、熔断只对异常计数 | 否 |
| 端到端 e2e_llm | 4 | 常识直答、网页/学术检索带来源、现场计算（离线统一 skip） | 是 |

## 最近一次离线运行结果

下表来自 `benchmarks/results/` 中最近一次真实离线运行（offline 模式）；以仓库内结果 JSON
为准，若与下表不符请以 JSON 为准并重新运行：

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

- 所有指标来自真实代码执行，断言写在 `evaluator.py`，可逐行核对。
- 检索降级用例通过注入假函数（`searxng_fn` / `ddg_fn`）验证编排逻辑，不依赖真实网络。
- 端到端事件归类逻辑（`schemas.py`）对未知 / 畸形事件健壮处理，并有纯离线单测覆盖
  （见 `tests/test_e2e_benchmark.py`，其中还包含一个经真实 ASGI HTTP+WebSocket 的确定性
  冒烟测试，用 fake agent 替代 LLM）。
- 不把“看起来应该”的数字写进文档；任何汇总数字都必须先跑出 JSON 再回填。
