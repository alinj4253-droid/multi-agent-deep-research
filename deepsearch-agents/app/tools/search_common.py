"""
检索治理通用组件（网页检索与学术检索共用）

- SearchBudget   按会话硬性限制对外检索次数，防止 Agent 陷入无限检索循环
- SearchCache    LRU 语义缓存，相同查询直接返回，减少对外请求
- CircuitBreaker 熔断器，连续失败后短时跳过不可用数据源，避免反复等待超时
"""

import re
import time
from collections import OrderedDict
from typing import Optional


class SearchBudget:
    """
    按 thread_id 统计每个研究任务的实际对外检索次数。

    提示词对模型的检索次数约束是"软约束"，模型可能不严格遵守；
    本类在工具层提供"硬限制"：达到上限后不再请求数据源，
    直接返回引导模型基于已有结果总结的结构化提示。缓存命中不消耗预算。
    """

    def __init__(self, max_per_session: int = 3):
        self.max_per_session = max_per_session
        self._counts: dict[str, int] = {}

    def reset(self, thread_id: str) -> None:
        """任务开始时重置该会话的计数"""
        self._counts.pop(thread_id, None)

    def remaining(self, thread_id: Optional[str]) -> int:
        """该会话剩余可用检索次数；无会话上下文时不限制"""
        if not thread_id:
            return self.max_per_session
        return max(0, self.max_per_session - self._counts.get(thread_id, 0))

    def consume(self, thread_id: Optional[str]) -> int:
        """消耗一次检索预算，返回消耗后的已用次数"""
        if not thread_id:
            return 0
        count = self._counts.get(thread_id, 0) + 1
        self._counts[thread_id] = count
        # 简单防护：字典过大时清理最早的一批（按插入顺序）
        if len(self._counts) > 200:
            for k in list(self._counts.keys())[:100]:
                self._counts.pop(k, None)
        return count


class _CacheEntry:
    """缓存条目：value + 创建时间，用于 TTL 过期判定"""

    __slots__ = ("value", "created_at")

    def __init__(self, value, created_at: float):
        self.value = value
        self.created_at = created_at


class SearchCache:
    """
    标准化查询结果 LRU 缓存（Normalized Query LRU Cache）。

    注意：这里只是对查询字符串做 strip/lower/合并空白后的精确匹配，
    不是语义/向量缓存——两个"意思相近但字面不同"的查询不会互相命中。
    缓存条目带创建时间，读取时按 TTL 判定是否过期；过期视为未命中，
    触发真实请求。缓存命中不消耗检索预算。

    key 必须由调用方拼上所有会影响结果的参数（如 query/region/max_results、
    year_from/sources/max_per_source），不能只用查询词，否则不同参数会串结果。
    """

    def __init__(self, max_size: int = 50, default_ttl: Optional[float] = None):
        self.cache: "OrderedDict" = OrderedDict()
        self.max_size = max_size
        self.default_ttl = default_ttl

    @staticmethod
    def normalize_query(query: str) -> str:
        """标准化查询：小写 + 合并空白，仅做字面归一，不做语义匹配"""
        return re.sub(r"\s+", " ", query.strip().lower())

    def get(self, key, ttl: Optional[float] = None, now: Optional[float] = None):
        """按 key 读取缓存；过期或缺返回 None（视为未命中）"""
        if key not in self.cache:
            return None
        entry = self.cache[key]
        self.cache.move_to_end(key)

        effective_ttl = self.default_ttl if ttl is None else ttl
        if effective_ttl is not None:
            now = now if now is not None else time.time()
            if now - entry.created_at > effective_ttl:
                del self.cache[key]
                return None
        return entry.value

    def set(self, key, value, now: Optional[float] = None):
        """写入缓存（LRU 淘汰最久未用项）"""
        created_at = now if now is not None else time.time()
        if key in self.cache:
            self.cache.move_to_end(key)
        self.cache[key] = _CacheEntry(value=value, created_at=created_at)
        while len(self.cache) > self.max_size:
            self.cache.popitem(last=False)


class CircuitBreaker:
    """
    简易熔断器。

    连续失败达到 threshold 次后"开路"：在 cooldown 秒内 allow() 返回 False，
    调用方应直接跳过该数据源、走降级路径，避免在不可用依赖上反复等待网络超时；
    冷却结束后进入"半开"，放行一次试探，成功则闭合、失败则重新计时。
    """

    def __init__(self, threshold: int = 3, cooldown: float = 60.0):
        self.threshold = threshold
        self.cooldown = cooldown
        self.failures = 0
        self.opened_at: Optional[float] = None

    def allow(self, now: Optional[float] = None) -> bool:
        """当前是否允许尝试该数据源"""
        now = now if now is not None else time.time()
        if self.failures < self.threshold:
            return True
        # 开路后冷却结束 -> 半开，放行一次试探
        if self.opened_at is not None and now - self.opened_at >= self.cooldown:
            return True
        return False

    def record_success(self) -> None:
        """一次成功调用：闭合熔断"""
        self.failures = 0
        self.opened_at = None

    def record_failure(self, now: Optional[float] = None) -> None:
        """一次失败调用：累计失败数，达到阈值则开路"""
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = now if now is not None else time.time()

    def reset(self) -> None:
        self.failures = 0
        self.opened_at = None

    @property
    def is_open(self) -> bool:
        return not self.allow()
