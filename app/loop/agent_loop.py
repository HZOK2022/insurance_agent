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
import re
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


def _summarize_args(args: Any, cap: int = 200) -> str:
    """工具参数摘要:转成短 JSON,超长截断(日志可读,不落全文)。"""
    try:
        s = json.dumps(args or {}, ensure_ascii=False)
    except Exception:
        s = str(args or {})
    return s if len(s) <= cap else s[:cap] + f"…(+{len(s) - cap}字)"


def _summarize_chunks(chunks: Any, limit: int = 5, snippet: int = 80) -> str:
    """检索召回摘要:每个命中 chunk 的 score/文档/章节/前 snippet 字(日志用,不落全文)。
    RAG 问题多出在检索——让日志一眼看清"召回了什么、分数多高、来自哪"。"""
    if not chunks or not isinstance(chunks, list):
        return ""
    parts: list[str] = []
    for c in chunks[:limit]:
        if not isinstance(c, dict):
            continue
        score = c.get("score")
        doc = c.get("doc_id") or c.get("product_name") or ""
        sec = (c.get("section") or "").strip()
        body = (c.get("content") or "").strip().replace("\n", " ")
        body = body[:snippet]
        sc = f"{float(score):.2f}" if isinstance(score, (int, float)) else "?"
        tag = doc + (f" §{sec}" if sec else "") + (f" {body}" if body else "")
        parts.append(f"[{sc} {tag}]")
    return " ".join(parts)


# ---- 工具参数校验(契约加固)----
# 为什么需要:handler 里普遍是 `(args or {}).get("query") or ""` 这类兜底取值,模型把参数名
# 写错(如 q/keyword)不会报错,而是带着空值**真的去执行** —— 检索空串照样 embedding、照样
# 返回 top_k 最近邻,模型拿到格式完美但语义无关的结果,于是编出带 [idx] 引用的错误答案。
# 这种"假成功"比崩溃危险得多(崩溃至少有 tool_error)。校验把它在进 handler 之前拦下来,
# 并给出模型能照着改的诊断(错在哪个字段、期望什么),而不是一句"调用失败"让模型瞎猜。
try:
    import jsonschema as _jsonschema
    from jsonschema import ValidationError as _JsonSchemaError
except ImportError:      # 校验是加固不是门槛:缺依赖就跳过,绝不让工具集体失效
    _jsonschema = None
    _JsonSchemaError = None


def _short(v: Any, n: int = 80) -> str:
    """截断超长值,避免诊断文案把上下文撑爆。"""
    s = str(v)
    return s if len(s) <= n else s[:n] + "…"


def tool_parameters_schema(tool: dict) -> dict:
    """从工具的 OpenAI function schema 里取出 parameters(JSON Schema)。

    兼容两种形态:{"function": {"parameters": ...}}(OpenAI 原生)与裸 {"parameters": ...}。
    没有声明 parameters 则返回空 dict(调用方据此放行)。
    """
    fn = (tool or {}).get("schema") or {}
    if isinstance(fn, dict) and isinstance(fn.get("function"), dict):
        fn = fn["function"]
    params = (fn.get("parameters") if isinstance(fn, dict) else None)
    return params if isinstance(params, dict) else {}


def _describe_schema_error(e) -> str:
    """把 jsonschema 的英文 ValidationError 翻成模型能照着改的中文诊断。

    只翻译最高频的三种(required/type/enum),其余原样透出 message。
    """
    path = ".".join(str(p) for p in (e.absolute_path or [])) or "(参数根)"
    if e.validator == "required":
        m = re.search(r"'(.+?)' is a required property", e.message or "")
        missing = m.group(1) if m else "(未知)"
        return (f"参数不合契约:缺少必填字段 {missing}。"
                f"请补齐后重新调用,不要用相近的字段名代替。")
    if e.validator == "type":
        got = type(e.instance).__name__
        return (f"参数不合契约:字段 {path} 类型不对,期望 {e.validator_value},"
                f"实际收到 {got}({_short(e.instance)!r})。请改用正确类型重新调用。")
    if e.validator == "enum":
        return (f"参数不合契约:字段 {path} 取值不在允许范围内;"
                f"允许的值为 {_short(e.validator_value, 120)}。请从中选一个重新调用。")
    return f"参数不合契约:字段 {path} {e.message}。请按工具说明修正后重新调用。"


def validate_tool_arguments(tool: dict, args: Any) -> str | None:
    """按工具的 JSON Schema 校验模型给出的参数。

    返回 None = 通过;否则返回**喂给模型**的诊断文案(不抛异常、不泄漏内部细节)。
    放行条件(校验是加固,不是门槛):缺 jsonschema 依赖 / 工具没声明 parameters /
    校验过程自身出错 —— 一律放行,退化为改动前的行为。
    """
    if _jsonschema is None:
        return None
    params = tool_parameters_schema(tool)
    if not params:
        return None
    if not isinstance(args, dict):
        return (f"参数不合契约:参数必须是 JSON 对象,实际收到 {type(args).__name__};"
                f"请按工具说明重新调用,不要省略参数名。")
    try:
        _jsonschema.validate(instance=args, schema=params)
    except _JsonSchemaError as e:      # type: ignore[misc]
        return _describe_schema_error(e)
    except Exception:
        logger.exception("工具参数校验自身异常,放行")
        return None
    return None


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
                 model: str | None = None, approval=None,
                 should_abort: Callable[[], bool] | None = None):
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
        self.should_abort = should_abort   # 显式"停止"通道(见 app/loop/abort.py):每 step 边界 + 每 chunk 检查

    @property
    def _effective_model(self) -> str:
        return self.model_override or getattr(self.cfg, "deepseek_model", "")

    def _abort_requested(self) -> bool:
        """显式"停止"是否被置位(回调来自业务层;回调自身异常一律当作"没停",不能让停止通道搞崩回合)。"""
        if not self.should_abort:
            return False
        try:
            return bool(self.should_abort())
        except Exception:  # noqa: BLE001
            return False

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

    def turn(self, session_id: str, text: str, history: list[dict] | None = None) -> Iterator[dict]:
        """turn/start → step* → turn/end(ReAct:思考→行动→观察→回答)。生成器:每写一条事件 yield 一条。

        引用编号 = **每轮 turn-local、从 1 连续**(D55):feed 给模型的检索片段用当轮编号
        (检索1 [1..k],检索2 [k+1..],由 handler 的 start_idx 保证),回答 citations 也按当轮
        references 顺序解析 —— 不再做跨轮全局编号重排(D35 取消),上下文回答(当轮无检索)
        不产生引用角标,需要回指早前内容时模型走 session_history_search 回源。
        """
        t0 = time.time()
        yield self._emit("turn_start", {"turn": 1})
        yield self._emit("user_message", {"text": text, "client_time": None})

        _prompt_tokens = 0
        _completion_tokens = 0
        _ttft = None
        _snap_bad = False          # 坏例快照标记:工具失败/检索弱 → 轮末落完整 prompt/completion
        _last_completion = ""      # 最近一次最终回答文本(供坏例快照)
        assistant_emitted = False
        aborted = False
        reason = "completed"
        n_steps = 0
        _step_t0: float | None = None   # 当前 step 起点,供 step_end 计算每步耗时
        _stopped = False                # 显式"停止"命中(非异常):仍要把终结事件推给客户端(客户端没断流)
        _partial_text: list = []        # 当前 step 已流出的正文(中断时保留已生成的部分回答,不丢)
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
        _chunk_offset = 0             # 本 turn 已返回 chunk 总数 → 检索片段当轮编号(检索1 [1..k],检索2 [k+1..]),每轮从 1 起,避免多轮检索引用错位
        references: list = []          # 本 turn 工具返回的原始引用(交给业务层呈现)
        references_map: dict = {}      # tool_call name -> 最新 reference(供 present 溯源)
        max_retrieve = int(getattr(self.cfg, "max_retrieve_per_turn", 5))      # 只约束"知识检索步数"(见下方 n_retrieve 计数)
        max_history_search = int(getattr(self.cfg, "max_history_search_per_turn", 0) or 0)  # 会话内回源检索独立上限
        n_retrieve = 0
        n_history_search = 0

        try:
            while True:
                # 停止点①:step 边界。命中就地收尾(保留部分回答),照常写 turn_end。
                if self._abort_requested():
                    _stopped = True
                    reason = "interrupted"
                    break
                n_steps += 1
                if n_steps > max_steps:
                    blocks, cits = self.present_answer("已达回答步数上限,请补充资料后再试。", references)
                    conversation.append({"role": "assistant", "content": "已达回答步数上限,请补充资料后再试。"})
                    yield self._emit("assistant_message", {"blocks": blocks, "citations": cits})
                    assistant_emitted = True
                    break
                yield self._emit("step_start", {"turn": 1, "step": n_steps})
                _step_t0 = time.time()
                _partial_text.clear()      # 每 step 重新累计,与前端"当前流式行"对齐(叙述/回答不混)

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
                _pt_before = _prompt_tokens
                _ct_before = _completion_tokens
                _step_ttft: float | None = None

                for chunk in self._stream_blocks(msgs):
                    if chunk.get("type") == "usage":
                        u = chunk.get("usage") or {}
                        _prompt_tokens += int(u.get("prompt_tokens") or 0)
                        _completion_tokens += int(u.get("completion_tokens") or 0)
                        continue
                    if chunk.get("ttft_ms") is not None:
                        if _ttft is None:
                            _ttft = chunk.get("ttft_ms")   # 首 token 时延(照 loop.py 口径)
                        if _step_ttft is None:
                            _step_ttft = chunk.get("ttft_ms")
                    yield self._emit("assistant_chunk", {"kind": chunk["type"].replace("-delta", ""),
                                                         "delta": chunk.get("text") or chunk.get("argumentsDelta", ""),
                                                         "ttft_ms": chunk.get("ttft_ms")})
                    assembler.push(chunk)
                    if chunk["type"] == "text-delta":
                        _partial_text.append(chunk.get("text") or "")
                    # 停止点②:每个 chunk 之后 —— 长回答能"立刻停",不必等本步跑完再检查。
                    if self._abort_requested():
                        _stopped = True
                        reason = "interrupted"
                        break
                if _stopped:
                    break

                # 每次 LLM 调用落一行日志(本步 token 增量/首 token 时延/步耗时/吞吐),便于定位"哪步贵/哪步慢"
                _step_ms = int((time.time() - (_step_t0 or time.time())) * 1000)
                _llm_pt = _prompt_tokens - _pt_before
                _llm_ct = _completion_tokens - _ct_before
                _llm_tps = (_llm_ct / (_step_ms / 1000)) if (_llm_ct > 0 and _step_ms > 0) else None
                logger.info("llm step=%s model=%s ptokens=%s ctokens=%s ttft=%s run_ms=%s tps=%s",
                            n_steps, self._effective_model, _llm_pt, _llm_ct,
                            _step_ttft, _step_ms, _llm_tps,
                            extra={"session_id": session_id, "trace_id": session_id,
                                   "model": self._effective_model})

                tool_calls = assembler.tool_calls()
                # D52 知识检索达上限:仅当本轮还调“知识检索类”工具才强制收尾;本会话历史检索(回忆)
                # 不触发也不被拦(它帮收尾,不增加知识检索收敛)。
                # 注意:这里是按"步"计数(一个 LLM 回合调了检索类工具就 +1),不是按"调用次数"。
                # 同一步内多个 search_knowledge 只算 1;其它工具(算保费/记忆等)不进入 n_retrieve。
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
                        # 停止点③:每个工具执行前 —— 一次 tool-call 段里有多个调用时也能及时停。
                        if self._abort_requested():
                            _stopped = True
                            reason = "interrupted"
                            break
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
                        # 排查摘要:每个工具调用落一行日志(参数/命中块数/ok/错误码/结果头),全文进 events
                        _res_head = (content or "").strip().replace("\n", " ") if isinstance(content, str) else ""
                        _res_log = _res_head[:120] + (f"…(+{len(_res_head) - 120}字)" if len(_res_head) > 120 else "")
                        logger.info(
                            "step tool sid=%s tool=%s ok=%s code=%s args=%s out=%d块 res=%s",
                            session_id, name, _tok, _terr, _summarize_args(args),
                            len(reference) if isinstance(reference, list) else 0,
                            _res_log,
                            extra={"session_id": session_id, "trace_id": session_id, "tool": name})
                        # D55:引用编号每轮 turn-local —— 检索内容保持 handler 的当轮编号
                        # (search_knowledge 用 start_idx 从 1 连续编号),不再用会话全局编号重排(D35 取消)。
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
                        if not _tok:
                            _snap_bad = True   # 工具失败/未批准 → 坏例快照
                        yield self._emit("tool_result", {"tool": name, "ok": _tok,
                                                         "result_truncated": truncated,
                                                         "error": _terr})
                        # ok / error_code 只进 tool_result 事件(UI 与审计可见),模型的 tool 消息
                        # 里只有 content —— 不加标记的话它只能从文案字面猜"这算不算失败"。
                        # 故失败时补一个结构化前缀,让模型明确知道:这是失败,不是一条普通结果。
                        _feed = pruned if _tok else f"【工具调用失败】{pruned}"
                        conversation.append({"role": "tool", "tool_call_id": tc.id or f"call_{i}",
                                             "name": name, "content": _feed})
                        references.append(reference)
                        references_map.setdefault(name, reference)
                        # 工具返回"类 chunk 列表" → 以 retrieval 事件透出(前端溯源 sources 用);业务无关:非列表则不发
                        if isinstance(reference, list):
                            # 检索弱(空/最高分<0.3)→ 坏例快照(RAG 问题 80% 出在检索;空结果尤其要记)
                            _rs = [c.get("score") for c in reference if isinstance(c, dict) and isinstance(c.get("score"), (int, float))]
                            if not reference or not _rs or max(_rs) < 0.3:
                                _snap_bad = True
                            if reference and isinstance(reference[0], dict):
                                logger.info("检索 sid=%s query=%s hits=%d %s", session_id,
                                            str((args or {}).get("query") or json.dumps(args or {}, ensure_ascii=False)),
                                            len(reference),
                                            _summarize_chunks(reference),
                                            extra={"session_id": session_id, "trace_id": session_id})
                                yield self._emit("retrieval", {"query": str((args or {}).get("query") or json.dumps(args or {}, ensure_ascii=False)), "chunks": reference})
                    if _stopped:
                        break
                    yield self._emit("step_end", {"turn": 1, "step": n_steps, "elapsed_ms": int((time.time() - (_step_t0 or time.time())) * 1000)})
                    continue

                answer_text = assembler.text_blocks().strip()
                # D55:回答引用按当轮 references 顺序解析(present_answer 平铺编号 1..N)——
                # 模型只见过当轮编号(检索内容当轮从 1 起),上下文回答(无检索)无块可解析 → 无角标。
                blocks, citations = self.present_answer(answer_text or "（无回答）", references)
                _cids = [c.get("idx") for c in (citations or []) if isinstance(c, dict) and c.get("idx") is not None]
                logger.info("回答 sid=%s chars=%d cites=%s head=%s", session_id,
                            len(answer_text or ""), _cids or len(citations or []),
                            (answer_text or "").strip().replace("\n", " ")[:200],
                            extra={"session_id": session_id, "trace_id": session_id})
                conversation.append({"role": "assistant", "content": answer_text or "（无回答）"})
                _last_completion = answer_text or ""
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
                # 显式"停止":保留本 step 已流出的正文(用户屏幕上已看到的部分,不能丢);
                # 一个字都没流出才用兜底话术。异常/断流路径维持原话术不变。
                _partial = "".join(_partial_text).strip()
                _stopped_now = bool(_stopped)
                _fallback = (_partial or "已手动停止本轮生成。") if _stopped_now else "回答生成失败/中断,请重试。"
                blocks, cits = self.present_answer(_fallback, references)
                _msg = {"blocks": blocks, "citations": cits}
                if _stopped_now:
                    _msg["interrupted"] = True    # 半截(手动停止)标记,供前端"已中断"展示 + build_history 续写识别
                if not aborted:
                    yield self._emit("assistant_message", _msg)
                    conversation.append({"role": "assistant", "content": _fallback})
                else:
                    self._emit("assistant_message", _msg)
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
                # 坏例快照:错误/中断/工具失败/检索弱轮才落完整 prompt/completion(好轮不存,省费用/PII)。
                # 放在 turn_end 之前,保持 turn_end 是本轮终结事件(测试/回放据此判定轮末)。
                _snap_enabled = bool(getattr(self.cfg, "badcase_snapshot_enabled", True))
                if _snap_enabled and (_snap_bad or reason in ("error", "interrupted")):
                    yield self._emit("badcase_snapshot", {
                        "reason": reason, "model": self._effective_model,
                        "system": self.system, "conversation": conversation,
                        "completion": _last_completion,
                        "prompt_tokens": _prompt_tokens, "completion_tokens": _completion_tokens,
                        "run_ms": _run_ms})
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

        ok=False 表示工具未成功执行(未知/参数不合契约/抛异常),
        error_code 区分 unknown_tool/invalid_arguments/tool_error/retrieval_unavailable。
        照 dsh:失败是"一等错误结果"(isError + error.code),喂给模型/用户的 content 为脱敏可读
        文案,完整异常只进日志/logging,不泄漏内部细节。start_idx=本 turn 已返回 chunk 数,用于 [idx] 整轮编号。

        执行前先按 schema 校验参数:不通过则不进 handler(避免"参数写错→空值→假成功")。
        """
        tool = self.tools.get(name)
        if not tool:
            return "（无此工具）", None, False, "unknown_tool"
        _bad_args = validate_tool_arguments(tool, args)
        if _bad_args:
            # 参数不合契约:不执行,把可操作的诊断回给模型(它才知道该补哪个字段)。
            logger.warning("工具参数不合契约 tool=%s args=%s → invalid_arguments", name, _short(args))
            return _bad_args, None, False, "invalid_arguments"
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

