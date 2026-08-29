"""AgentLoop: question -> retrieve -> assemble -> LLM answer -> log."""
from app.llm.structured import parse_answer
from app.retrieval.search_tool import search_knowledge

SYSTEM = ("你是保险销售知识助手。基于【检索资料】用 Markdown 回答客服问题。"
          "回答必须引用资料:正文中用 [idx] 标注,并在 citations 里给出对应 chunk_id。"
          '只输出 JSON:{"answer":"...","citations":[{"idx":1,"chunk_id":"..."}]}。'
          "资料不足就明确说不知道,不要编造。")

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
        citations = self._resolve(parsed, chunks)
        self.store.append(session_id, "assistant_message", {"text": parsed["answer"], "citations": citations})
        self.store.append(session_id, "usage", {"model": self.cfg.deepseek_model,
            "prompt_tokens": int(usage.get("prompt_tokens",0)), "completion_tokens": int(usage.get("completion_tokens",0)), "cost_estimate": None})
        return {"answer": parsed["answer"], "citations": citations, "chunks": chunks}