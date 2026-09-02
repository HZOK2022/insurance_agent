# 保险销售客服知识助手 (Insurance Sales Agent)

单机单进程的 Python agent,给线上保险销售客服提供实时、可追溯的知识问答与销售指导。
架构参考 DeepSeek Harness(dsh)的核心模块设计,但**砍掉扩展性机器,保留脊梁与不变式**。

## 技术选型(已定)

| 项 | 选择 |
|---|---|
| 语言/运行时 | Python 3.12+ |
| LLM | DeepSeek API(外部调用,已确认合规) |
| 嵌入 | bge-m3(本地,默认)或 API |
| 前端 | Vite + React 18 + TS(仿 dsh 视觉,零插件机制)\n| 服务外壳 | FastAPI + SSE |
| 事实源 | SQLite(WAL · append-only · 单写者) |
| 派生索引 | Qdrant(只索引当前生效版本) |
| 加速层 | Redis(嵌入缓存/工具缓存/限流/预算) |
| 自主度 | 关键操作审批:知识查询只读放行,写入型 API 工具审批 |
| 追溯 | 回答带出处引用,点击角标定位 chunk 原文(含历史版本) |
| 记忆 | 跨会话记忆(可插拔,默认关):SQLite memory_entries 表 + memory_save/search/forget 工具 + `memory_enabled` 开关 |

## dsh 参考源码

本项目按模块参照 DeepSeek Harness(dsh)源码的结构与不变式实现(抄结构,不抄机制):
- 本地 checkout:`D:\LLM\deepseek-harness-master`(当前机器)
- 公开仓库:`https://github.com/deepseek-ai/deepseek-harness`(参照版本 0.1.2-alpha.1)
- 模块→dsh 包路径对照:docs/project-skeleton.md

## 黄金法则

> SQLite = 事实源 · Qdrant = 可重建的派生索引 · Redis = 可丢失的加速层
> 易失层(Redis/Qdrant)全空,系统照常跑,只是慢。

## 核心铁律

1. **模型可见 ⟺ 已记录**:进模型请求的一切(含检索片段、引用、回答)都落 SQLite,历史对话角标永远可点。
2. **易失层可随时清空**:事实源只有 SQLite。
3. **回答必须可追溯**:不确定就拒答转人工;引用绑定版本,条款更新不失效。
4. **上限加在完整结果上**:步数 / token / 字节三重上限 + 每客服 token 预算。

## 写代码前的准备清单(按顺序)

- [ ] **0. 开工前读三份**:AGENTS.md(会话协议)→ STATUS.md(进度)→ DECISIONS.md(决策理由),几分钟
- [ ] **1. 回放测试基建(第一天就做)**:录制-回放测试通道,让测试不依赖真实 API。参考 dsh `test-support/llm-replay` 思路,Python 里用 VCR 模式自写。见 docs/project-skeleton.md。
- [ ] **2. schema 版本 fail-closed**:events 表类型注册表,日志里出现未注册类型=拒绝启动。见 docs/sqlite-schema.md。
- [ ] **3. 知识采集**:收集三类样本各 5-10 份——条款 PDF/Word、结构化产品数据(字段清单)、销售话术与 FAQ。这是 ⑧ 摄取管道的验收数据。
- [ ] **4. 引用验收标准**:回答带角标,点击定位 chunk 原文(含历史版本)。把这条写进测试用例。
- [ ] **5. 预算**:定每月 LLM 预算数值与告警阈值(写入 `.env`)。
- [ ] **6. 环境**:`python -m venv .venv`、`pip install -r requirements.txt`(先装 fastapi/uvicorn/pydantic/openai/sqlalchemy?/qdrant-client/redis)、复制 `.env.example` 为 `.env` 并填入真实地址。
- [ ] **7. 外部 API 清单**:确认哪些是读(放行)、哪些是写(审批),为每个工具写 schema。

## 记忆系统(可插拔增强,默认关)

三层记忆,按"定位"划分(明细 docs/memory-design.md):
- **L1 会话级·防遗忘**:events + build_history + 压缩 checkpoint 已有;补 `session_history_search`(本会话历史检索,弥补多次压缩丢的早期原文;会话 id 由系统注入,杜绝跨会话)。
- **L2 跨会话级·通用沉淀**:`memory_entries` 表 + `memory_save`/`memory_search`/`memory_forget` 工具 + `MEMORY_SYSTEM` 指令;LLM 在使用中决定记什么/改什么/忘什么;只存"跨会话、跨客户**通用**"的(会话独有/单客户/客户隐私一律不记)。
- **L3 客服画像·偏好**(预留,人可编辑)。

启用:`.env` 设 `MEMORY_ENABLED=true`(默认 **False**)。**关=不注册记忆工具/不加指令帧/不注入,行为与未加记忆完全一致(非侵入)**;删除 `app/memory/` 即整体移除。

设计理念:
- 记忆落 SQLite(黄金法则),每次写/忘追加 `memory_upsert`/`memory_archive`/`memory_injected` 事件(可审计)。
- 会话 id 由系统注入 handler,结构上杜绝跨会话/越权。
- 防堆积:单条 `memory_entry_max_chars` 截断 + 总量 `memory_total_budget_chars` 超限触发"记忆压实"(合并同类/删取代/按优先级归档,`redline` 永不压)。
- 工具遵循"默认不记":知识库覆盖的/单次查询答案/客户个体信息一律不记;显式"记住"也要先过"会话性 vs 全局性"。

**注意**:启用开关且启动后,`memory_entries` 表会在建表时自动创建(Schema 版本不变)。

## 目录

见 docs/project-skeleton.md(含每个模块对应的 dsh 源码参照路径)。
架构总览:docs/architecture-v3.md,图:docs/diagram/agent-v3.html / agent-v3.png。