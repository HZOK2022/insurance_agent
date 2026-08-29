"""AgentLoop: question -> retrieve -> assemble -> LLM structured answer -> log.

铁律:模型可见 ⟺ 已记录(检索片段/回答/引用都写会话日志)。
"""
from __future__ import annotations

from app.llm.structured import parse_answer
from app.retrieval.search_tool import search_knowledge

SYSTEM = ("你是保险销售知识助手。基于【检索资料】回答客服问题。"
          "answer 必须是数组,每个元素是块:{t:'p'|'h'|'ul', text 或 items}。"
          "要点用 t:'ul',items 为字符串数组每项一条;小标题用 t:'h';普通段落用 t:'p'。"
          "引用要克制:只在支撑具体结论/数字/前提的要点处标 [idx];同一资料来源的多条要点只标一次;不要每句/每项都标,并在 citations 给出对应 chunk_id。"
          '只输出 JSON:{"answer":[{"t":"p","text":"..."},{"t":"h","text":"..."},{"t":"ul","items":["...","..."]}],"citations":[{"idx":1,"chunk_id":"..."}]}。'
          "资料不足就说不知道,不要编造。")

class AgentLoop:
    def __init__(self, store, embedder, qstore, llm, cfg):
        self.store=store; self.embedder=embedder; self.qstore=qstore; self.llm=llm; self.cfg=cfg
    def _retrieve(self, query):
        return search_knowledge(self.embedder, self.qstore, query, top_k=self.cfg.top_k, top_rerank=self.cfg.top_k_reranker)
    def _format(self, chunks):
        if not chunks: return "（无检索资料）"
        parts=[]
        for i,c in enumerate(chunks,1):
            parts.append(f"[{i}] (chunk_id: {c['chunk_id']} | {c['doc_id']} {c.get('version','')}) {c['content']}")
        return "\n\n".join(parts)
    def _resolve(self, parsed, chunks):
        by_idx = {i+1: c["chunk_id"] for i,c in enumerate(chunks)}
        valid = set(by_idx.values()); out=[]; seen=set()
        for c in parsed["citations"]:
            cid = c["chunk_id"]
            if cid not in valid and c["idx"] in by_idx:
                cid = by_idx[c["idx"]]
            if cid in valid and cid not in seen:
                out.append({"idx": c["idx"], "chunk_id": cid}); seen.add(cid)
        return out
    def run(self, session_id, text):
        self.store.append(session_id, "user_message", {"text": text, "client_time": None})
        chunks = self._retrieve(text)
        self.store.append(session_id, "retrieval", {"query": text, "chunks": chunks})
        messages=[{"role":"system","content":SYSTEM},
                  {"role":"user","content":"【检索资料】\n"+self._format(chunks)+"\n\n【问题】"+text}]
        content, usage = self.llm.chat(messages)
        parsed = parse_answer(content)
        blocks = parsed["blocks"]
        citations = self._resolve(parsed, chunks)
        self.store.append(session_id, "assistant_message", {"blocks": blocks, "citations": citations})
        self.store.append(session_id, "usage", {"model": self.cfg.deepseek_model,
            "prompt_tokens": int(usage.get("prompt_tokens",0)), "completion_tokens": int(usage.get("completion_tokens",0)), "cost_estimate": None})
        return {"answer": "\n".join(b.get("text","") if b.get("t")!="ul" else "\n".join(b.get("items",[])) for b in blocks),
                "citations": citations, "chunks": chunks}