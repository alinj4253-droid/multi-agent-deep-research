"""
Phase 3 测试：run_deep_agent 的执行契约。

用 fake agent（astream 异步迭代器）替代真实 LLM，验证：
- 成功时返回 AgentRunResult（含 final_answer / artifacts）；
- 普通异常：emit error 后向上抛出（不伪装成正常返回）；
- 取消：emit cancelled 后向上抛出 CancelledError；
- Runtime 层对非法 session_id 做 defense-in-depth 校验。
"""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import AIMessage

from app.agent import main_agent as ma
from app.agent.result import AgentRunResult


class FakeAgent:
    """按预设脚本产出 chunk 或抛异常的假 agent。"""

    def __init__(self, script):
        # script: list，元素为 chunk(dict) 或 Exception 实例（在迭代时抛出）
        self.script = script

    def astream(self, inputs, config=None):
        script = self.script

        async def gen():
            for item in script:
                if isinstance(item, BaseException):
                    raise item
                yield item

        return gen()


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    # run_deep_agent 用 project_root_path/output/session_<id> 作为工作目录
    monkeypatch.setattr(ma, "project_root_path", tmp_path)
    yield tmp_path


def test_success_returns_result(workdir, monkeypatch):
    # 在会话目录放一个产物文件，验证 artifacts 汇总
    async def setup_and_run():
        # 预置一个 agent：先工具消息，后最终回答
        chunk = {"model": {"messages": [AIMessage(content="最终研究结论：一切正常")]}}
        monkeypatch.setattr(ma, "main_agent", FakeAgent([chunk]))
        result = await ma.run_deep_agent("测试问题", "thread_contract_1")
        return result

    result = _run(setup_and_run())
    assert isinstance(result, AgentRunResult)
    assert result.status == "completed"
    assert result.final_answer == "最终研究结论：一切正常"
    assert result.session_id == "thread_contract_1"
    # 会话目录被创建
    assert (workdir / "output" / "session_thread_contract_1").is_dir()


def test_success_lists_artifacts(workdir, monkeypatch):
    async def run():
        chunk = {"model": {"messages": [AIMessage(content="done")]}}
        monkeypatch.setattr(ma, "main_agent", FakeAgent([chunk]))
        result = await ma.run_deep_agent("q", "thread_art")
        return result

    # 让产物文件在任务运行期间存在：通过在 astream 里写文件更真实，
    # 这里改为预置目录与文件（run 会 mkdir(exist_ok)）
    # 预置会话目录与文件（run 会 mkdir(exist_ok)）
    sess = workdir / "output" / "session_thread_art"
    sess.mkdir(parents=True, exist_ok=True)
    (sess / "report.md").write_text("# r", encoding="utf-8")
    (sess / "_exec_abcd1234.py").write_text("tmp", encoding="utf-8")  # 应被排除

    result = _run(run())
    assert "report.md" in result.artifacts
    assert not any(a.startswith("_exec_") for a in result.artifacts)


def test_generic_exception_is_raised(workdir, monkeypatch):
    async def run():
        monkeypatch.setattr(
            ma, "main_agent", FakeAgent([RuntimeError("模型服务 500")])
        )
        await ma.run_deep_agent("q", "thread_err")

    with pytest.raises(RuntimeError, match="500"):
        _run(run())


def test_cancellation_is_raised(workdir, monkeypatch):
    async def run():
        monkeypatch.setattr(
            ma, "main_agent", FakeAgent([asyncio.CancelledError()])
        )
        await ma.run_deep_agent("q", "thread_cancel")

    with pytest.raises(asyncio.CancelledError):
        _run(run())


def test_runtime_rejects_invalid_session_id(workdir, monkeypatch):
    monkeypatch.setattr(ma, "main_agent", object())  # 非 None，绕过初始化检查

    async def run_bad():
        await ma.run_deep_agent("q", "../escape")

    with pytest.raises(ValueError):
        _run(run_bad())

    async def run_empty():
        await ma.run_deep_agent("q", "")

    with pytest.raises(ValueError):
        _run(run_empty())
