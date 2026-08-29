"""parse LLM content -> {answer, citations:[{idx, chunk_id}]}"""
import json

def _normalize(obj):
    if not isinstance(obj, dict): return {"answer": str(obj), "citations": []}
    answer = obj.get("answer", "") or ""
    cites = obj.get("citations", []) or []
    norm=[]
    for c in cites:
        if isinstance(c, dict) and "chunk_id" in c:
            try: norm.append({"idx": int(c.get("idx",0)), "chunk_id": str(c["chunk_id"])})
            except Exception: pass
    return {"answer": str(answer), "citations": norm}

def _extract_json(content):
    i=content.find("{"); j=content.rfind("}")
    return content[i:j+1] if (i>=0 and j>i) else None

def parse_answer(content):
    content = (content or "").strip()
    if not content: return {"answer":"","citations":[]}
    try: return _normalize(json.loads(content))
    except Exception: pass
    obj=_extract_json(content)
    if obj:
        try: return _normalize(json.loads(obj))
        except Exception: pass
    return _normalize({"answer": content, "citations": []})