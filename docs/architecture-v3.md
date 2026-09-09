# 架构总览(v3)

文本版对应 docs/diagram/agent-v3.png / agent-v3.html。编号沿用七模块清单,⑧ 为知识检索,⑨ 为审计/追溯层。

## 模块与 dsh 参照

| # | 模块 | dsh 参照实现 | Python 替代 |
|---|---|---|---|
| ⑦ | 服务化外壳:FastAPI + SSE,会话并发隔离(asyncio),内部 token,Redis 限流 | apps/cli + api/gateway + api/session-controller | FastAPI |
| ① | Agent Loop:硬编码 turn/step 循环,三重硬上限,取消传播,429/5xx 退避 | core/agent-loop(src/index.ts) | 自写状态机 |
| ⑧ | 知识检索(核心):三条摄取管道,chunk_id 稳定+版本过滤,混合检索,原文快照 | dsh 无 RAG,参考 mcp-client 接外部服务 | Qdrant + bge-m3 |
| ② | LLM 客户端:DeepSeek API,流式,结构化输出(answer+citations),重试/熔断,畸形 JSON 容错 | llm/llm + llm-deepseek + token-meter | openai SDK + Pydantic |
| ③ | 工具层:外部 API 封装,读放行/写审批,Pydantic 校验,每工具超时,结果截断 | core/tools(校验→执行→回填管线) | 自写 registry + Pydantic |
| ④ | 会话与上下文:append-only 事件日志,崩溃恢复,窗口管理,检索快照入日志 | core/session + session-persistence-sqlite | 自写 store |
| ⑤ | 安全护栏:写入型审批,只读放行,执行隔离,输出预算 | sandbox/* + interaction/user-approval | 自写 approval |
| ⑥ | 可观测:结构化日志+trace_id,成本计量,回放测试 | session-telemetry-otel + test-support/llm-replay | structlog + VCR |
| ⑨ | 审计/追溯层:回答+引用+检索快照可查,角标→chunk,审计导出 | 由会话日志查询视图构成(无独立系统) | SQLite 视图+导出 |

## 引用角标链路(验收标准)

回答结构化输出 `{answer, citations:[{idx, chunk_id}]}` → 前端渲染角标 →
点击角标 → 按 chunk_id 查 SQLite(events 里的检索快照 / chunks 原文表)→ 弹层展示原文+来源(含历史版本)。

**关键**:检索片段在注入模型前就写入 events 日志(铁律 1),因此历史会话的角标永远可点,即使条款已更新(引用绑定版本)。

## 审计/追溯层构成

- events 日志 = 审计的原始数据(用户提问、检索片段、回答、引用、工具调用、审批决定全部在日志里)
- 查询视图:按客服 / 时间 / 产品检索历史问答
- 导出:审计报表(合规留证,给监管/内审)
- 错误兜底:不确定就拒答转人工,并记录"拒答"事件

## 抄 dsh:抄什么、不抄什么

**抄**:append-only 日志、schema 版本 fail-closed(不认识就拒绝)、完整结果上限、模型可见⟺已记录、审批持久化与可审计、回放测试。

**不抄**:Cordis 插件系统、事件总线、capability seam 三角色、profile/bundle/patch、多 provider 适配器、fork/投影/compaction 多策略。

## 已定决策(来自需求访谈)

1. 语言 Python,模块逻辑照抄 dsh 源码思路。2. 业务:保险销售客服实时知识问答,提升销售能力。3. 自主度:关键操作审批。4. 部署:单机单进程。5. 合规:客户数据可走 DeepSeek API(已确认)。6. 知识源:条款文档+结构化数据+FAQ 混合。7. 追溯:回答带出处引用,角标点击定位 chunk 原文。8. 外部 API:未定,按读放行/写审批默认设计。

## 待定(不阻塞开工)

并发客服数、前端形态(独立 Web 页 vs 嵌入客服系统)、错误纠错反馈流、embedding 具体模型、每月预算数值、团队时间线。
