"""SSE 帧格式化。"""
import json

def event_frame(data: dict) -> str:
    return "data: " + json.dumps(data, ensure_ascii=False) + "\n\n"

def sse_stream(events: list[dict]) -> str:
    return "".join(event_frame(e) for e in events)