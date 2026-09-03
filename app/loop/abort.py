"""回合中止通道(前端"停止"按钮的后端落点):进程内、易失、非事实源。

为什么需要显式通道(而不是只靠前端 abort()):
  Starlette 的 StreamingResponse 对同步生成器走 `iterate_in_threadpool`,
  实测该版本(见 rag_env 的 starlette/concurrency.py)是**异步生成器且没有
  `finally: as_iterator.close()`** —— 客户端断开时**不保证** close() 底层
  `loop.turn` 生成器,GeneratorExit 可能永不触发,后端会把这一轮跑完(白烧
  token),前端只是"假停"。所以停止必须走显式置位:
    POST /api/sessions/{sid}/abort → request(sid)
    loop 在 **每个 step 边界** 与 **每个流式 chunk 之后** 检查 is_set(sid),
    命中即就地收尾(保留已流出的部分回答)并照常写 turn_end(reason=interrupted)。

生命周期(顺序很重要,防竞态):
  1. reserve(sid)  —— prompt 路由在**返回 StreamingResponse 之前**(handler 同步段)调用。
     此时 SSE 生成器尚未开始迭代(它要等客户端首拉 body),先把"进行中回合"登记好。
     不提前预约的话,用户在 LLM 首 token 前点停止 → abort 到达时还没任何登记 →
     置位会丢 → turn 白跑(实测:46s)。reserve 每次新建回合都清掉旧标记
     (prompt 严格串行,旧 turn 要么已结束要么已作废,其上遗留的 abort 应丢弃)。
  2. begin(sid)    —— run_prompt 生成器首 next 时兜底调用。幂等:若用户已秒停
     (Event 已置位)则**尊重不清**;否则保持未置位。
  3. request(sid)  —— abort 路由。**没有登记**(没有进行中/刚预约的回合)→ 无效,
     返回 False 且不创建 —— 否则残留位会在用户下一条消息时误停新回合。
  4. clear(sid)    —— turn 结束(run_prompt finally)回收。

定位:纯控制信号,不落 SQLite(易失;进程重启后回合本身也不存在了)。
不违反"SQLite = 事实源"——事实(事件、turn_end)照旧入库,这里只存"要不要停"。
"""
from __future__ import annotations

import threading

_events: dict[str, threading.Event] = {}
_lock = threading.Lock()


def reserve(sid: str) -> None:
    """回合预约(prompt 路由 handler 同步段):登记并清位。必须在任何 abort 到达前执行。"""
    with _lock:
        ev = _events.get(sid)
        if ev is None:
            _events[sid] = threading.Event()
        else:
            ev.clear()


def begin(sid: str) -> None:
    """生成器首 next 兜底(兼容不经过路由/无 reserve 的调用方)。幂等:
    已置位 = 用户在首 token 前就点了停止 → 尊重(不清),让首个检查点立即停;
    未置位则保持(清掉的是不可能存在的旧标记,实际每轮结束都 clear 了)。"""
    with _lock:
        ev = _events.get(sid)
        if ev is None:
            _events[sid] = threading.Event()
        elif not ev.is_set():
            ev.clear()


def request(sid: str) -> bool:
    """请求中止。返回 False = 该会话没有登记中的回合 → 无效 abort,不创建、不残留。
    (若这里随手创建置位,而此刻并无进行中的 turn,残留位会让该会话下一条
    消息在首个检查点被误停 —— 必须由 reserve 在 prompt 时创建。)"""
    with _lock:
        ev = _events.get(sid)
    if ev is None:
        return False
    ev.set()
    return True


def is_set(sid: str) -> bool:
    with _lock:
        ev = _events.get(sid)
    return bool(ev and ev.is_set())


def clear(sid: str) -> None:
    """回合结束回收(不留残余状态)。"""
    with _lock:
        _events.pop(sid, None)
