"""parse LLM content -> {blocks:[...], citations:[...]} (structured answer blocks)."""
import json

def _norm_block(b):
    if not isinstance(b, dict): return {"t":"p","text":str(b)}
    t=b.get("t","p")
    if t in ("ul","ol"):
        return {"t":t,"items":[str(x) for x in (b.get("items") or []) if x]}
    return {"t":"p","text":str(b.get("text",""))}

def _normalize(obj):
    if not isinstance(obj, dict): return {"blocks":[{"t":"p","text":str(obj)}],"citations":[]}
    ans=obj.get("answer")
    if isinstance(ans, list): blocks=[_norm_block(b) for b in ans]
    elif ans: blocks=[{"t":"p","text":str(ans)}]
    else: blocks=[]
    cites=[]
    for c in (obj.get("citations") or []):
        if isinstance(c, dict) and "chunk_id" in c:
            try: cites.append({"idx":int(c.get("idx",0)),"chunk_id":str(c["chunk_id"])})
            except Exception: pass
    return {"blocks":blocks,"citations":cites}

def _extract_json(content):
    i=content.find("{"); j=content.rfind("}")
    return content[i:j+1] if (i>=0 and j>i) else None

def parse_answer(content):
    content=(content or "").strip()
    if not content: return {"blocks":[],"citations":[]}
    try: return _normalize(json.loads(content))
    except Exception: pass
    obj=_extract_json(content)
    if obj:
        try: return _normalize(json.loads(obj))
        except Exception: pass
    return _normalize({"answer":content,"citations":[]})