# -*- coding: utf-8 -*-
"""进程内 TTL 缓存(观测派生指标专用):读多算少、允许短暂过期的场景。

定位:观测大盘(/api/metrics 等)是**派生只读**指标,重算要扫几十万行 events;
观测页不是实时大盘,≤TTL 秒的过期完全可接受 → 重复打开页面直接命中缓存。

取舍(照 D50"自建轻量"+黄金法则"缓存是可丢层"):
- 单机单实例前提 → 进程内 dict 即可,不上 Redis(重启丢缓存 = 一次重算,无害);
- producer 在锁外执行(两个并发未命中会各算一次,后写覆盖),避免持锁 1s 阻塞别的键;
- 只服务少数固定键(metrics/timeseries/anomalies/observability),不做容量淘汰。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable


class TTLCache:
    """最简 TTL 缓存:get_or_put(key, producer, ttl) 命中即返回,未命中计算并缓存。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._store: dict[str, tuple[float, Any]] = {}   # key -> (monotonic_ts, value)

    def get_or_put(self, key: str, producer: Callable[[], Any], ttl_seconds: float) -> Any:
        """ttl<=0 → 直接计算不缓存(关闭开关);命中未过期 → 返回缓存值。"""
        ttl = float(ttl_seconds or 0)
        if ttl > 0:
            now = time.monotonic()
            with self._lock:
                hit = self._store.get(key)
                if hit is not None and now - hit[0] < ttl:
                    return hit[1]
        val = producer()
        if ttl > 0:
            with self._lock:
                self._store[key] = (time.monotonic(), val)
        return val

    def clear(self) -> None:
        """清空(测试/手动失效用)。"""
        with self._lock:
            self._store.clear()
