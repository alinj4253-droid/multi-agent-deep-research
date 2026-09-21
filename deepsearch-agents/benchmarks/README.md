# Benchmark：可复现评测

本目录对系统的**工程正确性**做确定性评测，不依赖网络与 LLM 的用例直接对真实代码断言；需要大模型与检索服务的端到端用例单独标记，无凭据时记为 skipped，**不手填任何数字**。

## 运行

```powershell
# 仅离线确定性评测（秒级，无需任何外部服务）
.\.venv\Scripts\python.exe benchmarks\run_benchmark.py

# 额外跑端到端用例（需要 DEEPSEEK_API_KEY 与本地 SearXNG）
.\.venv\Scripts\python.exe benchmarks\run_benchmark.py --e2e
```

结果写入 `benchmarks/results/YYYY-MM-DD.json`，并以非零退出码反映离线用例是否全过（可直接挂 CI）。

## 用例分类（6 类，共 24 例）

| 类别 | 数量 | 验证内容 | 是否需要 LLM |
|---|---|---|---|
| 会话路径边界 path_safety | 4 | `../`、Unix/Windows 绝对路径越界被拒；合法相对路径放行 | 否 |
| thread_id 白名单 thread_validator | 4 | UUID/合法串通过；`../`、超长被拒 | 否 |
| 查询缓存 TTL 与完整 key cache_ttl | 4 | TTL 内命中、过期未命中、max_results 不串、大小写归一命中 | 否 |
| 每任务工具预算 tool_budget | 4 | Python 第 13 次硬限制、reset 恢复、网页 3 次/学术 2 次预算 | 否 |
| 检索降级与熔断 web_fallback | 4 | SearXNG 优先、失败降级 DDG、双失败返回尝试链、熔断直接跳过 | 否 |
| 端到端 e2e_llm | 4 | 常识直答、网页/学术检索带来源、现场计算 | 是（可选） |

## 本次离线运行结果

运行命令：`python benchmarks/run_benchmark.py`（offline 模式）

| 类别 | 通过 | 失败 | 跳过 |
|---|---|---|---|
| path_safety | 4 | 0 | 0 |
| thread_validator | 4 | 0 | 0 |
| cache_ttl | 4 | 0 | 0 |
| tool_budget | 4 | 0 | 0 |
| web_fallback | 4 | 0 | 0 |
| e2e_llm | 0 | 0 | 4（无凭据） |
| **合计** | **20** | **0** | **4** |

> 离线用例 **20/20 通过**；整体 83.3% 的分母含 4 个因无 LLM 凭据而跳过的 e2e 用例。在配好 `DEEPSEEK_API_KEY` 与 SearXNG 后用 `--e2e` 重跑，e2e 用例会被真实执行并计入结果。

## 设计原则

- 所有指标来自真实代码执行，断言写在 `evaluator.py`，可逐行核对。
- 检索降级用例通过**注入假函数**（`searxng_fn`/`ddg_fn`）验证编排逻辑，不依赖真实网络。
- 不把"看起来应该"的数字写进文档；任何汇总数字都必须先跑出 JSON 再回填。
