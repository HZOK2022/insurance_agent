"""dsh agent-loop 核心的 Python 忠实重构(与保险业务解耦)。

对照 dsh packages/core/agent-loop/src/agent.ts + tool-calls.ts + llm/assembler.ts:
- turn()/step() 状态机;StepEndReason = completed | max-tokens;turn/end reason = completed|error|interrupted|aborted。
- BlockAssembler:流式 chunk → 有序块(text/reasoning/tool-call),容忍 delta-only(无 block-start/end)。
- 原生 tool_calls:assistant 消息含 tool_calls → 执行 → 以 tool 角色消息回喂(next-step 上下文),
  tool_call_id 与 assistant 对齐;DeepSeek V4 多轮工具调用需回传 reasoning_content。
- 取消:signal.aborted → 中断，仍写终结事件(turn/end)，不悬挂。
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from app.utils.text import estimate_tokens, prune_tool_content
from app.retrieval.errors import RetrievalUnavailable
from app.compaction.compactor import (
    prune_tool_messages, select_keep_tail, build_summary_request,
    collect_summary, truncate_summary, frame_summary,
)

logger = logging.getLogger("insurance.agent")


# ---- 块模型(照 dsh ContentBlock)----
@dataclass
class Block:
    kind: str                 # 'text' | 'reasoning' | 'tool-call'
    text: str = field(default="")
    id: str | None = None     # tool-call
    name: str | None = None   # tool-call
    def to_dict(self) -> dict:
        if self.kind == "tool-call":
            return {"type": "tool-call", "id": self.id or "", "name": self.name or "", "arguments": self.text}
        return {"type": self.kind, "text": self.text}


class BlockAssembler:
    """照 dsh BlockAssembler:以 block_index 聚合流式段,按打开顺序输出块。"""

    def __init__(self) -> None:
        self._partials: dict[int, Block] = {}
        self._order: list[int] = []

    def _ensure(self, idx: int, kind: str) -> Block:
        """首块(即使无 block-start)也进 _order —— 否则 delta-only 流 blocks() 为空(照 dsh ensure)。"""
        if idx not in self._partials:
            self._partials[idx] = Block(kind)
            self._order.append(idx)
        return self._partials[idx]

    def push(self, chunk: dict) -> None:
        ctype = chunk.get("type")
        if ctype == "block-start":
            self._ensure(chunk["index"], chunk.get("blockType", "text"))
        elif ctype in ("text-delta", "reasoning-delta"):
            idx = chunk["index"]
            kind = "text" if ctype == "text-delta" else "reasoning"
            b = self._ensure(idx, kind)
            if b.kind == kind:
                b.text += chunk.get("text", "")
        elif ctype == "tool-call-delta":
            idx = chunk["index"]
            b = self._ensure(idx, "tool-call")
            b.id = chunk.get("id", b.id)
            if chunk.get("name"):
                b.name = chunk.get("name")
            b.text += chunk.get("argumentsDelta", "")
        elif ctype == "block-end":
            self._ensure(chunk["index"], "text")
        elif ctype == "usage":
            self.usage = chunk.get("usage")
        elif ctype == "finish":
            self.finish = chunk.get("reason")

    def blocks(self) -> list[Block]:
        return [self._partials[i] for i in self._order if i in self._partials]

    def reasoning_text(self) -> str:
        return "".join(b.text for b in self.blocks() if b.kind == "reasoning")

    def text_blocks(self) -> str:
        return "".join(b.text for b in self.blocks() if b.kind == "text")

    def tool_calls(self) -> list[Block]:
        return [b for b in self.blocks() if b.kind == "tool-call"]


def parse_tool_arguments(raw: str) -> Any:
    try:
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return raw


def _handler_accepts_session(handler) -> bool:
    """handler 是否接受 session_id 参数(D52:会话 id 注入)。不接受的 handler 保持旧调用,避免 TypeError。"""
    try:
        import inspect
        return "session_id" in inspect.signature(handler).parameters
    except Exception:
        return False


# ---- agent loop ----
class AgentLoop:
    """dsh agent-loop 核心:ReAct 循环,业务无关(工具表 + 回答呈现为注入)。"""

    def __init__(self, llm, system: str, tools: dict[str, dict],
                 present_answer: Callable[[str, list], tuple[list, list]], cfg,
                 emit: Callable[[str, dict], dict] | None = None,
                 force_answer: Callable[[list], tuple[list, list]] | None = None,
                 model: str | None = None, approval=None):
        # llm: .chat_stream(messages, json_mode, tools) -> iter chunks
        # system: 业务 prompt
        # tools: {name: {"schema": openai 工具 schema, "handler": fn(args)->{"content":str,"reference":any}}}
        # present_answer(answer_text, references) -> (blocks, citations)  业务层决定"展现形式"(保险=块+溯源引用)
        # cfg: SimpleNamespace(max_steps_per_turn, deepseek_model, ...)  (上限集中在此)
        self.llm = llm
        self.system = system
        self.tools = tools
        self.present_answer = present_answer
        self.cfg = cfg
        self._emit = emit or (lambda t, p: p)
        self.force_answer = force_answer   # 检索达上限强制结束时的业务兜底(如保险的"诚实说明")
        self.model_override = model   # 模型可配置:前端选 deepseek-v4-flash / deepseek-v4-pro
        self.approval = approval      # 写审批中心(None=不门控,兼容旧调用/测试)

    @property
    def _effective_model(self) -> str:
        return self.model_override or getattr(self.cfg, "deepseek_model", "")

    def _llm_retry_kw(self) -> dict:
        """真实 LLMClient(有 max_retries)才传 on_retry(记 llm_retry 事件);fake LLM 不传,避免破测试。"""
        if hasattr(self.llm, "max_retries"):
            return {"on_retry": lambda att: self._emit("llm_retry", att)}
        return {}

    def _tools_schemas(self) -> list[dict] | None:
        schemas = [t["schema"] for t in self.tools.values()]
        return schemas or None

    def _stream_blocks(self, msgs: list[dict]) -> Iterator[dict]:
        """流式取块:走 llm.chat_stream,产出标准化 chunk 供 BlockAssembler + 推送。"""
        for piece in self.llm.chat_stream(msgs, json_mode=False, tools=self._tools_schemas(), model=self.model_override, **self._llm_retry_kw()):
            kind = piece.get("kind", "text")
            if kind == "usage":
                yield {"type": "usage", "usage": piece.get("usage")}
                continue
            ttft = piece.get("ttft_ms")
            if kind == "reasoning":
                yield {"type": "reasoning-delta", "index": piece.get("block_index", 0), "text": piece.get("delta", ""), "ttft_ms": ttft}
            elif kind == "text":
                yield {"type": "text-delta", "index": piece.get("block_index", 0), "text": piece.get("delta", ""), "ttft_ms": ttft}
            elif kind == "tool-call":
                yield {"type": "tool-call-delta", "index": piece.get("block_index", 0),
                       "id": piece.get("call_id"), "name": piece.get("name"),
                       "argumentsDelta": piece.get("delta", ""), "ttft_ms": ttft}

    def turn(self, session_id: str, text: str, history: list[dict] | None = None,
             citation_pool: list | None = None, citation_idx: dict | None = None) -> Iterator[dict]:
        """turn/start → step* → turn/end(ReAct:思考→行动→观察→回答)。生成器:每写一条事件 yield 一条。"""
        _cpool: list = list(citation_pool) if citation_pool is not None else []   # 会话级 chunk 池(全局编号)
        _cidx: dict = dict(citation_idx) if citation_idx is not None else {}       # chunk_id -> 全局 idx
        _has_registry = citation_idx is not None
        t0 = time.time()
        yield self._emit("turn_start", {"turn": 1})
        yield self._emit("user_message", {"text": text, "client_time": None})

        _prompt_tokens = 0
        _completion_tokens = 0
        _ttft = None
        assistant_emitted = False
        aborted = False
        reason = "completed"
        n_steps = 0
        _step_t0: float | None = None   # 当前 step 起点,供 step_end 计算每步耗时
        max_steps = int(getattr(self.cfg, "max_steps_per_turn", 20))
        # 阶段 A:跨轮上下文。history = 此前轮次的 user/assistant(已剥旧 [idx]);当前 user 追加在后。
        # history 消息带 "seq"(事件序号)供压缩回指;构造模型消息前剥掉。
        _hist_seqs: list = [m.get("seq") for m in (history or [])]
        conversation: list[dict] = ([{"role": m.get("role"), "content": m.get("content", "")} for m in (history or [])]
                                    + [{"role": "user", "content": text}])
        # 阶段 B/B-1/C:窗口上限 + 压缩(先剪枝→重测→跳过摘要→保尾压头→摘要替换)。
        # 口径与 dsh 对齐:80% 触发算"全量"= system + tools + 对话。
        win = int(getattr(self.cfg, "context_window", 0) or 0)
        _ctx_compressed = False
        _sys_t = _tools_t = 0   # 供回合中 context-overflow 检查复用
        # 阶段 D:请求快照(供回放/重建"当时到底发了什么")。
        yield self._emit("request_header", {
            "reason": "turn", "model": self._effective_model,
            "system_len": len(self.system), "history_len": len(history or []), "window": win,
        })
        if win > 0:
            _sys_t = estimate_tokens(self.system)
            _tools_t = estimate_tokens(json.dumps(self._tools_schemas() or [], ensure_ascii=False))
            _thr = float(getattr(self.cfg, "compaction_threshold_ratio", 0.8) or 0.8)
            _retain = float(getattr(self.cfg, "compaction_retain_ratio", 0.16) or 0.16)
            _budget = max(0, int(win * _thr) - _sys_t - _tools_t)
            _before = len(conversation)
            _retain_budget = max(0, int(win * _retain))
            new_conv = None; _cinfo = None
            for _kind, _payload in self._compact_conversation(conversation, _hist_seqs, _budget, _retain_budget, win):
                if _kind == "event":
                    yield _payload
                else:
                    new_conv, _cinfo = _payload
            if _cinfo["triggered"]:
                _ctx_compressed = True
                conversation = new_conv
            else:
                # 压缩未触发/失败(如摘要生产失败)→ 回退朴素丢头,保证窗口不超。
                while len(conversation) > 1 and self._estimate_conversation(conversation) > _budget:
                    conversation.pop(0)
                _ctx_compressed = len(conversation) < _before
            _msg_t = self._estimate_conversation(conversation)   # 实际发送给模型的上下文(含工具/检索内容),与窗口无关
            yield self._emit("request_context", {
                "model": self._effective_model,
                "context_window": win,
                "system_tokens": _sys_t, "tools_tokens": _tools_t, "messages_tokens": _msg_t,
                "prompt_tokens": _sys_t + _tools_t + _msg_t,
                "completion_tokens": 0,
                "compression_triggered": _ctx_compressed,
            })
        _chunk_offset = 0             # 本 turn 已返回 chunk 总数 → 检索内容 [idx] 整轮全局编号(检索1 [1..k],检索2 [k+1..]),避免多轮检索引用错位
        references: list = []          # 本 turn 工具返回的原始引用(交给业务层呈现)
        references_map: dict = {}      # tool_call name -> 最新 reference(供 present 溯源)
        max_retrieve = int(getattr(self.cfg, "max_retrieve_per_turn", 5))
        max_history_search = int(getattr(self.cfg, "max_history_search_per_turn", 0) or 0)
        n_retrieve = 0
        n_history_search = 0

        try:
            while True:
                n_steps += 1
                if n_steps > max_steps:
                    blocks, cits = self.present_answer("已达回答步数上限,请补充资料后再试。", references)
                    conversation.append({"role": "assistant", "content": "已达回答步数上限,请补充资料后再试。"})
                    yield self._emit("assistant_message", {"blocks": blocks, "citations": cits})
                    assistant_emitted = True
                    break
                yield self._emit("step_start", {"turn": 1, "step": n_steps})
                _step_t0 = time.time()

                # 阶段C·回合中 pressure/context-overflow:检索/推理增长可能跨过 80% 或硬窗口 → 立即压缩头部。
                if win > 0:
                    _thr = float(getattr(self.cfg, "compaction_threshold_ratio", 0.8) or 0.8)
                    _retain = float(getattr(self.cfg, "compaction_retain_ratio", 0.16) or 0.16)
                    _total = _sys_t + _tools_t + self._estimate_conversation(conversation)
                    _limit = int(win * _thr)
                    if _total > _limit:
                        _reason = "context-overflow" if _total > win else "pressure"
                        _obudget = max(0, _limit - _sys_t - _tools_t)
                        _oconv = None; _oinfo = None
                        for _kind, _payload in self._compact_conversation(conversation, _hist_seqs, _obudget, max(0, int(win * _retain)), win, reason=_reason):
                            if _kind == "event":
                                yield _payload
                            else:
                                _oconv, _oinfo = _payload
                        if _oinfo["triggered"]:
                            conversation = _oconv
                            _ctx_compressed = True
                        else:
                            # 摘要失败 → 只丢"历史正文"头部,绝不拆工具对(不碰当前轮的 tool/assistant tool_calls)。
                            while len(conversation) > 1 and (_sys_t + _tools_t + self._estimate_conversation(conversation)) > _limit:
                                _head = conversation[0]
                                if _head.get("role") == "tool" or _head.get("tool_calls"):
                                    break
                                conversation.pop(0)
                            _ctx_compressed = True

                msgs = [{"role": "system", "content": self.system}] + conversation
                if n_retrieve >= max_retrieve:
                    msgs.append({"role": "system", "content": "检索次数已达上限,请立即基于已有资料输出最终回答,不要继续调用工具;资料不全请明确说明。"})
                assembler = BlockAssembler()

                for chunk in self._stream_blocks(msgs):
                    if chunk.get("type") == "usage":
                        u = chunk.get("usage") or {}
                        _prompt_tokens += int(u.get("prompt_tokens") or 0)
                        _completion_tokens += int(u.get("completion_tokens") or 0)
                        continue
                    if chunk.get("ttft_ms") is not None and _ttft is None:
                        _ttft = chunk.get("ttft_ms")   # 首 token 时延(照 loop.py 口径)
                    yield self._emit("assistant_chunk", {"kind": chunk["type"].replace("-delta", ""),
                                                         "delta": chunk.get("text") or chunk.get("argumentsDelta", ""),
                                                         "ttft_ms": chunk.get("ttft_ms")})
                    assembler.push(chunk)

                tool_calls = assembler.tool_calls()
                # D52 知识检索达上限:仅当本轮还调“知识检索类”工具才强制收尾;本会话历史检索(回忆)
                # 不触发也不被拦(它帮收尾,不增加知识检索收敛)。
                _has_kw_tool = any((tc.name or "search_knowledge") != "session_history_search" for tc in tool_calls)
                if tool_calls and _has_kw_tool and n_retrieve >= max_retrieve:
                    # LLM 无视上限仍想调工具 → 强制诚实结束(业务层兜底)
                    blocks, citations = (self.force_answer(references) if self.force_answer
                                         else self.present_answer("已检索多次,未能获得完整资料,请以原文为准。", references))
                    yield self._emit("assistant_message", {"blocks": blocks, "citations": citations})
                    assistant_emitted = True
                    yield self._emit("step_end", {"turn": 1, "step": n_steps, "elapsed_ms": int((time.time() - (_step_t0 or time.time())) * 1000)})
                    break
                if tool_calls:
                    if _has_kw_tool:
                        n_retrieve += 1      # 整轮知识检索收敛计数(保持原语义);历史检索不占
                    asst: dict = {"role": "assistant", "content": assembler.text_blocks().strip() or None}
                    asst["tool_calls"] = [{"id": tc.id or f"call_{i}", "type": "function",
                                           "function": {"name": tc.name or "search_knowledge", "arguments": tc.text or "{}"}}
                                          for i, tc in enumerate(tool_calls)]
                    if assembler.reasoning_text().strip():
                        asst["reasoning_content"] = assembler.reasoning_text()
                    conversation.append(asst)
                    for i, tc in enumerate(tool_calls):
                        name = tc.name or "search_knowledge"
                        args = parse_tool_arguments(tc.text)
                        yield self._emit("tool_call", {"tool": name, "args": args})
                        if name == "session_history_search":
                            # D52:本会话历史检索(回忆)——独立上限,不占知识检索收敛;会话 id 由系统注入,不来自模型
                            if max_history_search and n_history_search >= max_history_search:
                                content, reference, _tok, _terr = ("已达本会话历史检索上限,请基于现有资料回答。", None, False, "history_search_limit")
                            else:
                                content, reference, _tok, _terr = self._run_tool(name, args, start_idx=_chunk_offset, session_id=session_id)
                                n_history_search += 1
                        else:
                            # 阶段5:写工具审批门控(读工具放行;写工具需人工批准,可改参数/拒绝/挂起)
                            _gated = (self.tools.get(name) or {}).get("write") and self.approval is not None \
                                     and getattr(self.cfg, "write_tools_approval", "manual") != "auto"
                            if _gated:
                                _rid, _areq = self.approval.new_request(name, args, f"写入型工具 {name} 需人工审批")
                                yield self._emit("approval_request", _areq)
                                _ad = self.approval.wait(_rid)
                                if _ad and _ad.get("status") == "approve":
                                    args = _ad.get("edited_args") or args   # 用改后的参数执行
                                    content, reference, _tok, _terr = self._run_tool(name, args, start_idx=_chunk_offset, session_id=session_id)
                                else:
                                    content, reference, _tok, _terr = (f"写操作「{name}」未被批准({(_ad or {}).get('status', 'denied')}),未执行。", None, False, "approval_denied")
                            else:
                                content, reference, _tok, _terr = self._run_tool(name, args, start_idx=_chunk_offset, session_id=session_id)
                        if isinstance(reference, list):
                            _chunk_offset += len(reference)
                        # 跨轮引用:检索内容用"会话全局编号"重排(同一 chunk 各轮同 idx),供上下文回答复用 [idx]。
                        if _has_registry and isinstance(reference, list) and reference and isinstance(reference[0], dict):
                            for _c in reference:
                                _cid2 = _c.get("chunk_id")
                                if _cid2 is not None and _cid2 not in _cidx:
                                    _cpool.append(_c)
                                    _cidx[_cid2] = len(_cpool)
                            content = "\n\n".join(
                                f"[{_cidx[c['chunk_id']]}] ({c['chunk_id']}) {c['content']}" for c in reference)
                        # 阶段 B-1:工具结果落地截断(D12)。只截"喂给模型的 content";
                        # reference(原始 chunks)不动,完整进 retrieval 事件(引用/溯源不丢)。
                        pruned, truncated = content, False
                        if isinstance(content, str) and content:
                            _th = int(getattr(self.cfg, "max_tool_result_chars", 0) or 0)
                            if _th > 0:
                                _p = prune_tool_content(
                                    content, _th,
                                    int(getattr(self.cfg, "tool_result_head_chars", 0) or 0),
                                    int(getattr(self.cfg, "tool_result_tail_chars", 0) or 0))
                                if _p is not None:
                                    pruned, truncated = _p, True
                        yield self._emit("tool_result", {"tool": name, "ok": _tok,
                                                         "result_truncated": truncated,
                                                         "error": _terr})
                        conversation.append({"role": "tool", "tool_call_id": tc.id or f"call_{i}",
                                             "name": name, "content": pruned})
                        references.append(reference)
                        references_map.setdefault(name, reference)
                        # 工具返回"类 chunk 列表" → 以 retrieval 事件透出(前端溯源 sources 用);业务无关:非列表则不发
                        if isinstance(reference, list) and reference and isinstance(reference[0], dict):
                            yield self._emit("retrieval", {"query": str((args or {}).get("query") or json.dumps(args or {}, ensure_ascii=False)), "chunks": reference})
                    yield self._emit("step_end", {"turn": 1, "step": n_steps, "elapsed_ms": int((time.time() - (_step_t0 or time.time())) * 1000)})
                    continue

                answer_text = assembler.text_blocks().strip()
                if _has_registry:
                    # D43:本轮有检索 → 引用只准用本轮检出的块(按其全局 idx 解析),防止模型复用历史轮次索引导致 idx↔事实错配;
                    # 当轮无检索(上下文回答)→ 才允许跨轮复用全局编号(references 为空时).
                    _turn_used = set()
                    for _r in references:
                        if not isinstance(_r, list):
                            continue
                        for _c in _r:
                            if isinstance(_c, dict):
                                _ri = _cidx.get(_c.get("chunk_id"))
                                if _ri is not None:
                                    _turn_used.add(_ri)
                    if _turn_used:
                        _idx_map = {idx: cid for cid, idx in _cidx.items() if idx in _turn_used}
                    else:
                        _idx_map = {idx: cid for cid, idx in _cidx.items()}
                    blocks, citations = self.present_answer(answer_text or "（无回答）", references, idx_map=_idx_map)
                else:
                    blocks, citations = self.present_answer(answer_text or "（无回答）", references)
                conversation.append({"role": "assistant", "content": answer_text or "（无回答）"})
                yield self._emit("assistant_message", {"blocks": blocks or [{"t": "p", "text": answer_text}], "citations": citations or []})
                assistant_emitted = True
                yield self._emit("step_end", {"turn": 1, "step": n_steps, "elapsed_ms": int((time.time() - (_step_t0 or time.time())) * 1000)})
                break
        except GeneratorExit:
            aborted = True
            reason = "interrupted"
            raise
        except Exception as e:
            reason = "error"
            logger.exception("turn failed sid=%s err=%s", session_id, e, extra={"session_id": session_id, "trace_id": session_id})
        finally:
            if not assistant_emitted:
                blocks, cits = self.present_answer("回答生成失败/中断,请重试。", references)
                if not aborted:
                    yield self._emit("assistant_message", {"blocks": blocks, "citations": cits})
                    conversation.append({"role": "assistant", "content": "回答生成失败/中断,请重试。"})
                else:
                    self._emit("assistant_message", {"blocks": blocks, "citations": cits})
            _run_ms = int((time.time() - t0) * 1000)
            tps = (_completion_tokens / (_run_ms / 1000)) if (_completion_tokens > 0 and _run_ms > 0) else None
            # 阶段 B:回合结束时的上下文快照 —— 完整对话(历史 + 当前用户提问 + 助手回答),
            # 而非回合开始时仅含提问的瞬时值。前端"对话消息"据此展示,回答才会被计入上下文占用。
            # compression_triggered 沿用回合开头的裁剪结果(窗口裁剪只发生在回合开始)。
            rc_ev = None
            if win > 0:
                _sys_t2 = estimate_tokens(self.system)
                _tools_t2 = estimate_tokens(json.dumps(self._tools_schemas() or [], ensure_ascii=False))
                _msg_t2 = self._estimate_conversation(conversation)   # 实际发送上下文(含回答/工具),与窗口无关
                rc_ev = self._emit("request_context", {
                    "model": self._effective_model,
                    "context_window": win,
                    "system_tokens": _sys_t2, "tools_tokens": _tools_t2, "messages_tokens": _msg_t2,
                    "prompt_tokens": _sys_t2 + _tools_t2 + _msg_t2,
                    "completion_tokens": _completion_tokens,
                    "compression_triggered": _ctx_compressed,
                })
            use_ev = self._emit("usage", {"model": self._effective_model, "prompt_tokens": _prompt_tokens,
                                          "completion_tokens": _completion_tokens, "cost_estimate": None,
                                          "ttft_ms": _ttft, "run_ms": _run_ms, "tokens_per_second": tps})
            end_ev = self._emit("turn_end", {"turn": 1, "reason": reason, "elapsed_ms": _run_ms,
                                             "ttft_ms": _ttft, "tokens_per_second": tps})
            if not aborted:
                if rc_ev is not None:
                    yield rc_ev
                yield use_ev
                yield end_ev
            logger.info("turn end sid=%s steps=%d reason=%s", session_id, n_steps, reason, extra={"session_id": session_id, "trace_id": session_id})

    def _estimate_conversation(self, conversation: list[dict]) -> int:
        return sum(estimate_tokens(str(m.get("content") or "")) for m in conversation)

    def _compact_conversation(self, conversation: list[dict], hist_seqs: list,
                              budget: int, retain_budget: int, win: int, reason: str = "pressure"):
        """阶段 C 压缩(生成器)。先 yield ("event", compaction_start) 让前端显示"压缩中",
        再做阻塞的 LLM 摘要,再依次 yield compaction_summary/end;最后 yield ("result", (new_conversation, info))。
        调用方 for 迭代:kind=="event" 就 yield 事件,kind=="result" 就收 (conversation, info)。
        """
        info = {"triggered": False, "shadowed_seqs": [], "chars_saved": 0, "pruned": []}
        if self._estimate_conversation(conversation) <= budget:
            yield ("result", (conversation, info)); return
        est = lambda m: estimate_tokens(str(m.get("content") or ""))
        max_chars = int(getattr(self.cfg, "max_tool_result_chars", 0) or 0)
        head_c = int(getattr(self.cfg, "tool_result_head_chars", 0) or 0)
        tail_c = int(getattr(self.cfg, "tool_result_tail_chars", 0) or 0)
        pruned_list: list = []
        if max_chars > 0:
            conv2, pruned_list = prune_tool_messages(conversation, max_chars, head_c, tail_c)
            for p in pruned_list:
                _seq = hist_seqs[p["index"]] if p["index"] < len(hist_seqs) else None
                if isinstance(_seq, int):
                    yield ("event", self._emit("compaction_prune", {"seq": _seq,
                                                                  "shadowed_token_count": est({"content": ""}),
                                                                  "chars_removed": p["chars_removed"]}))
            if self._estimate_conversation(conv2) <= budget:
                info.update({"triggered": True, "pruned": pruned_list,
                             "chars_saved": sum(p["chars_removed"] for p in pruned_list)})
                yield ("result", (conv2, info)); return
            conversation = conv2
        k = select_keep_tail(conversation, retain_budget)
        if k <= 0:
            yield ("result", (conversation, info)); return
        head = conversation[:k]
        tail = conversation[k:]
        head_chars = sum(len(str(m.get("content") or "")) for m in head)
        shadowed_seqs = [s for s in (list(hist_seqs[:k]) if hist_seqs else []) if isinstance(s, int)]
        from_seq = min(shadowed_seqs) if shadowed_seqs else None
        to_seq = max(shadowed_seqs) if shadowed_seqs else None
        # 先发 compaction_start(前端显示"压缩中"),再做阻塞的摘要调用
        yield ("event", self._emit("compaction_start", {"from_seq": from_seq, "to_seq": to_seq,
                                                         "reason": reason, "turn": 1}))
        summary = None
        try:
            req = build_summary_request(self.system, head)
            summary = collect_summary(self.llm.chat_stream(req, json_mode=False, tools=None,
                                                           model=self.model_override, **self._llm_retry_kw()))
        except Exception as e:  # noqa: BLE001
            logger.warning("compaction summary failed sid turn, err=%s", e)
            summary = None
        max_tok = int(getattr(self.cfg, "compaction_max_tokens", 0) or 0)
        if summary:
            summary = truncate_summary(summary, max_tok)
        head_tokens = self._estimate_conversation(head)
        if not summary or estimate_tokens(summary) >= head_tokens:
            yield ("event", self._emit("compaction_end", {"reason": reason, "chars_saved": 0, "turn": 1}))
            yield ("result", (conversation, info)); return
        framed = frame_summary(summary)
        chars_saved = max(0, head_chars - len(summary))
        yield ("event", self._emit("compaction_summary", {"summary": summary, "shadowed_seqs": shadowed_seqs,
                                                          "shadowed_token_count": head_tokens}))
        yield ("event", self._emit("compaction_end", {"reason": reason, "chars_saved": chars_saved, "turn": 1}))
        new_conv = [{"role": "system", "content": framed}] + tail
        info.update({"triggered": True, "shadowed_seqs": shadowed_seqs, "chars_saved": chars_saved,
                     "pruned": pruned_list})
        yield ("result", (new_conv, info))

    def _run_tool(self, name: str, args: Any, start_idx: int = 0, session_id: str | None = None) -> tuple[str, Any, bool, str | None]:
        """按名字查表执行工具;返回 (喂给 LLM 的 content, reference, ok, error_code)。

        ok=False 表示工具未成功执行(未知/抛异常),error_code 区分 unknown_tool/tool_error/retrieval_unavailable。
        照 dsh:失败是"一等错误结果"(isError + error.code),喂给模型/用户的 content 为脱敏可读
        文案,完整异常只进日志/logging,不泄漏内部细节。start_idx=本 turn 已返回 chunk 数,用于 [idx] 整轮编号。
        """
        tool = self.tools.get(name)
        if not tool:
            return "（无此工具）", None, False, "unknown_tool"
        try:
            if _handler_accepts_session(tool["handler"]):
                raw = tool["handler"](args, start_idx, session_id=session_id)
            else:
                raw = tool["handler"](args, start_idx)
        except RetrievalUnavailable:
            # 检索基础设施(向量库)不可用 → 注入零检索结果,LLM 依 SYSTEM 诚实拒答(严禁杜撰)
            logger.error("检索服务不可用 tool=%s(重试后仍失败)→ 记 retrieval_unavailable", name)
            return ("【检索服务不可用】当前无法访问知识库数据。请如实告知用户:"
                    "抱歉,当前知识库数据暂不可用,我无法给出有数据支撑的回答,为避免不准确信息,请稍后重试或转人工坐席;"
                    "严禁编造任何条款内容、数字或责任范围。",
                    None, False, "retrieval_unavailable")
        except Exception as e:
            logger.exception("tool %s failed", name)
            return f"工具「{name}」调用失败,未取得结果,请基于已有资料回答。", None, False, "tool_error"
        if isinstance(raw, dict) and ("content" in raw or "reference" in raw):
            return str(raw.get("content") or ""), raw.get("reference"), True, None
        return str(raw), raw, True, None

