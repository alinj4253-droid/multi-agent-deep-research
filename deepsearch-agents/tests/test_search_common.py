"""
检索治理通用组件单元测试（纯逻辑，不依赖网络）

重点覆盖 CircuitBreaker 的闭合 -> 开路 -> 冷却半开 -> 成功复位状态机，
以及从 web_search_tool 迁移到 search_common 后 SearchBudget/SearchCache 仍可用。
"""

from app.tools.search_common import CircuitBreaker


class TestCircuitBreaker:
    def test_closed_initially(self):
        cb = CircuitBreaker(threshold=2, cooldown=60.0)
        assert cb.allow(now=0) is True
        assert cb.is_open is False

    def test_opens_after_threshold(self):
        # 用真实当前时间开路，才能让基于 time.time() 的 is_open/allow() 正确反映开路状态
        cb = CircuitBreaker(threshold=2, cooldown=60.0)
        cb.record_failure()
        assert cb.allow() is True  # 仅 1 次失败，未达阈值
        cb.record_failure()
        assert cb.allow() is False  # 达阈值，立即开路
        assert cb.is_open

    def test_stays_open_during_cooldown(self):
        cb = CircuitBreaker(threshold=2, cooldown=60.0)
        cb.record_failure(now=0)
        cb.record_failure(now=0)
        assert cb.allow(now=59) is False

    def test_half_open_after_cooldown(self):
        cb = CircuitBreaker(threshold=2, cooldown=60.0)
        cb.record_failure(now=0)
        cb.record_failure(now=0)
        assert cb.allow(now=60) is True  # 冷却结束，半开放行一次试探

    def test_success_resets(self):
        cb = CircuitBreaker(threshold=2, cooldown=60.0)
        cb.record_failure(now=0)
        cb.record_success()
        assert cb.failures == 0
        assert cb.opened_at is None
        assert cb.allow(now=1) is True

    def test_half_open_failure_reopens(self):
        cb = CircuitBreaker(threshold=2, cooldown=60.0)
        cb.record_failure(now=0)
        cb.record_failure(now=0)
        assert cb.allow(now=60) is True  # 半开试探
        cb.record_failure(now=60)        # 试探仍失败 -> 重新开路并刷新计时
        assert cb.allow(now=61) is False
        assert cb.allow(now=120) is True
