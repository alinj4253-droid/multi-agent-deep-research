"""
网络搜索工具核心组件的单元测试

覆盖三个不依赖网络 / 大模型的纯逻辑组件：
1. SearchCache：LRU 缓存的存取、标准化命中与容量淘汰
2. rerank_results：基于关键词匹配度的结果重排序
3. SearchBudget：按会话的检索次数硬预算
"""

from app.tools.web_search_tool import (
    SearchBudget,
    SearchCache,
    rerank_results,
)


# ============================================================
# SearchCache
# ============================================================
class TestSearchCache:
    def test_set_and_get_hit(self):
        cache = SearchCache(max_size=3)
        payload = {"results": [{"title": "a"}]}
        cache.set("qwen", payload)
        assert cache.get("qwen") == payload

    def test_get_miss_returns_none(self):
        cache = SearchCache()
        assert cache.get("not-exist") is None

    def test_query_normalization(self):
        """大小写差异与多余空白应视为同一查询：由 normalize_query 归一后再作 key"""
        cache = SearchCache()
        payload = {"results": [{"title": "x"}]}
        key = SearchCache.normalize_query("  Gaussian  Splatting ")
        cache.set(key, payload)
        other_key = SearchCache.normalize_query("gaussian splatting")
        assert cache.get(other_key) is payload

    def test_lru_eviction(self):
        """超过容量后，最久未访问的条目被淘汰"""
        cache = SearchCache(max_size=2)
        cache.set("a", 1)
        cache.set("b", 2)
        # 访问 a，使 b 成为最久未使用
        assert cache.get("a") == 1
        cache.set("c", 3)
        assert cache.get("b") is None  # b 被淘汰
        assert cache.get("a") == 1
        assert cache.get("c") == 3


# ============================================================
# rerank_results
# ============================================================
class TestRerank:
    def test_empty_and_single_passthrough(self):
        assert rerank_results("q", []) == []
        single = [{"title": "only", "content": "x"}]
        assert rerank_results("q", single) == single

    def test_title_match_ranked_first(self):
        query = "gaussian splatting"
        irrelevant = {"title": "今日天气晴朗", "content": " unrelated text "}
        relevant = {
            "title": "3D Gaussian Splatting 最新进展",
            "content": "介绍三维高斯泼溅技术",
        }
        ranked = rerank_results(query, [irrelevant, relevant])
        assert ranked[0] is relevant
        assert ranked[1] is irrelevant

    def test_returns_same_number_of_items(self):
        items = [
            {"title": f"result {i}", "content": "alpha beta gamma"}
            for i in range(5)
        ]
        ranked = rerank_results("alpha", items)
        assert len(ranked) == 5


## ============================================================
# SearchBudget
# ============================================================
class TestSearchBudget:
    def test_initial_remaining(self):
        budget = SearchBudget(max_per_session=3)
        assert budget.remaining("thread-1") == 3

    def test_consume_decrements(self):
        budget = SearchBudget(max_per_session=3)
        used = budget.consume("thread-1")
        assert used == 1
        assert budget.remaining("thread-1") == 2
        budget.consume("thread-1")
        budget.consume("thread-1")
        assert budget.remaining("thread-1") == 0

    def test_remaining_never_negative(self):
        budget = SearchBudget(max_per_session=1)
        budget.consume("t")
        budget.consume("t")
        assert budget.remaining("t") == 0

    def test_reset_restores_budget(self):
        budget = SearchBudget(max_per_session=2)
        budget.consume("t")
        budget.consume("t")
        assert budget.remaining("t") == 0
        budget.reset("t")
        assert budget.remaining("t") == 2

    def test_threads_isolated(self):
        """不同会话的预算计数必须相互独立，不能串台"""
        budget = SearchBudget(max_per_session=2)
        budget.consume("A")
        assert budget.remaining("A") == 1
        assert budget.remaining("B") == 2

    def test_no_thread_context_unlimited(self):
        """缺少会话上下文（如脚本直接调用）时不做预算限制"""
        budget = SearchBudget(max_per_session=1)
        assert budget.remaining(None) == 1
        budget.consume(None)
        assert budget.remaining(None) == 1
