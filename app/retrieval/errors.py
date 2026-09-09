# -*- coding: utf-8 -*-
"""检索基础设施异常(统一出口)。

Qdrant 等派生索引不可用(初始化失败 / 重试耗尽)时抛 RetrievalUnavailable:
- 上层 search_knowledge 捕获 → 降级 SQLite 关键词检索(Stage 1);
- 无任何兜底数据时 → 交 _run_tool 记 error_code=retrieval_unavailable,LLM 诚实拒答(Stage 2)。
独立成模块:不被 retrieval 内部 import 成环。
"""


class RetrievalUnavailable(Exception):
    """检索服务不可用(向量库异常/未启动,重试后仍失败)。

    语义 = 上游基础设施故障(连不上/超时/5xx):可重试、可冷却、计入"向量库宕机"指标。
    """


class RetrievalClientError(Exception):
    """检索请求构造/参数非法(确定性客户端错误:pydantic 校验失败 / 4xx 等)。

    与 RetrievalUnavailable 严格区分:这是**代码层 bug**(拼的请求/Qdrant schema 不合法),
    不是上游服务故障。特征:重试必然同样失败、冷却会**误诊**为"服务宕机"并连累后续检索,
    因此必须**立即抛出、不重试、不进冷却期**,并归到 tool_error(告警开发而非运维)。
    """
