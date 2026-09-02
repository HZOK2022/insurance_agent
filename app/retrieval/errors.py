# -*- coding: utf-8 -*-
"""检索基础设施异常(统一出口)。

Qdrant 等派生索引不可用(初始化失败 / 重试耗尽)时抛 RetrievalUnavailable:
- 上层 search_knowledge 捕获 → 降级 SQLite 关键词检索(Stage 1);
- 无任何兜底数据时 → 交 _run_tool 记 error_code=retrieval_unavailable,LLM 诚实拒答(Stage 2)。
独立成模块:不被 retrieval 内部 import 成环。
"""


class RetrievalUnavailable(Exception):
    """检索服务不可用(向量库异常/未启动,重试后仍失败)。"""
