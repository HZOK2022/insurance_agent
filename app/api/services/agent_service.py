"""agent 运行层:用"核心 AgentLoop + 保险业务层"跑一个回合,产销事件序列(供 SSE 推流)。"""
from typing import Iterator

from app.loop.agent_loop import AgentLoop
from app.session.events import make_event
from app.session.store import SessionStore


def run_prompt(store: SessionStore, llm, bundle: dict, session_id: str, text: str) -> Iterator[dict]:
    """真流式:构造会话绑定的核心 AgentLoop(业务层注入 system/tools/present_answer/force_answer),
    emit 落库;调用 turn(),逐个 yield 事件(含 assistant_chunk),供 SSE 逐帧推流。"""
    def emit(type_: str, payload: dict) -> dict:
        ev = make_event(type_, payload)
        store.append(session_id, type_, payload)
        return ev
    loop = AgentLoop(llm, bundle["system"], bundle["tools"], bundle["present_answer"],
                     bundle["cfg"], emit=emit, force_answer=bundle.get("force_answer"))
    for ev in loop.turn(session_id, text):
        yield ev
