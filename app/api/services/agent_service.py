"""agent 运行层:触发 AgentLoop,产出本回合事件序列(供 SSE 实时推流)。"""
from typing import Iterator

from app.loop.loop import AgentLoop
from app.session.store import SessionStore


def run_prompt(loop: AgentLoop, store: SessionStore, session_id: str, text: str) -> Iterator[dict]:
    """真流式:调用 loop.stream_run,逐个 yield 事件(含 assistant_chunk),供 SSE 逐帧推流。"""
    for ev in loop.stream_run(session_id, text):
        yield ev
