"""agent 运行层:用"核心 AgentLoop + 保险业务层"跑一个回合,产销事件序列(供 SSE 推流)。"""
from typing import Iterator

from app.loop.agent_loop import AgentLoop
from app.session.context import build_history, build_chunk_registry
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
    # 阶段 A:构建跨轮历史(保留 [idx],供跨轮引用)。在 loop 写入当前 user_message 之前调用。
    history = build_history(store, session_id)
    # 会话级 chunk 注册表(全局编号):上下文回答(当轮无检索)也能解析 [idx] 到历史 chunk。
    citation_pool, citation_idx = build_chunk_registry(store, session_id)
    from app.api.services import container as _container   # 局部导入,避免与 container 模块循环引用
    loop = AgentLoop(llm, bundle["system"], bundle["tools"], bundle["present_answer"],
                     _fresh_cfg(), emit=emit, force_answer=bundle.get("force_answer"), model=model,
                     approval=_container.get_approval())
    for ev in loop.turn(session_id, text, history=history, citation_pool=citation_pool, citation_idx=citation_idx):
        yield ev
