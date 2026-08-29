"""AgentLoop (真 ReAct): 循环步,LLM 可输出 retrieve 或 answer,直到 answer。

照 dsh core/agent-loop 的 turn/step 循环(agent.ts):
- 每步让 LLM 输出 JSON {action:'retrieve'|'answer', query?, answer?, citations?}。
- action=retrieve → 执行 search_knowledge → 结果追加到上下文(history) → step+1 再调 LLM。
- action=answer → 输出最终回答 + citations → 结束。
- 受 max_steps_per_turn 上限。

铁律:模型可见 ⟺ 已记录(检索片段/回答/引用/工具调用/每步 chunk 都写会话日志)。
stream_run(session_id, text) 是生成器:每写一条事件就 yield 一条,供 SSE 实时推流。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Iterator

from app.llm.structured import parse_answer
from app.retrieval.search_tool import search_knowledge

logger = logging.getLogger("insurance.agent")

SYSTEM = (
    "你是保险销售知识助手。通过多步循环回答客服问题,每一步只输出一个 JSON。\n"
    "每步你必须决定下一步动作:\n"
    "- 若需要更多知识库资料(问题涉及具体产品/条款/责任/免赔额/理赔等) → 输出{\"action\":\"retrieve\",\"query\":\"<检索词>\"}。\n"
    "- 若已有足够资料,或这是寒暄/常识问题 → 输出最终回答{\"action\":\"answer\",\"answer\":[...],\"citations\":[...]}。\n"
    "可多次 retrieve 直到资料充分。只有当你认为已有足够依据回答时才输出 answer。\n"
    "answer 是数组,每元素块 {t:'p'|'h'|'ul', text 或 items};要点用 t:'ul',items 为字符串数组;小标题 t:'h';段落 t:'p'。\n"
    "引用要克制:只在支撑具体结论/数字/前提处标 [idx];同一来源多条要点只标一次;citations 给出对应 chunk_id。\n"
    "资料不足可继续 retrieve,不要编造。若最终确实查不到就明确说不知道。"
)


class AgentLoop:
    def __init__(self, store, embedder, qstore, llm, cfg):
        self.store = store
        self.embedder = embedder
        self.qstore = qstore
        self.llm = llm
        self.cfg = cfg

    def _retrieve(self, query: str) -> list[dict]:
        return search_knowledge(self.embedder, self.qstore, query,
                                top_k=self.cfg.top_k, top_rerank=self.cfg.top_k_reranker)

    def _format(self, chunks: list[dict]) -> str:
        if not chunks:
            return "（无检索资料）"
        parts = []
        for i, c in enumerate(chunks, 1):
            parts.append(f"[{i}] (chunk_id: {c['chunk_id']} | {c['doc_id']} {c.get('version', '')}) {c['content']}")
        return "\n\n".join(parts)

    def _resolve(self, parsed: dict, chunks: list[dict]) -> list[dict]:
        by_idx = {i + 1: c["chunk_id"] for i, c in enumerate(chunks)}
        valid = set(by_idx.values())
        out, seen = [], set()
        for c in parsed.get("citations", []):
            if isinstance(c, str):
                c = {"chunk_id": c, "idx": None}
            if not isinstance(c, dict):
                continue
            cid = c.get("chunk_id")
            if cid not in valid and c.get("idx") in by_idx:
                cid = by_idx[c["idx"]]
            if cid in valid and cid not in seen:
                out.append({"idx": c.get("idx"), "chunk_id": cid})
                seen.add(cid)
        return out

    def _emit(self, session_id: str, type_: str, payload: dict) -> dict:
        from app.session.events import make_event
        ev = make_event(type_, payload)
        self.store.append(session_id, type_, payload)
        return ev

    def _parse_step(self, raw_text: str) -> dict:
        """解析一步 LLM 输出为 {action, query, answer, citations}。失败保守默认 answer。"""
        try:
            obj = json.loads(raw_text)
        except Exception:
            obj = None
        if isinstance(obj, dict):
            action = obj.get("action")
            if action == "retrieve":
                return {"action": "retrieve", "query": (obj.get("query") or "").strip()}
            if action == "answer":
                blocks = obj.get("answer") or []
                if not isinstance(blocks, list):
                    blocks = [{"t": "p", "text": str(blocks)}]
                norm = []
                for b in blocks:
                    if not isinstance(b, dict):
                        norm.append({"t": "p", "text": str(b)})
                    else:
                        t = b.get("t", "p")
                        if t in ("ul", "ol"):
                            norm.append({"t": t, "items": [str(x) for x in (b.get("items") or []) if x is not None]})
                        elif t == "r":
                            norm.append({"t": "r", "text": str(b.get("text") or "")})
                        else:
                            norm.append({"t": "p", "text": str(b.get("text") or "")})
                cites = [c for c in (obj.get("citations") or []) if isinstance(c, dict)]
                return {"action": "answer", "answer": norm, "citations": cites}
        # 兜底:若整体是纯 JSON 输出(无 action 标记),当作 answer
        parsed = parse_answer(raw_text)
        if parsed.get("blocks") or parsed.get("citations"):
            return {"action": "answer", "answer": parsed.get("blocks"), "citations": parsed.get("citations")}
        return {"action": "answer", "answer": [{"t": "p", "text": raw_text}], "citations": []}

    def stream_run(self, session_id: str, text: str) -> Iterator[dict]:
        t0 = time.time()
        logger.info("turn start sid=%s text=%r", session_id, text)
        yield self._emit(session_id, "turn_start", {"turn": 1})
        yield self._emit(session_id, "user_message", {"text": text, "client_time": None})

        # 多轮历史(排除当前 text 的 user;含历史 assistant 摘要)
        prior_history = []
        for e in self.store.read(session_id):
            if e["type"] == "user_message" and e["payload"].get("text") != text:
                prior_history.append({"role": "user", "content": e["payload"]["text"]})
            elif e["type"] == "assistant_message":
                blocks = e["payload"].get("blocks", [])
                prior_history.append({"role": "assistant", "content": "\n".join(
                    b.get("text", "") if b.get("t") != "ul" else "\n".join(b.get("items", []))
                    for b in blocks)})

        # 本 turn 内累积的检索资料上下文
        retrieved_docs: list[dict] = []
        n_steps = 0
        max_steps = int(self.cfg.max_steps_per_turn)
        final_blocks = None
        final_citations = []
        final_reasoning = []

        while True:
            n_steps += 1
            if n_steps > max_steps:
                logger.warning("max_steps=%d reached, forcing answer", max_steps)
                final_blocks = final_blocks or [{"t": "p", "text": "已达到回答步数上限,请补充资料后再试。"}]
                break
            yield self._emit(session_id, "step_start", {"turn": 1, "step": n_steps})

            # 组装这一步的 messages:system + 历史 + 检索结果 + 问题
            msgs = [{"role": "system", "content": SYSTEM}] + prior_history
            if retrieved_docs:
                msgs.append({"role": "system", "content": "【已检索资料】\n" + self._format(retrieved_docs)})
            msgs.append({"role": "user", "content": "【问题】" + text})

            # 流式取这一步输出
            _text_buf, _reason_buf = [], []
            _first_ttft = None
            _started = time.time()
            for piece in self.llm.chat_stream(msgs, json_mode=True):
                _kind = piece.get("kind", "text")
                _delta = piece["delta"]
                _ttft = piece.get("ttft_ms")
                if _kind == "reasoning":
                    _reason_buf.append(_delta)
                elif _kind == "text":
                    _text_buf.append(_delta)
                if _ttft is not None and _first_ttft is None:
                    _first_ttft = _ttft
                yield {"type": "assistant_chunk", "payload": {"kind": _kind, "delta": _delta, "ttft_ms": _ttft}}
                self._emit(session_id, "assistant_chunk", {"kind": _kind, "delta": _delta})

            raw_text = "".join(_text_buf)
            step_reasoning = "".join(_reason_buf)
            if step_reasoning.strip():
                final_reasoning.append(step_reasoning)
            step = self._parse_step(raw_text)
            logger.info("step%d action=%s", n_steps, step.get("action"))

            if step.get("action") == "retrieve":
                query = step.get("query") or text
                yield self._emit(session_id, "tool_call", {"tool": "search_knowledge", "args": {"query": query}})
                logger.info("tool search_knowledge query=%r", query)
                chunks = self._retrieve(query)
                yield self._emit(session_id, "tool_result", {"tool": "search_knowledge", "ok": bool(chunks),
                                                             "result_truncated": bool(chunks),
                                                             "error": None if chunks else "no_hits"})
                if chunks:
                    retrieved_docs.extend(chunks)
                    yield self._emit(session_id, "retrieval", {"query": query, "chunks": chunks})
                yield self._emit(session_id, "step_end", {"turn": 1, "step": n_steps})
                continue  # 继续下一 step(LLM 在下一 step 能看到检索结果)

            # action == answer
            final_blocks = step.get("answer")
            final_citations = self._resolve({"citations": step.get("citations", [])}, retrieved_docs) if retrieved_docs else []
            yield self._emit(session_id, "assistant_message", {
                "blocks": final_blocks or [{"t": "p", "text": raw_text}],
                "citations": final_citations})
            yield self._emit(session_id, "step_end", {"turn": 1, "step": n_steps})
            break

        _run_ms = int((time.time() - t0) * 1000)
        yield self._emit(session_id, "usage", {
            "model": self.cfg.deepseek_model, "prompt_tokens": 0, "completion_tokens": 0,
            "cost_estimate": None, "ttft_ms": _first_ttft, "run_ms": _run_ms,
            "tokens_per_second": None})
        yield self._emit(session_id, "turn_end", {"turn": 1, "reason": "completed",
                                                  "elapsed_ms": _run_ms})
        logger.info("turn end sid=%s steps=%d", session_id, n_steps)

    def run(self, session_id: str, text: str) -> dict:
        for _ev in self.stream_run(session_id, text):
            pass
        last = None
        for e in self.store.read(session_id):
            if e["type"] == "assistant_message":
                last = e["payload"]
        blocks = (last or {}).get("blocks", [])
        citations = (last or {}).get("citations", [])
        answer = "\n".join(b.get("text", "") if b.get("t") != "ul" else "\n".join(b.get("items", []))
                            for b in blocks)
        return {"answer": answer, "citations": citations, "chunks": []}
