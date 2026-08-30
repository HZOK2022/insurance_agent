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


# ---- agent loop ----
class AgentLoop:
    """dsh agent-loop 核心:ReAct 循环,业务无关(工具表 + 回答呈现为注入)。"""

    def __init__(self, llm, system: str, tools: dict[str, dict],
                 present_answer: Callable[[str, list], tuple[list, list]], cfg,
                 emit: Callable[[str, dict], dict] | None = None,
                 force_answer: Callable[[list], tuple[list, list]] | None = None):
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

    def _tools_schemas(self) -> list[dict] | None:
        schemas = [t["schema"] for t in self.tools.values()]
        return schemas or None

    def _stream_blocks(self, msgs: list[dict]) -> Iterator[dict]:
        """流式取块:走 llm.chat_stream,产出标准化 chunk 供 BlockAssembler + 推送。"""
        for piece in self.llm.chat_stream(msgs, json_mode=False, tools=self._tools_schemas()):
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

    def turn(self, session_id: str, text: str) -> Iterator[dict]:
        """turn/start → step* → turn/end(ReAct:思考→行动→观察→回答)。生成器:每写一条事件 yield 一条。"""
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
        max_steps = int(getattr(self.cfg, "max_steps_per_turn", 20))
        conversation: list[dict] = [{"role": "user", "content": text}]
        references: list = []          # 本 turn 工具返回的原始引用(交给业务层呈现)
        references_map: dict = {}      # tool_call name -> 最新 reference(供 present 溯源)
        max_retrieve = int(getattr(self.cfg, "max_retrieve_per_turn", 5))
        n_retrieve = 0

        try:
            while True:
                n_steps += 1
                if n_steps > max_steps:
                    blocks, cits = self.present_answer("已达回答步数上限,请补充资料后再试。", references)
                    yield self._emit("assistant_message", {"blocks": blocks, "citations": cits})
                    assistant_emitted = True
                    break
                yield self._emit("step_start", {"turn": 1, "step": n_steps})

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
                if tool_calls and n_retrieve >= max_retrieve:
                    # LLM 无视上限仍想调工具 → 强制诚实结束(业务层兜底)
                    blocks, citations = (self.force_answer(references) if self.force_answer
                                         else self.present_answer("已检索多次,未能获得完整资料,请以原文为准。", references))
                    yield self._emit("assistant_message", {"blocks": blocks, "citations": citations})
                    assistant_emitted = True
                    yield self._emit("step_end", {"turn": 1, "step": n_steps})
                    break
                if tool_calls:
                    n_retrieve += 1
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
                        content, reference = self._run_tool(name, args)
                        yield self._emit("tool_result", {"tool": name, "ok": reference is not None or bool(content),
                                                         "result_truncated": False,
                                                         "error": None if (reference is not None or content) else "no_hits"})
                        conversation.append({"role": "tool", "tool_call_id": tc.id or f"call_{i}",
                                             "name": name, "content": content})
                        references.append(reference)
                        references_map.setdefault(name, reference)
                        # 工具返回"类 chunk 列表" → 以 retrieval 事件透出(前端溯源 sources 用);业务无关:非列表则不发
                        if isinstance(reference, list) and reference and isinstance(reference[0], dict):
                            yield self._emit("retrieval", {"query": (args or {}).get("query"), "chunks": reference})
                    yield self._emit("step_end", {"turn": 1, "step": n_steps})
                    continue

                answer_text = assembler.text_blocks().strip()
                blocks, citations = self.present_answer(answer_text or "（无回答）", references)
                yield self._emit("assistant_message", {"blocks": blocks or [{"t": "p", "text": answer_text}], "citations": citations or []})
                assistant_emitted = True
                yield self._emit("step_end", {"turn": 1, "step": n_steps})
                break
        except GeneratorExit:
            aborted = True
            reason = "interrupted"
            raise
        except Exception as e:
            reason = "error"
            logger.exception("turn failed sid=%s err=%s", session_id, e)
        finally:
            if not assistant_emitted:
                blocks, cits = self.present_answer("回答生成失败/中断,请重试。", references)
                if not aborted:
                    yield self._emit("assistant_message", {"blocks": blocks, "citations": cits})
                else:
                    self._emit("assistant_message", {"blocks": blocks, "citations": cits})
            _run_ms = int((time.time() - t0) * 1000)
            tps = (_completion_tokens / (_run_ms / 1000)) if (_completion_tokens > 0 and _run_ms > 0) else None
            use_ev = self._emit("usage", {"model": getattr(self.cfg, "deepseek_model", ""), "prompt_tokens": _prompt_tokens,
                                          "completion_tokens": _completion_tokens, "cost_estimate": None,
                                          "ttft_ms": _ttft, "run_ms": _run_ms, "tokens_per_second": tps})
            end_ev = self._emit("turn_end", {"turn": 1, "reason": reason, "elapsed_ms": _run_ms,
                                             "ttft_ms": _ttft, "tokens_per_second": tps})
            if not aborted:
                yield use_ev
                yield end_ev
            logger.info("turn end sid=%s steps=%d reason=%s", session_id, n_steps, reason)

    def _run_tool(self, name: str, args: Any) -> tuple[str, Any]:
        """按名字查表执行工具;返回 (喂给 LLM 的 content, 业务层用的 reference)。"""
        tool = self.tools.get(name)
        if not tool:
            return "（无此工具）", None
        try:
            raw = tool["handler"](args)
        except Exception as e:
            logger.exception("tool %s failed", name)
            return f"工具错误: {e}", None
        if isinstance(raw, dict) and ("content" in raw or "reference" in raw):
            return str(raw.get("content") or ""), raw.get("reference")
        return str(raw), raw

