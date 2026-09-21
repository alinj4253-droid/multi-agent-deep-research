"""
Phase 2 测试：LLM 模型延迟到 runtime 才初始化（import 无副作用）。

验证：
- 单纯 import app.agent.llm 不创建模型、不读取/校验密钥（CI 无 Key 也能收集用例）；
- get_main_model()/get_fast_model() 真正调用时才读取配置并构造模型；
- 缺少 OPENAI_API_KEY 时抛出明确、可操作的 RuntimeError；
- 模型在进程内通过 lru_cache 复用；
- 三个子智能体在 runtime 构建时才解析 fast model，可用 monkeypatch 注入 fake model；
- init_main_agent() 把懒加载得到的主/子模型正确装配进 create_deep_agent。
"""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent import llm
from app.agent import main_agent as ma
from app.agent.subagents import academic_literature_agent as acad
from app.agent.subagents import data_analysis_agent as data
from app.agent.subagents import network_search_agent as net


@pytest.fixture(autouse=True)
def _clear_model_cache():
    """每个用例前后清空懒加载缓存，避免缓存的模型串扰。"""
    llm.get_main_model.cache_clear()
    llm.get_fast_model.cache_clear()
    yield
    llm.get_main_model.cache_clear()
    llm.get_fast_model.cache_clear()


def test_import_does_not_create_models():
    """模块 import 后不应存在 import 期就实例化好的 model / fast_model。"""
    assert not hasattr(llm, "model")
    assert not hasattr(llm, "fast_model")
    assert llm.get_main_model.cache_info().currsize == 0
    assert llm.get_fast_model.cache_info().currsize == 0


def test_get_main_model_requires_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        llm.get_main_model()


def test_get_fast_model_requires_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        llm.get_fast_model()


def test_get_main_model_reads_config(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-1234567890")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("LLM_MAIN_MODEL", "deepseek-v4-pro")

    captured = {}

    def fake_init(**kwargs):
        captured.update(kwargs)
        return "MAIN_OBJ"

    monkeypatch.setattr(llm, "init_chat_model", fake_init)

    assert llm.get_main_model() == "MAIN_OBJ"
    assert captured["model"] == "deepseek-v4-pro"
    assert captured["model_provider"] == "openai"
    assert captured["api_key"] == "sk-test-1234567890"
    assert captured["base_url"] == "https://api.deepseek.com/v1"


def test_get_fast_model_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-1234567890")
    monkeypatch.delenv("LLM_FAST_MODEL", raising=False)

    captured = {}

    def fake_init(**kwargs):
        captured.update(kwargs)
        return "FAST_OBJ"

    monkeypatch.setattr(llm, "init_chat_model", fake_init)

    assert llm.get_fast_model() == "FAST_OBJ"
    assert captured["model"] == llm._DEFAULT_MODEL


def test_models_are_cached_within_process(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-1234567890")
    calls = {"n": 0}

    def fake_init(**kwargs):
        calls["n"] += 1
        return object()

    monkeypatch.setattr(llm, "init_chat_model", fake_init)

    first = llm.get_main_model()
    second = llm.get_main_model()
    assert first is second
    assert calls["n"] == 1


def test_subagent_builders_resolve_model_at_runtime(monkeypatch):
    """子智能体只在 build_*() 调用时取模型，import 阶段不触发模型初始化。"""
    for module in (net, data, acad):
        monkeypatch.setattr(module, "get_fast_model", lambda: "FAKE_MODEL")

    configs = [
        net.build_network_search_agent(),
        data.build_data_analysis_agent(),
        acad.build_academic_literature_agent(),
    ]
    for cfg in configs:
        assert cfg["model"] == "FAKE_MODEL"
        assert cfg["name"]
        assert cfg["description"]
        assert cfg["system_prompt"]
        assert isinstance(cfg["tools"], list) and cfg["tools"]


def test_init_main_agent_wires_lazy_models(monkeypatch, tmp_path):
    """init_main_agent 在 runtime 取主模型、构建子智能体并装配给 create_deep_agent。"""
    monkeypatch.setattr(ma, "project_root_path", tmp_path)
    monkeypatch.setattr(ma, "get_main_model", lambda: "MAIN_MODEL")
    monkeypatch.setattr(
        ma, "build_data_analysis_agent", lambda: {"name": "data", "model": "FAST"}
    )
    monkeypatch.setattr(
        ma, "build_network_search_agent", lambda: {"name": "net", "model": "FAST"}
    )
    monkeypatch.setattr(
        ma, "build_academic_literature_agent", lambda: {"name": "acad", "model": "FAST"}
    )

    class FakeConn:
        async def close(self):
            return None

    async def fake_connect(*args, **kwargs):
        return FakeConn()

    monkeypatch.setattr(ma.aiosqlite, "connect", fake_connect)
    monkeypatch.setattr(ma, "AsyncSqliteSaver", lambda conn: "CKPT")

    captured = {}

    def fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return "AGENT_INSTANCE"

    monkeypatch.setattr(ma, "create_deep_agent", fake_create_deep_agent)

    asyncio.run(ma.init_main_agent())
    try:
        assert captured["model"] == "MAIN_MODEL"
        assert captured["checkpointer"] == "CKPT"
        assert [s["name"] for s in captured["subagents"]] == [
            "data",
            "net",
            "acad",
        ]
        assert all(s["model"] == "FAST" for s in captured["subagents"])
        assert ma.main_agent == "AGENT_INSTANCE"
    finally:
        ma.main_agent = None
        ma._db_conn = None
