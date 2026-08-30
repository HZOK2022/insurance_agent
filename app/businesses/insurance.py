"""保险业务层:挂在 agent-loop 核心上的"一个业务"。

业务层 = system + 工具表 + 回答呈现(present_answer)。
- 工具: search_knowledge(检索条款)。handler 返回 {"content": 喂给LLM的文本, "reference": 原始chunks}(reference 供溯源)。
- 呈现: present_answer 把回答里的 [idx] 映射回条款原文(溯源),生成结构化 blocks + citations。
换个业务(其实现在显示形式不同),只需新写一个 bundle——核心不动。
"""
from __future__ import annotations

import re
from typing import Any

from app.retrieval.search_tool import search_knowledge

SEARCH_TOOL = {"type": "function", "function": {
    "name": "search_knowledge",
    "description": "检索保险知识库(产品条款/重大疾病病种/责任免除/免赔额/理赔等),返回相关条款片段。",
    "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "检索关键词或问题"}},
                   "required": ["query"]}}}

SYSTEM = (
    "你是保险销售知识助手。可调用 search_knowledge 工具检索知识库回答问题。\n"
    "规则:\n"
    "- 需要知识库资料时,调用 search_knowledge,并**先写一句叙述**(查到了什么、还缺什么、下一步要查什么),再调用工具。\n"
    "- 调用后看到检索结果;资料不足可再查,但别用几乎相同的词反复查,连续检索无新增就停止。\n"
    "- 资料足够或这是寒暄/常识时,不要再调工具,**直接输出最终回答**。\n"
    "- 最终回答:写成要回复客户的**可读文本**(可分段;要点行用'- '开头;关键结论用**加粗**)。在引用处标 [idx](对应你检索结果里的片段编号,如 [1])。不要输出 JSON/代码块。\n"
    "- 诚实优先:只写实际检索到的。未获得完整清单必须写明'以下为检索到的部分病种,完整清单以保险条款原文为准',严禁声称'共N种/完整列表'除非确实列全;查不到就说不知道,不要编造。"
)


def _format_chunks(chunks: list[dict]) -> str:
    if not chunks:
        return "（无检索资料）"
    return "\n\n".join(f"[{i}] ({c['chunk_id']}) {c['content']}" for i, c in enumerate(chunks, 1))


def build_tools(embedder, qstore, cfg) -> dict[str, dict]:
    def handler(args: Any) -> dict:
        query = (args or {}).get("query") or ""
        chunks = search_knowledge(embedder, qstore, query, top_k=cfg.top_k, top_rerank=cfg.top_k_reranker)
        # 喂给 LLM 的 content 用格式化文本;reference 保留原始 chunks 供溯源
        return {"content": _format_chunks(chunks), "reference": chunks}
    return {"search_knowledge": {"schema": SEARCH_TOOL, "handler": handler}}


def present_answer(answer_text: str, chunks_list: list) -> tuple[list, list]:
    """业务层的"展现形式":把 [idx] 映射回条款原文(溯源),生成 blocks + citations。"""
    all_chunks = [c for c in chunks_list if isinstance(c, list)]
    flat = [c for cs in all_chunks for c in cs]
    by_idx = {i + 1: c["chunk_id"] for i, c in enumerate(flat)}
    idxs = sorted({int(m) for m in re.findall(r"\[(\d+)\]", answer_text)})
    cites, seen = [], set()
    for idx in idxs:
        cid = by_idx.get(idx)
        if cid and cid not in seen:
            cites.append({"idx": idx, "chunk_id": cid})
            seen.add(cid)
    blocks = [{"t": "p", "text": answer_text or "（无回答）"}]
    return blocks, cites


def force_answer(chunks_list: list) -> tuple[list, list]:
    """检索达上限强制结束时的业务兜底(诚实说明,不编造)。"""
    return [{"t": "p", "text": "已检索多次,未能获得完整清单。为避免编造,完整清单请以保险条款原文为准;确切的病种名称与定义以条款原文为准。"}], []


def bundle(embedder, qstore, cfg) -> dict:
    return {"system": SYSTEM, "tools": build_tools(embedder, qstore, cfg),
            "present_answer": present_answer, "force_answer": force_answer, "cfg": cfg}
