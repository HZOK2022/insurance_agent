# -*- coding: utf-8 -*-
"""写审批中心:发 approval_request 后,等待人工 approval_decision(批准/拒绝/挂起,可改参数、附原因)。

单机单进程:用 threading.Event 让"正在等审批的 turn"在别的请求(前端 POST 决定)里被唤醒。
"""
from __future__ import annotations

import threading
import uuid


class _Pending:
    __slots__ = ("request_id", "tool", "args", "reason", "decision", "event")
    def __init__(self, request_id, tool, args, reason):
        self.request_id = request_id
        self.tool = tool
        self.args = args
        self.reason = reason
        self.decision = None
        self.event = threading.Event()


class ApprovalCenter:
    def __init__(self, timeout: float = 300.0):
        self.timeout = timeout
        self._pending: dict[str, _Pending] = {}
        self._lock = threading.Lock()

    def new_request(self, tool: str, args, reason: str) -> tuple[str, dict]:
        rid = uuid.uuid4().hex[:12]
        req = {"request_id": rid, "tool": tool, "args": args, "reason": reason}
        with self._lock:
            self._pending[rid] = _Pending(rid, tool, args, reason)
        return rid, req

    def decide(self, request_id: str, status: str, edited_args=None,
               reason: str = "", decided_by: str = "user") -> bool:
        """写入决定并唤醒等待者;返回是否找到了对应请求。"""
        with self._lock:
            p = self._pending.get(request_id)
            if p is None:
                return False
            p.decision = {"request_id": request_id, "status": status,
                          "edited_args": edited_args, "reason": reason, "decided_by": decided_by}
            p.event.set()
        return True

    def wait(self, request_id: str, timeout: float | None = None):
        """阻塞等待决定(超时返回 {'status':'timeout'} 或 None)。"""
        with self._lock:
            p = self._pending.get(request_id)
        if p is None:
            return None
        p.event.wait(timeout if timeout is not None else self.timeout)
        return p.decision if p.decision is not None else {"status": "timeout"}
