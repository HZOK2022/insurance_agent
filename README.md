# 保险销售客服知识助手 (Insurance Sales Agent)

单机单进程的 Python agent,给线上保险销售客服提供实时、可追溯的知识问答与销售指导。
架构参考 DeepSeek Harness(dsh)的核心模块设计,但**砍掉扩展性机器,保留脊梁与不变式**。

## 功能概览

- **知识检索(RAG)** —— 条款/病种/规则检索:Qdrant 稠密 + BM25 混合 + bge 重排,按保险类别软圈定;派生索引可重建。
- **ReAct Agent Loop** —— 判定是否需要检索 → 调工具 → 流式生成 → 结构化最终回答;业务层(保险)与核心解耦。
- **引用可追溯** —— 回答带 [idx] 角标,点击定位 chunk 原文(含历史版本,条款更新不失效)。
- **上下文管理** —— 窗口上限 + 工具结果剪枝 + **保尾压头压缩**(§8.3 座席工作台 checkpoints,压力/溢出双触发),多轮引用跨轮复用。
- **护栏** —— 提示注入检测、RAG 投毒隔离、输出系统泄漏掩码、PII 脱敏、写工具人工审批、诚实拒答转人工。
- **审计与可观测** —— append-only 事件日志(事实源)+ 历史问答查询/导出(合规留证)+ 指标聚合(延迟/token/错误率/成本)。
- **会话与鉴权** —— 多客服账号(users + auth_tokens,pbkdf2),登录页,Bearer 双通道鉴权 + 进程内限流。
- **记忆系统(可插拔,默认关)** —— 三层记忆(会话回源 / 跨会话沉淀 / 画像),详见下文。
- **评测** —— 40 条评估集 + LLM-judge 打分(质量门,评估驱动)。

## 技术选型(已定)

| 项 | 选择 |
|---|---|
| 语言/运行时 | Python 3.12+ (FastAPI + uvicorn,SSE 流式) |
| LLM | DeepSeek API(流式 + 结构化,指数退避重试) |
| 嵌入 | bge-large-zh-v1.5(本地,默认) |
| 前端 | Vite + React 18 + TS(仿 dsh 视觉,零插件机制) |
| 事实源 | SQLite(WAL · append-only · 单写者) |
| 派生索引 | Qdrant(只索引当前生效版本)+ BM25(从 SQLite 构建) |
| 加速层 | Redis(嵌入缓存/工具缓存/限流/预算) |
| 自主度 | 读工具放行,写型 API 工具人工审批 |
| 追溯 | 回答带出处引用,点击角标定位 chunk 原文(含历史版本) |
| 记忆 | 跨会话记忆(可插拔,默认关):SQLite memory_entries 表 + memory_save/search/forget 工具 + memory_enabled 开关 |

## 黄金法则

> SQLite = 事实源 · Qdrant = 可重建的派生索引 · Redis = 可丢失的加速层
> 易失层(Redis/Qdrant)全空,系统照常跑,只是慢。

## 核心铁律

1. **模型可见 ⟺ 已记录**:进模型请求的一切(含检索片段、引用、回答)都落 SQLite,历史对话角标永远可点。
2. **易失层可随时清空**:事实源只有 SQLite。
3. **回答必须可追溯**:不确定就拒答转人工;引用绑定版本,条款更新不失效。
4. **上限加在完整结果上**:步数 / token / 字节三重上限 + 每客服 token 预算;上限集中在 config/,禁止散落硬编码。

## 记忆系统(可插拔增强,默认关)

三层记忆,按"定位"划分(明细 docs/memory-design.md):
- **L1 会话级·防遗忘**:events + build_history + 压缩 checkpoint 已有;补 session_history_search(本会话历史检索,弥补多次压缩丢的早期原文;会话 id 由系统注入,杜绝跨会话)。
- **L2 跨会话级·通用沉淀**:memory_entries 表 + memory_save/memory_search/memory_forget 工具 + MEMORY_SYSTEM 指令;LLM 在使用中决定记什么/改什么/忘什么;只存"跨会话、跨客户**通用**"的(会话独有/单客户/客户隐私一律不记)。
- **L3 客服画像·偏好**(预留,人可编辑)。

启用:.env 设 MEMORY_ENABLED=true(默认 **False**)。**关=不注册记忆工具/不加指令帧/不注入,行为与未加记忆完全一致(非侵入)**;删除 app/memory/ 即整体移除。

设计理念:
- 记忆落 SQLite(黄金法则),每次写/忘追加 memory_upsert/memory_archive/memory_injected 事件(可审计)。
- 会话 id 由系统注入 handler,结构上杜绝跨会话/越权。
- 防堆积:单条 memory_entry_max_chars 截断 + 总量 memory_total_budget_chars 超限触发"记忆压实"(合并同类/删取代/按优先级归档,redline 永不压)。
- 工具遵循"默认不记":知识库覆盖的/单次查询答案/客户个体信息一律不记;显式"记住"也要先过"会话性 vs 全局性"。

## 架构与模块

| 模块 | 位置 | 职责 |
|---|---|---|
| 事件注册表 | app/session/events.py | 事件类型 fail-closed;未注册类型拒绝启动 |
| 存储 | app/session/store.py | SQLite append-only(events/sessions/users/memory_entries);单写者 |
| 上下文 | app/session/context.py | 事件→对话历史折合;会话级 chunk 注册表(跨轮引用) |
| Agent Loop 核心 | app/loop/agent_loop.py | ReAct 状态机 + 块组装 + 原生工具回喂 + 取消 |
| 保险业务层 | app/businesses/insurance.py | system/工具表/present_answer(引用溯源) |
| 检索 | app/retrieval/ | chunker/embedder/qdrant/hybrid/reranker/knowledge_store/categories |
| 压缩 | app/compaction/compactor.py | 保尾压头;§8.3 座席工作台 checkpoints |
| 护栏 | app/guardrails/ | approval/injection/rag/redact |
| 审计/可观测 | app/audit/queries.py · app/observability/metrics.py | 查询/导出 · 指标聚合 |
| 记忆 | app/memory/ | store/system/tools(可插拔) |
| 配置 | app/config/config.py | 集中上限/阈值(禁止散落硬编码) |
| API | app/api/ | FastAPI 路由/服务/schema;登录/审批/审计/metrics |
| LLM | app/llm/client.py | DeepSeek 流式 + 重试/退避 |

## 运行

> ⚠ 后端须用带依赖的解释器(本项目用另一项目的 rag_env):
> D:\LLM\huai_test\agentic_rag_ins\rag_env\Scripts\python.exe
> (基础 python 缺 fastapi/uvicorn/sentence_transformers。)

**后端**(端口 8181,reload 只监视 app/):
```bash
<rag_env>\python.exe run.py
```

**前端**(Vite dev / build,web/):
```bash
cd web && npm install && npm run dev      # 开发
npm run build                             # 产物 web/dist,后端托管
```

**知识摄取**:
```bash
<rag_env>\python.exe scripts/ingest_kb.py --path <条款文件/目录> [--clear] [--category 医疗险]
```

## 配置(.env)

复制 .env.example 为 .env 并填入真实值。关键项:
- **必填**:DEEPSEEK_API_KEY;检索重排 RERANKING_EXTERNAL_API_KEY;存储 QDRANT_URL/REDIS_URL/SQLITE_PATH。
- **记忆**:MEMORY_ENABLED=true(默认关)。
- **登录**:LOGIN_USER/LOGIN_PASSWORD(默认 admin/change-me,正式环境请改)。
- **鉴权**:API_TOKEN(接口 Bearer;空=开发模式)。
- 其余上限/阈值全在 app/config/config.py 集中(改 .env 后重启后端生效)。

## 测试

- **单元/回放**:python -m unittest discover -s tests(168 项;含录制-回放 tests/replay,改 prompt/条款/工具 schema 必跑)。
- **评测**:<rag_env>\python.exe scripts/eval_agent.py(含 40 条评估集 + LLM-judge 质量门)。
- **UI 自测**:python scripts/selftest_ui.py(build→起后端→造会话→截图)。
- **其它脚本**:scripts/seed_premium.py(费率库播种)、scripts/rebuild_qdrant.py(重建派生索引)。

## 文档索引

- 架构:docs/architecture-v3.md · 骨架与 dsh 参照:docs/project-skeleton.md · 契约:docs/contract.md
- 存储:docs/sqlite-schema.md · docs/qdrant-schema.md · docs/redis-usage.md
- 业务层:docs/businesses.md · 上下文管理:docs/context-management.md · 评测/可观测:docs/evaluation-and-ops.md · 记忆:docs/memory-design.md
- 学习教程(过程叙事):docs/learning/(00-11)

## dsh 参考源码

本项目按模块参照 DeepSeek Harness(dsh)源码的结构与不变式实现(抄结构,不抄机制):
- 本地 checkout:D:\LLM\deepseek-harness-master(当前机器)
- 公开仓库:https://github.com/deepseek-ai/deepseek-harness(参照版本 0.1.2-alpha.1)
- 模块→dsh 包路径对照:docs/project-skeleton.md
