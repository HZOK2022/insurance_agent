# -*- coding: utf-8 -*-
"""进程内滑动窗口限流器(单机单进程):按 client key(源 IP)限制单位窗口内请求数。

黄金法则:单进程内存 = 可丢失的加速层,不持久化;重启即清零(生产多实例需外部存储,本项目单机够用)。
"""
from __future__ import annotations

import threading
import time
from collections import deque


class RateLimiter:
    def __init__(self):
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window: float) -> bool:
        """key 在 [now-window, now] 窗口内请求数 < limit 则放行并计数;否则拒绝。"""
        if limit <= 0:
            return True
        now = time.time()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and q[0] < now - window:
                q.popleft()
            if len(q) < limit:
                q.append(now)
                return True
            return False
