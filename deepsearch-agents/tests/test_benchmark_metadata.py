"""
Phase 3 测试：Benchmark 运行时配置采集（离线，不触网）。

验证结果文件记录的是真实模型 / 预算 / 环境，且绝不泄露 API Key。
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks import runtime_config as rc


class TestResolveModels:
    def test_reads_main_and_fast_model(self):
        env = {"LLM_MAIN_MODEL": "deepseek-v4-pro", "LLM_FAST_MODEL": "deepseek-flash"}
        models = rc.resolve_models(env)
        assert models == {"main": "deepseek-v4-pro", "fast": "deepseek-flash"}

    def test_falls_back_to_default_when_unset(self):
        models = rc.resolve_models({})
        assert models["main"] == rc.DEFAULT_MODEL
        assert models["fast"] == rc.DEFAULT_MODEL

    def test_ignores_legacy_model_vars(self):
        # 旧的 DEEPSEEK_MODEL / OPENAI_MODEL / MODEL_NAME 不应再被使用
        env = {"DEEPSEEK_MODEL": "should-not-use", "MODEL_NAME": "x"}
        models = rc.resolve_models(env)
        assert models["main"] == rc.DEFAULT_MODEL
        assert "should-not-use" not in json.dumps(models)

    def test_fast_falls_back_independently(self):
        models = rc.resolve_models({"LLM_MAIN_MODEL": "big-model"})
        assert models == {"main": "big-model", "fast": rc.DEFAULT_MODEL}


class TestProvider:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://api.deepseek.com/v1", "deepseek"),
            ("https://dashscope.aliyuncs.com/compatible-mode/v1", "dashscope"),
            ("https://api.openai.com/v1", "openai"),
            ("", "unknown"),
        ],
    )
    def test_infer_provider(self, url, expected):
        assert rc.infer_provider(url) == expected


class TestBudgets:
    def test_real_budget_constants(self):
        budgets = rc.resolve_budgets()
        # 必须与代码中真实生效的常量一致（web 3 / academic 2 / python 12）
        assert budgets == {"web": 3, "academic": 2, "python": 12}


class TestCollectMetadata:
    def test_collects_reproducibility_fields(self, tmp_path):
        fake_cases = tmp_path / "cases.json"
        fake_cases.write_text("{}", encoding="utf-8")
        env = {
            "OPENAI_BASE_URL": "https://api.deepseek.com/v1",
            "LLM_MAIN_MODEL": "deepseek-flash",
            "LLM_FAST_MODEL": "deepseek-flash",
        }
        meta = rc.collect_metadata(env=env, cases_path=fake_cases)

        assert meta["models"]["main"] == "deepseek-flash"
        assert meta["models"]["fast"] == "deepseek-flash"
        assert meta["llm"]["provider"] == "deepseek"
        assert meta["budgets"] == {"web": 3, "academic": 2, "python": 12}
        assert isinstance(meta["python_version"], str) and meta["python_version"]
        assert meta["git_commit"]  # 当前仓库内应能取到 SHA，否则为 unknown
        assert len(meta["cases_sha256"]) == 16

    def test_dotenv_loadable_and_serializable(self):
        # 默认从 .env / 进程环境采集，结果必须可 json 序列化
        meta = rc.collect_metadata()
        blob = json.dumps(meta, ensure_ascii=False)
        assert isinstance(blob, str) and "models" in blob

    def test_api_key_never_leaks(self, tmp_path):
        secret = "sk-1234567890abcdefDEADBEEFsecret"
        token = "tok-abcdef1234567890zz"
        env = {
            "OPENAI_BASE_URL": "https://api.deepseek.com/v1",
            "OPENAI_API_KEY": secret,
            "LLM_MAIN_MODEL": "deepseek-flash",
            "LLM_FAST_MODEL": "deepseek-flash",
            "SOME_TOKEN": token,
        }
        # 正常采集不应报错，结果里也绝不能出现密钥 / token
        meta = rc.collect_metadata(env=env, cases_path=tmp_path / "c.json")
        blob = json.dumps(meta)
        assert secret not in blob
        assert token not in blob
        assert "OPENAI_API_KEY" not in blob

    def test_secret_guard_detects_leak(self):
        secret = "sk-1234567890abcdefDEADBEEFsecret"
        env = {"OPENAI_API_KEY": secret}
        # 直接验证守卫：一旦结果载荷里混入密钥值就必须报错
        with pytest.raises(AssertionError):
            rc._assert_no_secret({"leaked": secret}, env)

    def test_git_commit_is_sha_or_unknown(self):
        sha = rc.git_commit_sha()
        assert sha == "unknown" or (len(sha) >= 7 and all(c in "0123456789abcdef" for c in sha))
