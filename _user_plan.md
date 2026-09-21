# Multi-Agent Deep Research 最终收尾改造方案

> 目标：将当前 `multi-agent-deep-research` 从“已经具备完整功能的二次工程化项目”收口为一个可以公开放在 GitHub、写入大模型 Agent / AI 应用算法实习简历、并能够承受面试官深入追问的完整工程项目。
>
> 本文档不是新增功能路线图。除非某项改动直接服务于安全性、正确性、可复现性或简历可信度，否则本轮不再扩展新的 Agent、Memory、Reflection、MCP 等能力。核心原则是：**把已经有的能力做实、把描述与实现对齐、把测试和评测补齐、把公开仓库整理干净。**

---

## 0. 最高优先级协作规则：必须遵守 `AGENTS.md`

仓库根目录已有 `AGENTS.md`，其中两条规则在本轮改造中属于**强制约束**，优先级高于本文档中任何单项实现建议：

1. **每次改动完成后，都必须创建一个对应的 Git Commit，以便后续追踪和回滚。**
2. **每次改动后，都必须编写或更新相关测试，并在交付前确保所有测试和验证全部通过。**

因此，本轮禁止采用以下开发方式：

- 禁止一次性修改全部问题，最后只提交一个“大杂烩 commit”。
- 禁止先改代码、连续推进多个阶段，最后统一补测试。
- 禁止“功能看起来能跑”但单元测试、回归测试未通过就进入下一阶段。
- 禁止为了让 README 好看而先写性能数字，之后再补实验。
- 禁止使用没有实际实现支撑的表述，例如把普通字符串 LRU 缓存称为“语义缓存”，把普通 Python 子进程称为“安全沙箱”。

### 标准执行循环

每一个改造阶段都必须按照下面的固定顺序执行：

```text
阅读相关代码
    ↓
确认问题能够复现 / 当前行为明确
    ↓
实现本阶段修改
    ↓
新增或更新对应测试
    ↓
运行本阶段定向测试
    ↓
运行后端完整 pytest / 前端 lint、build 等必要回归
    ↓
确认全部通过
    ↓
git diff 审查
    ↓
git add
    ↓
git commit
    ↓
进入下一阶段
```

每个阶段原则上对应 **1 个独立 Git Commit**。如果一个阶段本身包含两个互相独立、可单独回滚的改动，可以继续拆分 commit，但不能反过来把多个阶段压成一个 commit。

建议最终提交历史保持为：

```text
fix(security): enforce thread id and session path boundaries
fix(agent): reset python execution budget per task
fix(search): make query cache keys complete and add ttl
docs: align terminology with actual runtime guarantees
test(benchmark): add reproducible research task benchmark
ci: add backend and frontend verification workflow
chore(repo): clean public repository and add attribution
docs: finalize public readme with verified benchmark results
```

Commit Message 可以根据实际文件稍作调整，但必须保证“一个 Commit 能清楚解释一组完整且可验证的修改”。

---

# 1. 本轮改造总体目标

当前项目已经具备以下真实能力，因此**不需要推倒重做**：

- 主智能体 + 网络检索 / 数据分析 / 学术检索三个子智能体的多 Agent 协作链路；
- FastAPI 后端与 React 前端；
- WebSocket 实时事件推送；
- LangGraph SQLite Checkpointer 多轮状态持久化；
- SearXNG 为主、DDG 为降级的网络检索；
- arXiv / OpenAlex / Crossref 三源学术检索；
- 检索 Budget、Circuit Breaker、LRU Cache、关键词重排；
- Python 子进程数据分析、执行超时、产物检测、调用次数限制；
- 上传文件、会话历史、任务取消和产物下载；
- 已经存在一批单元测试和 E2E 脚本。

本轮真正需要完成的是五件事：

**第一，修安全和正确性问题；第二，让代码行为与 README/简历表述完全一致；第三，补可复现 Benchmark；第四，补 CI；第五，把 GitHub 仓库整理成“产品/工程项目”的公开形态。**

完成标准不是“能运行”，而是：

> 面试官打开 GitHub 后，从 README 到核心代码、测试、Benchmark、Git History 都不存在明显矛盾；简历写出的每个技术点都能在代码和实验结果中找到证据。

---

# 2. P0：统一 `thread_id` 校验，修复路径穿越入口

## 2.1 当前问题

目前部分接口已经对 `thread_id` 做了格式约束，但任务创建等入口并没有保证同一套校验规则。

`thread_id` 最终会参与生成类似：

```text
output/session_{thread_id}
updated/session_{thread_id}
```

这样的文件系统路径。

因此，如果某些 API 接受未经约束的 `thread_id`，攻击者或异常客户端理论上可以传入带路径语义的内容，使文件系统边界变得不可信。

这不是 README 表述问题，而是实际的 API / 文件路径安全问题，必须作为 P0 修复。

---

## 2.2 目标行为

全系统中的 `thread_id` 必须满足统一规则，例如：

```regex
^[A-Za-z0-9_-]{1,64}$
```

必须保证：

- 自动生成的 UUID 合法；
- 用户通过 API 显式传入的 thread_id 必须经过统一校验；
- 上传、任务、WebSocket、历史会话、文件下载等所有接收 thread_id 的入口使用同一规则；
- 不允许 `../`、`\`、`/`、`.` 路径跳转字符；
- 不允许空字符串；
- 不允许超长 ID。

---

## 2.3 推荐实现

不要在每个接口中复制一份正则。

建议新增统一模块，例如：

```text
deepsearch-agents/app/utils/validators.py
```

定义：

```python
THREAD_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

def validate_thread_id(thread_id: str) -> str:
    ...
```

如果 API 请求模型使用 Pydantic，也可以在 Request Schema 层使用 `Field(pattern=...)` 做第一层校验，但**不能只依赖 Pydantic**。底层会话目录创建逻辑仍应调用公共 validator，形成双层保护：

```text
API Schema validation
        ↓
service / filesystem boundary validation
```

这样即使未来增加新的调用入口，也不容易绕过。

---

## 2.4 必须新增测试

至少增加：

1. 合法 UUID 可以创建任务；
2. `abc_123-test` 可以通过；
3. `../test` 被拒绝；
4. `..\test` 被拒绝；
5. `foo/bar` 被拒绝；
6. 超过 64 字符被拒绝；
7. 空字符串被拒绝；
8. `/api/task` 与 `/api/upload` 行为一致；
9. 直接调用底层 session path 创建函数时，非法 ID 仍然被拒绝。

---

## 2.5 验收标准

只有同时满足下面条件，才能提交：

- 所有对外入口统一使用相同规则；
- 没有重复散落的 thread_id 正则；
- 新增安全测试全部通过；
- 后端完整 `pytest` 通过；
- 原有 E2E 行为没有被破坏。

### 建议 Commit

```text
fix(security): enforce thread id validation across session APIs
```

---

# 3. P0：建立真正的 Session Path Boundary

## 3.1 当前问题

当前路径解析逻辑存在一个核心风险：

如果传入的是已经存在或可解析的绝对路径，部分逻辑可能直接接受这个绝对路径，而不是强制将所有 Agent 文件操作限制在当前会话目录中。

这意味着目前 README 中类似：

```text
工作目录限定在当前会话目录
会话目录隔离
沙箱安全执行
```

这样的描述并不能由现有文件系统边界完全支撑。

即使后续决定不做 Docker Sandbox，这个路径边界也必须修，因为它属于基本正确性和安全性。

---

## 3.2 目标行为

对于 Agent 可操作的文件，必须保证：

```text
resolved_path ∈ current_session_root
```

即：

```python
resolved_path.is_relative_to(session_root)
```

如果当前 Python 版本兼容性需要考虑，也可以使用 `os.path.commonpath` 实现等价判断。

需要防止：

- `../../xxx`
- 绝对路径 `C:\Users\...`
- `/etc/passwd`
- 通过 symlink 离开 session root
- 上传文件读取工具绕过 session root
- Markdown / PDF / 文件下载工具绕过边界

---

## 3.3 推荐实现

将所有 session 文件路径解析集中在一个函数中：

```python
def resolve_session_path(
    input_path: str | Path,
    session_root: Path,
    *,
    must_exist: bool = False
) -> Path:
    ...
```

完整逻辑建议：

```text
1. 获取 session_root.resolve()
2. 如果输入是相对路径：
   candidate = session_root / input_path

3. 如果输入是绝对路径：
   不允许“因为是绝对路径就自动接受”
   必须继续验证其是否位于 session_root 内

4. candidate.resolve(strict=False)

5. 检查 candidate 是否仍在 resolved_session_root 之下

6. 越界：
   raise ValueError / PermissionError

7. 合法：
   返回 candidate
```

如果存在 symbolic link，需要保证 resolve 后再次判断。

---

## 3.4 应覆盖的调用方

审查并统一下列文件操作路径：

- Python 执行器输入 / 输出路径；
- 上传文件读取工具；
- Markdown 生成；
- PDF 生成；
- 图像 / CSV / XLSX 等产物定位；
- API 下载；
- 会话 output 目录；
- updated 上传缓存目录。

原则：

> Agent 和客户端提供的路径永远不能决定宿主机任意位置；它们只能在服务器主动分配的 session workspace 中工作。

---

## 3.5 测试

至少包括：

- 正常相对路径；
- session 内绝对路径；
- `../` 越界；
- 多层 `../../`；
- Windows 风格绝对路径；
- Unix 绝对路径；
- session 根目录本身；
- session 子目录；
- symlink 越界（如果测试环境允许）；
- 上传读取、下载接口和 Python 工具至少各有一条越界回归测试。

---

## 3.6 验收标准

最终应该能够准确地说：

> 文件读写层建立了 session workspace boundary，任何 Agent 文件操作都会经过统一路径解析并拒绝访问当前会话目录之外的路径。

注意：这仍然**不代表 Python 代码本身已经实现 OS 级安全沙箱**。这一点在第 4 节统一处理。

### 建议 Commit

如果与 thread_id 同属一次完整安全修复，可以合并为：

```text
fix(security): enforce thread id and session path boundaries
```

如果改动较大则单独：

```text
fix(security): restrict agent file access to session workspace
```

两种都符合 AGENTS.md，关键是一个 commit 内逻辑必须完整、测试必须随 commit 一起进入。

---

# 4. P0：纠正“Python Sandbox”定位，不制造简历过度包装

## 4.1 当前问题

当前 Python 数据分析工具真实具备：

- 独立 Python 子进程；
- `cwd` 指向当前 session；
- 30 秒执行超时；
- 每会话 / 每任务调用次数限制；
- 产物文件差分检测；
- stdout / stderr 回传。

但这些能力**不等于安全沙箱**。

普通 Python 子进程仍可能：

- 访问宿主机其他允许访问的文件；
- 读取继承的环境变量；
- 发起网络请求；
- 启动其他进程；
- 使用宿主机 CPU / 内存；
- 调用系统接口。

因此，“Python 安全沙箱”“会话目录隔离执行”属于明显过度表述。

---

## 4.2 本轮推荐决策

本轮**不强制做 Docker Sandbox**。

原因：

1. 当前项目主要目标是 Agent 算法 / 应用实习，而不是安全执行平台；
2. Docker Sandbox 会显著增加部署复杂度；
3. 真正的安全 Sandbox 还需要网络、环境变量、资源配额、用户权限、只读文件系统等完整策略，不能只“套个 Docker”就宣称安全；
4. 当前更重要的是完成 Benchmark、CI 和项目门面收口。

因此，将这部分统一改称：

```text
受控 Python 子进程执行器
```

或：

```text
受约束 Python 执行器
```

---

## 4.3 README / 代码注释 / 简历建议表述

禁止：

```text
Python 安全沙箱
安全沙箱执行
通过会话目录实现安全隔离
```

建议：

```text
为数据分析 Agent 实现独立 Python 子进程执行器，
通过 30s 超时、任务级调用预算、Session Workspace、
产物差分检测和执行结果回传抑制重复执行与无限循环。
```

如果未来真正实现 Docker Sandbox，可以另开独立阶段，至少要求：

- `network=none`
- 非 root 用户
- 不继承宿主机敏感环境变量
- 只挂载当前 session workspace
- CPU limit
- memory limit
- PID limit
- execution timeout
- root filesystem 只读或最小权限
- 镜像预装白名单依赖

在这些能力没有真正实现之前，不再使用 Secure Sandbox 作为项目卖点。

---

# 5. P1：修复 Python 调用预算跨轮累计问题

## 5.1 当前问题

网络检索和学术检索 Budget 会在一次新的 `run_deep_agent()` 开始时 reset。

但 Python 执行次数如果仅以 `thread_id` 累加，而没有在每次新用户任务开始时 reset，就会产生：

```text
同一历史会话
第一轮 4 次
第二轮 3 次
第三轮 5 次
----------------
达到 12 次
之后该 thread 永久无法再使用 Python
```

这显然不是合理的产品行为。

---

## 5.2 统一语义

本项目中的 Tool Budget 统一定义为：

```text
per-task / per-turn budget
```

即：

> 每次用户发起一个新的 Agent 任务，都拥有新的工具调用预算；Budget 的目标是防止“单次任务内部失控循环”，不是永久限制一整个历史会话。

因此在任务开始时统一：

```python
search_budget.reset(thread_id)
academic_budget.reset(thread_id)
python_execution_budget.reset(thread_id)
```

长期最好把这三个预算抽象成统一接口，避免未来再出现某一个忘记 reset。

---

## 5.3 测试

必须覆盖：

- 单轮 12 次以内正常执行；
- 单轮第 13 次被限制；
- 新的一轮同一个 thread_id 可以重新执行；
- 不同 thread_id 互不影响；
- reset 不会误清除另一个 thread 的计数；
- 任务异常退出后，下一轮仍能初始化新的 Budget。

### 建议 Commit

```text
fix(agent): reset python execution budget for each task
```

---

# 6. P1：把“语义缓存”纠正为真正的 Query LRU Cache

## 6.1 当前问题

当前所谓“语义缓存”的核心逻辑只是：

```text
strip
lower
合并空白字符
```

然后将标准化字符串作为 LRU key。

例如：

```text
"3DGS 最新进展"
```

与：

```text
"3D Gaussian Splatting 最近有哪些研究"
```

不会被认为是同一个查询。

因此它不是 Semantic Cache。

---

## 6.2 推荐方案

本轮不引入 embedding / vector database。

直接把命名改成：

```text
Normalized Query LRU Cache
查询结果 LRU 缓存
标准化查询缓存
```

这是当前真实实现，而且技术价值已经够用。

只有未来实际完成：

```text
query embedding
    ↓
cosine similarity
    ↓
threshold
    ↓
reuse semantically similar result
```

才能重新使用“Semantic Cache”这个名称。

---

# 7. P1：补全 Cache Key 与 TTL

## 7.1 当前问题

缓存还存在两个工程问题。

### 问题 A：Cache Key 不完整

网页搜索如果只使用 `query` 当 key：

```text
query="xxx", max_results=5
```

第一次缓存 5 条以后：

```text
query="xxx", max_results=10
```

可能直接返回之前的 5 条。

同理，region、sources、year_from、max_results_per_source 等所有会影响结果的数据，都必须参与 key。

### 问题 B：无 TTL

Deep Research 中大量任务属于：

- 今天发生了什么；
- 最新政策；
- 最新论文；
- 近期技术进展。

如果缓存永久有效，那么服务长期运行后会把“历史搜索结果”作为最新数据返回。

---

## 7.2 推荐数据结构

缓存条目至少：

```python
@dataclass
class CacheEntry:
    value: Any
    created_at: float
```

读取时：

```text
now - created_at <= ttl
    → hit
否则
    → expire + remove + real search
```

网页搜索建议默认 TTL：

```text
10 ～ 30 分钟
```

学术检索可以更长：

```text
1 ～ 6 小时
```

不建议写死在实现深处，使用配置常量或环境变量。

---

## 7.3 Cache Key

Web：

```text
normalized_query
region
max_results
可能影响 provider 行为的其他参数
```

Academic：

```text
normalized_query
year_from
sources
max_results_per_source
排序参数（如存在）
```

推荐生成 tuple，不需要为了“高级”使用复杂 hash。

---

## 7.4 Budget 与 Cache 的关系

保持当前合理逻辑：

```text
cache hit
    → 不发生真实外部请求
    → 不消耗 search budget

cache miss
    → 真实搜索
    → 消耗 budget
```

TTL 过期应视为 miss。

---

## 7.5 测试

必须覆盖：

- 相同完整 key 命中；
- query 相同但 max_results 不同，不误命中；
- query 相同但 region 不同，不误命中；
- Academic year_from 不同，不误命中；
- sources 不同，不误命中；
- TTL 内命中；
- TTL 过期重新搜索；
- 空结果是否不缓存保持既有设计；
- cache hit 不增加 Budget；
- expired cache 触发真实请求并消耗 Budget。

### 建议 Commit

```text
fix(search): add ttl and complete keys to query caches
```

---

# 8. P1：统一 SearXNG / DDG 描述与真实实现

## 8.1 当前问题

当前运行配置已经是：

```text
SearXNG 主检索
默认启用 Bing / Yandex / 搜狗 / 360 等当前可用引擎
SearXNG 不可用时使用独立 DDG provider fallback
```

但部分旧代码注释、旧 README 可能仍存在：

```text
SearXNG 聚合 Google/Bing/DuckDuckGo 等 70+ 引擎
```

之类从上游或旧版本留下来的描述。

这种问题本身不影响运行，但是面试官同时看 settings 和 README 时会产生矛盾。

---

## 8.2 最终统一表述

统一写成：

> 自建 SearXNG 作为可配置元搜索聚合层；当前默认配置针对本地网络环境启用 Bing、Yandex、搜狗、360 等可用搜索源，当 SearXNG 不可用时通过独立 `ddgs` provider 降级。

不要使用：

```text
70+ 搜索源
稳定聚合 Google / DDG ...
```

除非当前实际配置和测试能够证明。

---

## 8.3 检查范围

全局搜索：

```text
semantic cache
语义缓存
sandbox
安全沙箱
70+
Google
Tavily
旧的搜索供应商描述
```

代码注释、README、deepsearch-agents/README、docstring、架构图文字全部统一。

### 建议 Commit

如果和术语整改一起完成：

```text
docs: align search and execution terminology with implementation
```

---

# 9. P1：建立可复现 Benchmark，而不是继续堆新 Agent

这是本轮对“简历质量”提升最大的一步。

---

## 9.1 当前不足

已有单元测试回答的是：

```text
代码有没有按照设计运行？
```

已有 E2E 回答的是：

```text
整个系统能不能跑通？
```

但简历真正缺少的问题是：

```text
这些工程治理有没有实际效果？
```

例如 README 如果写：

```text
将搜索次数从 15+ 降到 3 次以内
```

“3 次以内”本身是 hard limit，不足以证明方案有效。

真正需要证明的是：

> 调用次数降低以后，任务仍然能够完成，而且引用、年份、产物等质量指标没有明显崩掉。

---

## 9.2 Benchmark 规模

第一版不需要 100 道。

建议：

```text
24 ～ 30 个固定任务
```

分为 6 类，每类 4～5 个：

### A. 常识直答

目标：

- 不应触发联网；
- 不应错误路由到子 Agent。

### B. 普通网络检索

目标：

- 正确使用 Web Search；
- 搜索次数不超预算；
- 最终答案包含有效来源。

### C. 时效信息研究

目标：

- TTL / 查询链路正常；
- 返回可验证的新信息；
- 引用链接有效。

### D. 学术文献

包括：

- 最新论文；
- 经典论文；
- 指定年份；
- 多源检索；
- 4DGS / Agent 等实际研究主题。

目标：

- year_from 满足率；
- DOI/URL 有效性；
- 去重有效。

### E. Python 数据分析

包括：

- 统计；
- CSV；
- 可视化；
- 文件产物。

目标：

- 任务完成；
- 不无限重复调用；
- 产物真实存在。

### F. 混合复杂任务

例如：

```text
检索某个主题近期论文
→ 提取数据
→ Python 汇总
→ 输出报告
```

目标：

- 主 Agent 路由逻辑；
- 多工具协同；
- 总调用次数；
- 完成率。

---

# 10. Benchmark 数据格式

建议：

```text
deepsearch-agents/benchmarks/
├── cases.json
├── run_benchmark.py
├── evaluator.py
├── README.md
└── results/
    └── YYYY-MM-DD.json
```

单个 case 示例：

```json
{
  "id": "academic_recent_001",
  "category": "academic_recent",
  "query": "检索 2025 年以来 4D Gaussian Splatting 的代表性工作",
  "requirements": {
    "must_use_academic_search": true,
    "year_from": 2025,
    "max_academic_calls": 3
  }
}
```

运行结果需要记录：

```text
case id
是否完成
最终回答
总耗时
主 Agent 调度次数
web_search 调用次数
academic_search 调用次数
python 调用次数
tool error 次数
是否超过预算
输出文件
引用链接
```

---

# 11. Benchmark 第一版指标

优先做**可以自动验证的指标**，不要一上来做 LLM-as-a-Judge，把评测搞得不可复现。

至少统计：

| 指标 | 含义 |
|---|---|
| Task Completion Rate | 固定任务是否得到非错误最终结果 |
| Tool Routing Accuracy | 需要某工具时是否调用、不需要时是否误调用 |
| Avg Web Calls | 平均网页搜索次数 |
| Budget Violation Rate | 是否发生超预算 |
| Academic Year Constraint Rate | 返回论文是否满足 year_from |
| Citation Presence Rate | 要求来源的任务是否返回来源 |
| Citation URL Valid Rate | 引用 URL 格式 / 可访问性 |
| Artifact Success Rate | 数据分析任务的文件是否真实生成 |
| Avg / P50 / P95 Latency | 端到端耗时 |
| Tool Error Rate | 外部工具异常比例 |

如后续确实有“改造前”代码 tag / commit，可以对同一 Benchmark 跑 Before / After。

如果无法稳定重现旧版本，**不要伪造“改造前数字”**。

可以只公开：

```text
当前版本 30-case benchmark
```

简历同样可以写：

> 在 30 个固定研究任务回归集上，工具预算违规率 0%，xxx。

具体数字必须等真实跑完以后再填写。

---

# 12. Benchmark 不允许手填“漂亮数据”

严格规则：

- `README.md` 的所有性能数字必须来源于 `benchmarks/results/*.json`；
- `results` 文件应保留运行时间、Git commit SHA、模型名、配置；
- 不允许 Agent 自动把预期值填进结果表；
- 外部搜索受网络波动影响时，要在 README 写清测试条件；
- 如果某项结果不好，先保留真实结果，再分析，而不是删除 case。

### 建议 Commit

```text
test(benchmark): add reproducible multi-agent research benchmark
```

注意：Benchmark 属于“测试/验证能力”，但它不是用来替代单元测试。新增 Benchmark 的同时仍然必须运行完整 pytest。

---

# 13. P2：补 GitHub Actions CI

## 13.1 目标

让 GitHub 上每一个 commit 都能够自动证明：

```text
后端测试通过
前端 lint 通过
前端 build 通过
```

而不是 README 手写：

```text
106 passed
```

---

## 13.2 推荐 Workflow

新增：

```text
.github/workflows/ci.yml
```

至少两个 job：

### backend

```text
checkout
setup python
install dependencies
pytest
```

如果项目已经使用 Ruff：

```text
ruff check
```

如果当前没有 Ruff，不建议本轮为了 CI 临时引入大量代码格式修改；可以以后再加。

### frontend

```text
checkout
setup node
npm ci
npm run lint
npm run build
```

不强制在 CI 中运行需要真实 API / SearXNG / LLM Key 的在线 E2E，因为会造成：

- 密钥管理复杂；
- 外部 API 不稳定；
- CI 成本不可控。

在线 E2E 可以作为“发布前人工回归”。

---

## 13.3 README

CI 运行稳定后，可以增加 badge：

```text
CI passing
```

但不需要堆很多 badge。

### 建议 Commit

```text
ci: verify backend tests and frontend build on every push
```

---

# 14. P1：清理公开 GitHub 仓库门面

当前仓库根目录存在多份：

```text
审查记录与待决策问题.md
第二轮修复记录与待决策问题.md
第四阶段进度总结与后续规划.md
项目进度总览与收尾计划.md
改造方案.md
```

这类文件对项目开发过程有价值，但对公开简历仓库没有价值。

招聘者应该看到的是：

```text
这个项目是什么
为什么这么设计
怎么运行
怎么测试
效果怎样
局限是什么
```

而不是：

```text
AI 帮这个项目改了几轮
为了简历还有哪些地方没包装完
```

---

## 14.1 最终公开仓库建议结构

```text
.
├── .github/
│   └── workflows/
│       └── ci.yml
├── deepsearch-agents/
│   ├── app/
│   ├── benchmarks/
│   ├── docs/
│   ├── scripts/
│   ├── tests/
│   └── ...
├── frontend-demo/
├── searxng/
├── AGENTS.md
├── CREDITS.md
├── LICENSE
├── README.md
└── .gitignore
```

开发阶段审查文档：

- 如果自己还需要：保存在本地、不提交；
- 如果一定需要 Git 追踪：放到 private branch / private archive；
- 最终 public `main` 不保留。

---

## 14.2 README 禁止出现“简历描述示例”

当前根 README 中的：

```text
简历项目描述示例
```

应该删掉。

README 是项目说明书，不是求职包装笔记。

简历 bullet 可以单独保存在你自己的求职文档中，等 Benchmark 最终数据出来后再写。

---

## 14.3 README 最终结构

建议：

```text
1. 项目简介
2. Demo / Screenshot
3. Architecture
4. Core Capabilities
5. Why / Engineering Challenges
6. Search Governance
7. Academic Retrieval
8. Controlled Python Execution
9. Session Persistence & Observability
10. Benchmark
11. Quick Start
12. Testing
13. Project Structure
14. Limitations
15. Attribution
16. License
```

其中“Engineering Challenges”非常关键。

不要只列：

```text
LangGraph
FastAPI
React
SearXNG
```

而应该解释：

```text
问题：
Agent 会反复改写关键词 → 搜索循环

解决：
Prompt soft constraint + Tool-level hard budget

问题：
单一搜索源不稳定

解决：
SearXNG + DDG fallback + Circuit Breaker

问题：
数据分析 Agent 会重复执行相同脚本

解决：
Execution Budget + artifact diff + timeout

问题：
多轮历史丢失

解决：
AsyncSqliteSaver
```

这样才能把项目从“技术栈堆砌”转化成“工程问题解决能力”。

### 建议 Commit

```text
chore(repo): clean development notes from public repository
```

---

# 15. P1：处理 Upstream Attribution 和 License

## 15.1 原则

该项目是在公开上游 Deep Research 教程项目基础上继续改造的，因此不能在公开 GitHub 或面试中暗示：

```text
整个系统从 0 到 1 全部独立原创
```

真正应该强调的是你完成的增量工程工作。

---

## 15.2 新增 LICENSE

检查 upstream 原许可证要求。

如果为 MIT，则应保留对应 copyright / permission notice。

不要只在 README 写一句“基于某项目”，而缺失许可证文件。

---

## 15.3 新增 `CREDITS.md`

建议包含：

```text
# Credits

This project is derived from the Deep Research example in
didilili/ai-agents-from-zero.

The current repository substantially extends the original example with:
- self-hosted SearXNG + fallback retrieval;
- tool-level search budgets;
- cache / circuit breaker / reranking;
- multi-source academic retrieval;
- persistent multi-turn sessions;
- controlled Python execution;
- frontend observability;
- benchmark and CI;
...
```

这里不需要贬低自己的工作。

正确归因不会削弱简历，反而能防止面试时出现“代码来源被搜出来以后解释不清”的严重问题。

### 建议 Commit

```text
chore(repo): add upstream attribution and license
```

---

# 16. README 与简历术语最终统一表

在项目完全收尾前，全局检查下面这些术语。

| 当前可能存在的表述 | 最终推荐 |
|---|---|
| 语义缓存 | 查询结果 LRU 缓存 / Normalized Query LRU Cache |
| Python 安全沙箱 | 受控 Python 子进程执行器 |
| 会话目录安全隔离 | Session Workspace 文件路径边界 |
| 70+ 搜索引擎 | 可配置 SearXNG 元搜索；当前启用的具体源 |
| 零成本搜索 | 无商业 Search API 调用费用；仍需本地部署 / 网络资源 |
| 将搜索从 15+ 降到 ≤3 | 只有存在可复现实验时才保留 |
| 完全自主设计 DeepAgents 架构 | 基于 DeepAgents / upstream 进行二次工程化 |
| 每会话 Python 调用上限 | 每任务 Python Execution Budget |

---

# 17. 对已有废弃能力做最后清理：RAGFlow / knowledge_demo

当前 README 已说明：

```text
RAGFlow 相关代码保留，但当前主链路未接线
```

对最终简历仓库来说，最好做一次决策。

## 方案 A：删除废弃代码（推荐）

如果未来不准备演示：

```text
app/ragflow/
knowledge_demo.py
ragflow_tools.py
```

则直接删除，并同步删 README 和环境变量中的 RAGFlow 描述。

优点：

- 项目更干净；
- 不会让面试官误以为存在一个“其实没工作的 RAG”模块；
- 降低依赖和认知负担。

## 方案 B：保留

如果确实准备后续恢复，则必须明确标识：

```text
experimental / not wired into current runtime
```

但对当前“尽快形成简历项目”的目标而言，不建议为了一个未接线模块继续增加复杂度。

如果删除，需要补相关 import / startup regression test。

建议与 repo cleanup 一起提交，不单独追求功能。

---

# 18. 测试体系最终分层

本轮完成后，测试应该明确分为四层。

## 第一层：Unit Test

覆盖：

- Budget；
- Cache；
- TTL；
- Circuit Breaker；
- reranking；
- Academic sources；
- path validation；
- thread id validation；
- Python execution limit；
- session history reconstruction。

运行：

```bash
cd deepsearch-agents
python -m pytest -q
```

---

## 第二层：API / Integration Test

覆盖：

- create task；
- upload；
- invalid thread id；
- websocket event；
- cancel；
- history；
- artifact download；
- session boundary。

原则：尽量 mock 外部 API。

---

## 第三层：Online E2E

保留已有：

```text
常识直答
网络检索
数据分析
学术文献
```

建议再增加一个：

```text
复杂混合任务
```

E2E 需要真实后端 / SearXNG / 模型，因此不要求每个小 commit 都在线运行全部 E2E，但：

> **每个阶段必须运行本阶段可离线验证的完整 pytest；最终交付前必须跑一次完整在线 E2E。**

---

## 第四层：Benchmark

Benchmark 不判断“代码函数有没有 bug”，而用于衡量：

```text
系统行为质量
调用效率
约束满足率
延迟
工具错误率
```

---

# 19. 每阶段强制验收模板

以后让 Agent / Codex 修改时，每一阶段交付必须按下面格式汇报：

```markdown
## 本阶段完成内容

- 修改：
- 影响文件：
- 行为变化：

## 测试

新增 / 修改测试：
- xxx

执行：
- `python -m pytest ...`
- `npm run lint`
- ...

结果：
- xxx passed
- 0 failed

## Git

Commit:
`<hash> <commit message>`

## 遗留问题

- 无
```

如果测试失败：

```text
禁止 commit 为“完成状态”
禁止进入下一阶段
```

如果必须 commit 保存中间状态，应明确：

```text
WIP
```

但按照当前项目收尾目标，尽量不要制造 WIP commit。

---

# 20. 推荐完整执行顺序

不要并行乱改，严格按照以下顺序。

## Phase 1：安全边界

修改：

- thread_id validation；
- session filesystem boundary；
- 对应 API / utility tests。

验证：

```text
定向 security tests
完整 pytest
```

Commit：

```text
fix(security): enforce thread id and session path boundaries
```

---

## Phase 2：Python Execution Budget 正确性

修改：

- Python 次数限制从跨会话累计明确变为 per-task；
- task start 时 reset；
- 统一 Budget 语义。

测试：

- 同一个 thread 多轮；
- 第 13 次拒绝；
- 下一轮恢复。

Commit：

```text
fix(agent): reset python execution budget for each task
```

---

## Phase 3：Search Cache 正确性

修改：

- “semantic cache”代码命名；
- 完整 cache key；
- TTL；
- 配置常量。

测试：

- hit / miss；
- TTL；
- max_results；
- region；
- year_from；
- sources；
- Budget interaction。

Commit：

```text
fix(search): add ttl and complete keys to query caches
```

---

## Phase 4：术语与运行逻辑一致性

修改：

- Python Sandbox → Controlled Python Executor；
- Semantic Cache → Query LRU Cache；
- SearXNG 引擎描述；
- 每会话 → 每任务 Budget；
- 代码 docstring；
- README 中相关描述。

此阶段禁止写最终 Benchmark 数字。

测试：

- pytest；
- README command / config 人工核对；
- 全局 grep 旧术语。

Commit：

```text
docs: align terminology with actual runtime behavior
```

---

## Phase 5：Benchmark

新增：

```text
benchmarks/cases.json
benchmarks/run_benchmark.py
benchmarks/evaluator.py
benchmarks/README.md
```

至少 24～30 个固定 case。

先实现 runner 和指标，再真实运行。

测试：

- evaluator 单元测试；
- case schema test；
- dry-run / small subset；
- 完整 pytest。

Commit：

```text
test(benchmark): add reproducible research task benchmark
```

注意：

真实 benchmark result 如果容易受环境影响，可以随后单独：

```text
docs(benchmark): publish baseline benchmark results
```

不要把 runner 逻辑和人为结果修改混在一起。

---

## Phase 6：CI

新增 GitHub Actions。

验证：

本地：

```text
pytest
npm run lint
npm run build
```

Push 后确认 GitHub Actions Green。

Commit：

```text
ci: add backend and frontend verification workflow
```

---

## Phase 7：License / Attribution / 仓库清理

完成：

- LICENSE；
- CREDITS；
- 删除审查 / 改造 / 简历过程文档；
- 删除 README “简历项目描述示例”；
- 决定 RAGFlow 废弃代码是否删除；
- 清理过时 pyproject / package 描述；
- `.gitignore` 检查 runtime output / db / env。

测试：

```text
pytest
npm run lint
npm run build
```

Commit 建议拆成两个，便于回滚：

```text
chore(repo): add upstream attribution and license
chore(repo): clean development-only files from public repository
```

---

## Phase 8：最终 README

只有前面全部完成、Benchmark 真实运行以后，才写最终 README。

把真实结果填写进：

```text
Benchmark
```

章节。

最终运行：

```text
backend full pytest
frontend lint
frontend build
online E2E
benchmark
```

全部通过以后：

```text
docs: finalize public readme with verified benchmark results
```

---

# 21. 最终验收 Checklist

## 安全与正确性

- [ ] 所有 thread_id 入口统一校验
- [ ] Agent 文件操作不能越过 session workspace
- [ ] API 下载不能读取 session 外文件
- [ ] Python Budget 每个新任务正确 reset
- [ ] 不再把普通 subprocess 宣称为 Secure Sandbox

## Search

- [ ] LRU Cache 命名准确
- [ ] Cache Key 包含所有影响结果的参数
- [ ] Cache 有 TTL
- [ ] Cache Hit 不消耗 Budget
- [ ] Cache Expire 会重新搜索
- [ ] SearXNG / DDG 说明与真实配置一致

## Academic

- [ ] arXiv / OpenAlex / Crossref 三源仍正常
- [ ] year_from 测试通过
- [ ] 去重 / 排序测试通过
- [ ] Benchmark 有学术任务

## Python

- [ ] timeout 正常
- [ ] artifact diff 正常
- [ ] 调用上限正常
- [ ] 多轮 reset 正常
- [ ] session path boundary 正常

## Testing

- [ ] 后端 pytest 0 failed
- [ ] 前端 lint 通过
- [ ] 前端 build 通过
- [ ] 在线 E2E 全部通过
- [ ] Benchmark runner 可复现
- [ ] 每个改造阶段对应测试已提交

## CI

- [ ] GitHub Actions backend green
- [ ] GitHub Actions frontend green

## Repository

- [ ] LICENSE 存在
- [ ] CREDITS / Attribution 存在
- [ ] 审查记录不再出现在公开 main
- [ ] “简历描述示例”从 README 删除
- [ ] README 不包含过度包装词
- [ ] `.env` / API Key / checkpoints / output 未提交
- [ ] RAGFlow 等废弃代码已做明确决策

## Git

- [ ] 每个阶段都有独立 commit
- [ ] 每个 commit 都包含对应测试
- [ ] 不存在一个“大而全”的最终修改 commit
- [ ] commit message 能解释该 commit 的目的
- [ ] 最终 `git status` clean

---

# 22. 完成后，这个项目在简历上真正应该强调什么

最终不要把重点写成：

```text
会 LangChain
会 LangGraph
会 Tool Calling
会多 Agent
```

这些只是工具。

这个项目真正有区分度的逻辑应该是：

> 基于 DeepAgents 搭建多 Agent 深度研究系统后，针对真实运行过程中出现的检索循环、单一搜索源不稳定、多轮状态丢失、数据分析重复执行等问题，从 Tool / Runtime 层引入 Search Budget、Fallback、Circuit Breaker、Query Cache、SQLite Checkpointer、Execution Budget 和 Session Workspace Boundary，并通过固定 Benchmark 与 CI 验证系统行为。

也就是说，项目核心故事不是：

```text
“我搭了一个 Agent”
```

而是：

```text
“我让一个原本能跑的 Agent Demo 变成了一个行为受控、可恢复、可观测、可测试、可量化验证的工程系统。”
```

这个故事与当前项目真实的二次工程改造路径一致，也比继续新增第四、第五个 Agent 更适合作为实习简历项目。

---

# 23. 本轮明确不做的事情

为防止 Agent 后续再次无限扩需求，本轮以下事项默认 **Out of Scope**：

- 不新增无明确需求的第四 / 第五个子 Agent；
- 不为了关键词堆砌接入 Memory；
- 不为了关键词堆砌实现 Reflection；
- 不为了关键词堆砌接 MCP；
- 不为了叫“语义缓存”临时引入向量数据库；
- 不强制实现 Docker Secure Sandbox；
- 不重新设计整个前端；
- 不做复杂用户账户系统；
- 不做 Kubernetes / 云原生部署；
- 不做与简历可信度无直接关系的大规模重构。

如果以上能力未来确实需要，应在本轮全部验收完成后另开 Issue / Phase，不能插入当前收尾工作。

---

# 24. 对 Agent / Codex 的最终执行指令

如果把本文档交给代码 Agent，建议使用下面这段作为最前置指令：

```text
请严格按照《Multi-Agent Deep Research 最终收尾改造方案》逐阶段执行。

必须首先阅读仓库根目录 AGENTS.md。

任何阶段都必须遵守：
1. 先确认当前实现和问题；
2. 只修改当前阶段范围；
3. 同步新增或更新测试；
4. 运行定向测试和必要完整回归；
5. 全部通过后再创建 Git Commit；
6. 一个阶段完成后立即 commit，不允许积累多个阶段后统一提交；
7. 每次提交后汇报 commit hash、修改文件、测试命令和测试结果；
8. 未经真实 Benchmark 验证，不允许在 README 中编造或预填任何性能数字；
9. 不允许使用实现无法支撑的“语义缓存”“安全沙箱”等表述；
10. 不得未经要求新增 Agent、Memory、Reflection、MCP 等额外功能。

如果某阶段发现方案与当前代码结构不完全一致，应保持目标不变，选择最小且工程上合理的实现，并在该阶段说明调整原因；不要跳过问题，也不要扩大范围。
```

---

# 25. 最终完成定义（Definition of Done）

整个项目只有在以下条件**全部成立**时，才算真正“改好，可以写简历”：

1. P0 安全边界问题修复；
2. Python per-task Budget 修复；
3. Search Cache key + TTL 修复；
4. README、代码和实际运行行为完全一致；
5. 不再存在“Semantic Cache / Secure Sandbox”式过度包装；
6. 24～30 个固定 Benchmark 能完整运行并产生真实结果；
7. CI 自动验证后端与前端；
8. 上游 Attribution / License 合规；
9. Public GitHub 中开发审查记录与简历包装内容清理完成；
10. 后端 full pytest、前端 lint/build、在线 E2E 全部通过；
11. 所有阶段都有清晰且独立的 Git Commit；
12. 最终 README 的量化数字全部能追溯到 Benchmark result；
13. `git status` clean；
14. 从 README 中写出的每一个核心技术点，都能在实际代码、测试或 Benchmark 中找到对应证据。

达到这个状态以后，再进行最后一步：**根据真实 Benchmark 数据编写简历上的 3～4 条项目描述，并准备项目面试追问稿。**

在此之前，不建议提前固定最终简历数字和表述。
