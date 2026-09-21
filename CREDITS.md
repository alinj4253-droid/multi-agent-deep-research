# Credits / 致谢

本项目基于开源教程项目 [didilili/ai-agents-from-zero](https://github.com/didilili/ai-agents-from-zero) 进行本地化与工程化改造，遵循其 MIT 许可证。

## 上游

- **didilili/ai-agents-from-zero**（MIT, Copyright (c) 2026 didilili）
  - 提供了 DeepAgents 多智能体框架、主/子智能体编排、WebSocket + FastAPI 前端交互等基础结构。

## 本项目的主要改造

- 网络检索由付费 Tavily 改为**自建 SearXNG 元搜索**（WSL2 Docker）+ DuckDuckGo 降级，配熔断器、每任务检索预算、查询结果 LRU 缓存（带 TTL）与结果重排序。
- 原 MySQL 数据库助手替换为**受控 Python 子进程执行器**（subprocess 隔离 + 超时 + 会话工作目录限制）。
- 学术检索改为并发聚合 arXiv / OpenAlex / Crossref，跨源去重与按被引排序。
- 会话/线程与文件路径安全：thread_id 白名单、会话工作区路径边界校验。
- 工程化：离线可复现 benchmark、GitHub Actions CI、单元测试。

## 第三方依赖

本项目的运行依赖见 `deepsearch-agents/requirements.txt` 与 `frontend-demo/package.json`，均遵循其各自开源许可证。
