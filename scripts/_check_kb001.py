# -*- coding: utf-8 -*-
"""临时(用完即删):查尊享e生2025 保险责任(第六条)各块,定位“必选计划保险责任”真正的承载块。"""
import sys
sys.path.insert(0, r"d:\LLM\insurance-agent")
from app.retrieval.knowledge_store import KnowledgeStore
from app.api.services import container
k = KnowledgeStore(cfg=container.get_cfg())

rows = [(c["chunk_id"], c.get("meta", {}).get("section", "")) for c in k.all_chunks()
        if c.get("meta", {}).get("doc_id") == "尊享e生2025"]
rows.sort(key=lambda r: int(r[0].split(":")[1]))
print("== 含'保险责任'或'必选'的块 ==")
for cid, sec in rows:
    if ("保险责任" in sec and "第六" in sec) or ("必选" in sec):
        print(f"{cid:16s} {sec[:70]}")
print()
print("== 关键字命中 content 含'必选'的块 ==")
for cid, sec in rows:
    ch = k.get_chunk(cid) or {}
    if "必选" in (ch.get("content") or ""):
        print(f"{cid:16s} {sec[:60]}")