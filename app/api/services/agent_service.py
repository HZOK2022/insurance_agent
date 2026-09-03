"""agent 运行层:用"核心 AgentLoop + 保险业务层"跑一个回合,产销事件序列(供 SSE 推流)。"""
from typing import Iterator

from app.guardrails.injection import detect_injection, GUARD_CAUTION, mask_system_leak, looks_like_system_leak
from app.loop.abort import begin as begin_abort, clear as clear_abort, is_set as abort_is_set
from app.loop.agent_loop import AgentLoop
from app.session.context import build_history
from app.session.events import make_event
from app.session.store import SessionStore


def _fresh_cfg():
    # 动态读 .env 的最新配置(.env 是配置事实源,改它应立即生效);重单例已在 container 缓存,
    # 这里的 cfg 只给核心循环的窗口/压缩阈值等动态值用。
    from app.api.services import container
    return container.get_cfg()


def run_prompt(store: SessionStore, llm, bundle: dict, session_id: str, text: str, model: str | None = None) -> Iterator[dict]:
    """真流式:构造会话绑定的核心 AgentLoop(业务层注入 system/tools/present_answer/force_answer),
    emit 落库;调用 turn(),逐个 yield 事件(含 assistant_chunk),供 SSE 逐帧推流。"""
    def emit(type_: str, payload: dict) -> dict:
        ev = make_event(type_, payload)
        store.append(session_id, type_, payload)
        return ev
    # 阶段 A:构建跨轮历史(剥掉旧 [idx] 角标,防跨轮编号混淆,D55)。在 loop 写入当前 user_message 之前调用。
    history = build_history(store, session_id)
    from app.api.services import container as _container   # 局部导入,避免与 container 模块循环引用
    sys_msg = bundle["system"]
    # ① 输入注入护栏:命中疑似注入/越权指令 → 追加安全提示到 system + 记 guard_triggered(审计)
    if detect_injection(text):
        sys_msg = sys_msg + "\n" + GUARD_CAUTION
        emit("guard_triggered", {"kind": "injection", "detail": "检测到疑似提示注入/越权指令,已追加安全提示"})
    # 跨会话记忆(非侵入增强,D52):memory_enabled 时拼"指令+常驻记忆帧"到 system;关/未接入则原样
    if bundle.get("memory_system"):
        from app.memory.tools import build_memory_frame
        frame = build_memory_frame(bundle, store, session_id, _fresh_cfg())
        if frame:
            sys_msg = sys_msg + "\n\n" + frame
            try:
                emit("memory_injected", {"scope": "常驻", "count": 1, "tokens": None,
                                         "user_id": (store.get_session(session_id) or {}).get("user_id")})
            except Exception:
                pass
    # 显式"停止"通道:回合开始清位;loop 在每个 step 边界 / 每个 chunk 之后检查(不依赖客户端断开,
    # 见 app/loop/abort.py 注释 —— Starlette 不保证断开时 close 底层生成器)。
    begin_abort(session_id)
    loop = AgentLoop(llm, sys_msg, bundle["tools"], bundle["present_answer"],
                     _fresh_cfg(), emit=emit, force_answer=bundle.get("force_answer"), model=model,
                     approval=_container.get_approval(),
                     should_abort=lambda: abort_is_set(session_id))
    # ① 输出护栏:回答若泄漏系统签名片段 → 掩码 + 记 guard_triggered(审计)
    try:
        for ev in loop.turn(session_id, text, history=history):
            if ev["type"] == "assistant_message":
                blocks = (ev["payload"] or {}).get("blocks") or []
                if looks_like_system_leak(str(blocks), bundle["system"]):
                    new_blocks, _m = mask_system_leak(blocks, bundle["system"])
                    ev["payload"]["blocks"] = new_blocks
                    emit("guard_triggered", {"kind": "system_leak", "detail": "输出疑似泄漏系统提示,已掩码"})
            yield ev
    finally:
        clear_abort(session_id)
