# -*- coding: utf-8 -*-
"""启动依赖体检:启动时对"便宜依赖"(Qdrant / 本地 SQLite)逐项探测并打一行状态日志。

设计:
- **绝不阻断启动**:任一依赖异常,服务照常起(运行期靠降级兜底),只输出一行 WARN 让人启动一眼看到问题;
- **不碰 embedder**(加载模型 ~10s,违背 D23 启动提速)与 **LLM**(费钱),它们保持 lazy;
- 结构化返回 [{name, ok, detail}],便于单测断言(不靠抓日志)。

调用:create_app 在 seed_admin/reconcile 之后调用;qstore 由调用方先构造一次传入(启动即构造,
首次提问不再为连不上等重试)。
"""
from __future__ import annotations

import logging

logger = logging.getLogger("startup.deps")


def _add(results: list[dict], name: str, ok: bool, detail: str) -> None:
    results.append({"name": name, "ok": bool(ok), "detail": str(detail)})
    (logger.info if ok else logger.warning)("startup deps: %s %s", name, detail)


def report_startup_dependencies(cfg, qstore=None) -> list[dict]:
    """体检并打日志。cfg 需含 sqlite_path / knowledge_db_path / premium_db_path 等;
    qstore=已构造的 QdrantStore(为 None 则记为"未检查")。本函数绝不抛异常。"""
    out: list[dict] = []

    # 1) agent.db(会话事实源):能被调到这里说明 SessionStore 已打开且 schema fail-closed 通过
    _add(out, "sqlite:agent.db", True, "ok(SessionStore 已打开, schema fail-closed 通过)")

    # 2) qdrant(派生向量索引):构造是否成功由调用方先行验证;这里据 is_down 出状态行
    if qstore is None:
        _add(out, "qdrant", False, "未检查(调用方未构造)—— 首次提问将再尝试")
    else:
        coll = getattr(qstore, "collection", "?")
        if qstore.is_down():
            _add(out, "qdrant", False,
                 f"不可用(collection={coll}) → 知识库问答将诚实拒答,直至 Qdrant 恢复(保费计算等本地功能不受影响)")
        else:
            _add(out, "qdrant", True, f"ok(collection={coll})")

    # 3) knowledge.db(chunks 事实源,BM25/重建来源)
    try:
        from app.retrieval.knowledge_store import KnowledgeStore
        k = KnowledgeStore(getattr(cfg, "knowledge_db_path", "data/knowledge.db"))
        try:
            n = k.count()
        finally:
            k.close()
        _add(out, "sqlite:knowledge.db", True, f"ok(chunks={n})")
    except Exception as e:
        _add(out, "sqlite:knowledge.db", False, f"不可用: {type(e).__name__}: {e}")

    # 4) premium.db(费率事实源):轻探 sqlite 可打开即可(不 import 业务模块,避免重副作用)
    try:
        import sqlite3
        conn = sqlite3.connect(getattr(cfg, "premium_db_path", "data/premium.db"))
        try:
            conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
        finally:
            conn.close()
        _add(out, "sqlite:premium.db", True, "ok")
    except Exception as e:
        _add(out, "sqlite:premium.db", False, f"不可用: {type(e).__name__}: {e}")

    return out
