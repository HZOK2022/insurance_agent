"""阶段0:FastAPI 入口,mock 模式(MODE=mock)。

阶段0 只验证契约:POST prompt 后,SSE 依次推送 canned 事件(与契约一致)。
阶段4 起把 mock 换成真实 loop,事件类型不变(模型可见 ⟺ 已记录)。
运行:uvicorn app.main:app --reload --port 8000
"""
import asyncio
import json
import time
import uuid

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI(title="insurance-agent")

MODE = "mock"  # mock | real(阶段4 切换)


class CreateBody(BaseModel):
    user_id: str


class PromptBody(BaseModel):
    text: str
    client_time: str | None = None


def _chunk():
    return {
        "chunk_id": "P10086:v3.2:section4:12", "score": 0.93,
        "doc_id": "P10086", "version": "v3.2", "section": "section4",
        "source": "product/P10086/条款v3.2.pdf",
        "content": "【责任免除】因下列情形之一导致被保险人发生保险事故的,本公司不承担给付保险金责任:……(原文节选)",
    }


@app.get("/api/health")
def health():
    return {"ok": True}


@app.post("/api/sessions")
def create_session(body: CreateBody):
    return {"id": uuid.uuid4().hex[:12], "title": "新会话", "created_at": time.strftime("%Y-%m-%d")}


@app.get("/api/sessions")
def list_sessions():
    return [{"id": "s1", "title": "重疾险责任免除咨询", "created_at": "2026-03-01", "updated_at": "2026-03-01"}]


@app.post("/api/sessions/{sid}/prompt")
def prompt(sid: str, body: PromptBody):
    return {"accepted": True}


@app.get("/api/sessions/{sid}/events")
async def events(sid: str):
    """SSE:依次推送本回合事件(阶段0 mock)。阶段4 换真实 loop,帧格式不变。"""
    events = [
        ("user_message", {"text": "刚才那个问题", "client_time": None}),
        ("retrieval", {"query": "重疾险责任免除", "chunks": [_chunk()]}),
        ("tool_call", {"tool": "search_knowledge", "args": {"query": "责任免除"}}),
        ("tool_result", {"tool": "search_knowledge", "ok": True, "result_truncated": False}),
    ]
    reply = "根据《XX重疾险条款 v3.2》(责任免除,第 4.1 条),您描述的情形属于责任免除范围。[1]"

    async def gen():
        seq = 0
        for t, p in events:
            seq += 1
            yield f"data: {json.dumps({'seq': seq, 'type': t, 'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ'), 'payload': p})}\n\n"
            await asyncio.sleep(0.15)
        # 流式回答
        for i in range(3, len(reply) + 1, 3):
            seq += 1
            yield f"data: {json.dumps({'seq': seq, 'type': 'assistant_chunk', 'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ'), 'payload': {'delta': reply[:i]}})}\n\n"
            await asyncio.sleep(0.05)
        seq += 1
        yield f"data: {json.dumps({'seq': seq, 'type': 'assistant_message', 'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ'), 'payload': {'text': reply, 'citations': [{'idx': 1, 'chunk_id': _chunk()['chunk_id']}]}})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/sessions/{sid}/citation/{chunk_id}")
def citation(sid: str, chunk_id: str):
    ch = _chunk()
    return {"content": ch["content"], "source": ch["source"], "doc_id": ch["doc_id"], "version": ch["version"], "section": ch["section"]}


# 生产:托管前端 build 产物(存在才挂载)
import os
_web_dist = os.path.join(os.path.dirname(__file__), "..", "web", "dist")
if os.path.isdir(_web_dist):
    app.mount("/", StaticFiles(directory=_web_dist, html=True), name="web")
