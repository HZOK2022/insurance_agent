"""agent 运行层:触发 AgentLoop,产出本回合事件序列(供 SSE 推流)。"""
from app.loop.loop import AgentLoop
from app.session.store import SessionStore


def run_prompt(loop: AgentLoop, store: SessionStore, session_id: str, text: str) -> list[dict]:
    loop.run(session_id, text)
    evs = store.read(session_id)
    idx = max((i for i, e in enumerate(evs) if e["type"] == "user_message"), default=-1)
    return evs[idx:] if idx >= 0 else evs