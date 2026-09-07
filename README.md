# 保险销售客服知识助手 (Insurance Sales Agent)

内部"保险销售客服副驾驶(copilot)"——给线上保险销售客服提供**实时、可追溯**的知识问答与销售指导。
架构参考 DeepSeek Harness(dsh)的核心模块设计(参照版本 `0.1.2-alpha.1`),但**砍掉扩展性机器,保留脊梁与不变式**(不引入 Cordis 插件系统 / 事件总线 / capability seam)。

## 部署拓扑与事实源

> 生产**两机部署**:应用服务器 + 数据库服务器。

```
[应用服务器 A]                                    [数据库服务器 B]
  FastAPI + Agent 循环 + 检索 + LLM 客户端           MySQL(事实源,唯一真相)
  Qdrant(向量索引,同机 localhost,派生/可重建)         ├ 会话/事件/记忆  (db_name)
  (可选) Redis(缓存/加速,可丢)                       ├ 知识          (knowledge_db_name)
  /metrics + 结构化日志                              └ 费率          (premium_db_name)
```

- **事实源 = 数据库服务器的 MySQL**(会话/事件/记忆/知识/费率);**SQLite 仅开发/测试回退**(未配 `db_host` 时)。
- **单写者**:只有应用服务器部署写事实源(避免多实例写冲突),events 只 INSERT。
- **黄金法则**:`MySQL = 事实源 · Qdrant = 可重建的派生索引 · Redis = 可丢失的加速层`。易失层(Qdrant/Redis)全空系统照常跑,只是慢。

## 核心能力(现状)

- **知识检索(RAG)** — 摄取(三路解析 backend:mineru / markitdown / pdfplumber + 结构化切块 + 目录树)后:稠密(bge) + BM25 混合检索 + bge 重排;按**产品名**/**保险类别**软圈定;产品名唯一 + 内容 hash 判重(防误覆盖)。Qdrant/BM25 为派生索引,可重建。
- **Agent Loop(ReAct)** — turn/step 状态机;LLM 判定是否检索/调工具,流式生成(assistant_chunk),业务层(保险)与核心解耦;原生工具调用 + 叙述。
- **回答可追溯** — 回答带 `[idx]` 角标,点击定位 chunk 原文(**引用绑定版本**,条款更新不失效);检索片段在注入模型**之前**写入事件日志,历史会话角标永远可点。
- **上下文管理** — 窗口上限 + 工具结果剪枝 + **保尾压头压缩**(§8.3 座席工作台 checkpoint,pressure/context-overflow 双触发);跨轮引用复用;会话内回源检索(`session_history_search`)。
- **产品消歧(主动追问)** — 问题依赖具体产品(保费/免赔额/等待期/能否报销等)却无法从上下文确定是哪款时,**主动中断追问**并列出在售产品,用户回答后再继续原问题。
- **护栏** — 提示注入检测 · RAG 投毒隔离 · 输出系统泄漏掩码 · PII 脱敏 · 写工具人工审批 · 诚实拒答转人工。
- **审计与可观测** — append-only 事件日志(事实源)+ 历史问答查询/导出(合规留证)+ 指标聚合(`/api/metrics`)+ 观测总览(`/api/observability`)+ 时间序列(`/api/metrics/timeseries`)+ **P0 异常定位器**(坏轮分类 + 检索→引用漏斗)+ 告警脚本。
- **会话与鉴权** — 多客服账号(users + auth_tokens,pbkdf2 + 恒定时间比较)+ 登录页;`Authorization: Bearer` 双通道鉴权 + 进程内限流。
- **记忆系统(可插拔,默认关)** — 三桶记忆(用户画像 / 跨会话沉淀 / 会话限定)+ 管理面板 + 按桶压实;`MEMORY_ENABLED=true` 才启用(关=行为与未加一致,非侵入)。
- **评测** — 40+ 条评估集(10 类)+ LLM-judge 六维打分 + 程序化引用校验;评估驱动修复;录制-回放测试(改 prompt/条款/工具 schema 必跑)。

## 技术选型

| 项 | 选择 |
|---|---|
| 语言/运行时 | Python 3.11+(FastAPI + uvicorn,SSE 流式) |
| LLM | DeepSeek API(流式 + 指数退避重试,`deepseek-v4-flash`/`-pro` 可选) |
| 嵌入 | bge-large-zh-v1.5(本地默认 / 在线 SiliconFlow 可选) |
| 事实源 | MySQL(生产) / SQLite(开发/测试回退,append-only · 单写者) |
| 派生索引 | Qdrant(向量)+ BM25(从事实源构建) |
| 加速层 | Redis(可选,可丢) |
| 前端 | Vite + React 18 + TS(仿 dsh 视觉,零插件机制) |
| 自主度 | 读工具放行,写型 API 工具人工审批 |
| 追溯 | 回答带出处引用,点击角标定位 chunk 原文(绑定版本) |

## 核心铁律(AGENTS)

1. **模型可见 ⟺ 已记录**:进模型请求的一切(含检索片段、引用、回答)都落事实源;新的模型可见输入 = 新事件类型,必须先注册。
2. **易失层可随时清空**:事实源只有数据库的 MySQL(开发/测试回退 SQLite)。
3. **回答必须可追溯**:不确定就拒答转人工;引用绑定版本,条款更新不失效。
4. **上限加在完整结果上**:步数 / token / 字节三重上限 + 每客服 token 预算;上限集中在 config/,禁止散落硬编码。
5. **events 只 INSERT**:恢复与修改一律走新事件;事件类型先注册,未注册类型拒绝启动(fail-closed)。

## 架构与模块

| 模块 | 位置 | 职责 |
|---|---|---|
| 事件注册表 | app/session/events.py | 事件类型 fail-closed;未注册类型拒绝启动 |
| 存储(事实源) | app/session/store.py | events/sessions/users/memory_entries;append-only;单写者 |
| 上下文 | app/session/context.py | 事件→对话历史折合;会话级 chunk 注册表(跨轮引用) |
| Agent Loop 核心 | app/loop/agent_loop.py | ReAct 状态机 + 块组装 + 原生工具回喂 + 取消/中止 |
| 保险业务层 | app/businesses/insurance.py | system / 工具表 / present_answer(引用溯源) |
| 费率计算 | app/businesses/premium.py · premium_ax.py | 费率事实源 PremiumStore + calculate_premium(查表确定性) |
| 检索 | app/retrieval/ | chunker / embedder / qdrant / hybrid / reranker / knowledge_store / categories / search_tool |
| 压缩 | app/compaction/compactor.py | 保尾压头;§8.3 座席工作台 checkpoints |
| 护栏 | app/guardrails/ | approval / injection / rag / redact |
| 审计/可观测 | app/audit/queries.py · app/observability/metrics.py | 查询/导出 · 指标聚合/异常定位 |
| 记忆 | app/memory/ | store / system / tools(可插拔,默认关) |
| 配置 | app/config/config.py | 集中上限/阈值(禁止散落硬编码) |
| API | app/api/ | FastAPI 路由/服务/schema;登录/审批/审计/观测 |
| LLM | app/llm/client.py | DeepSeek 流式 + 重试/退避 |

## 运行

> ⚠ 后端须用带依赖的解释器(本项目用另一项目的 `rag_env`):
> `D:\LLM\huai_test\agentic_rag_ins\rag_env\Scripts\python.exe`
> (基础 Python 缺 fastapi/uvicorn/sentence_transformers 等。)

**后端**(端口 8181,reload 只监视 `app/`;生产建议关 reload、多 worker):
```bash
<rag_env>\python.exe run.py
```

**前端**(web/;生产 `npm run build` 后产物 `web/dist` 由后端托管):
```bash
cd web && npm install && npm run dev      # 开发(Vite 代理 /api)
npm run build                             # 生产构建
```

**知识摄取**:
```bash
<rag_env>\python.exe scripts/ingest_kb.py --path <条款文件/目录> [--clear] [--category 医疗险] [--parser mineru|markitdown|pdfplumber|native]
```

**费率播种 / 索引重建**(分别见 scripts/seed_premium.py、scripts/rebuild_qdrant.py)。

**迁移**(SQLite → MySQL):scripts/migrate_to_mysql.py / migrate_sqlite_to_mysql.py / migrate_kb_premium_to_mysql.py。

## 配置(.env)

复制 `.env.example` 为 `.env` 并填入真实值(改后重启后端生效)。重点:
- **必填**:`DEEPSEEK_API_KEY`;重排 `RERANKING_EXTERNAL_API_KEY`;存储 `QDRANT_URL`/`REDIS_URL`。
- **事实源**:`DB_ENABLED=true` + `DB_HOST/DB_PORT/DB_USER/DB_PASS/DB_NAME`(生产 MySQL);未配 `DB_HOST` 则回退 SQLite。
- **嵌入**:`EMBEDDING_BACKEND=local`(默认)或 `api`(在线 SiliconFlow,key 留空复用重排 key)。
- **记忆**:`MEMORY_ENABLED=true`(默认关)。
- **登录/鉴权**:`LOGIN_USER`/`LOGIN_PASSWORD`(默认 admin/change-me,正式请改);`API_TOKEN`(接口 Bearer,空=开发模式免鉴权)。
- 其余上限/阈值/日志/坏例快照全集中在 `app/config/config.py`,用 `.env` 覆盖。

## 测试 / 评测 / 运维

- **单元/回放**:`<rag_env>\python.exe -m unittest discover -s tests`(全量,当前 328 项;含录制-回放 `tests/replay`,改 prompt/条款/工具 schema 必跑)。
- **评测(质量门)**:
  - `scripts/eval_agent.py` — 跑评估集 → LLM-judge 六维打分 + 违规标记 + 汇总(40+ 条,10 类)。
  - `scripts/eval_citation.py` — **程序化引用校验**(角标 → 真实 chunk、是否本轮召回、是否支撑断言),独立于 LLM-judge。
- **观测告警**:`scripts/check_metrics.py`(从 events 聚合,阈值告警,告警带 trace_id 可回放)。
- **UI 自测**:`python scripts/selftest_ui.py`(build→起后端→造会话→截图)。

## 文档索引

- 架构:docs/architecture-v3.md(图 docs/diagram/agent-v3.html)· 部署:docs/deployment.md · 骨架与 dsh 参照:docs/project-skeleton.md · 契约:docs/contract.md
- 存储:docs/sqlite-schema.md · docs/qdrant-schema.md · docs/redis-usage.md · MySQL 迁移:docs/learning/13-mysql-migration.md
- 业务层:docs/businesses.md · 上下文管理:docs/context-management.md · 评测/可观测:docs/evaluation-and-ops.md · 记忆:docs/memory-design.md
- 切块:docs/chunking-design.md · MinerU:docs/mineru-api-docs.md · 知识库管理:docs/产品文档-知识库管理.md
- 学习教程(过程叙事):docs/learning/(00-overview … 14-product-disambiguation)

## dsh 参照源码

本项目按模块参照 DeepSeek Harness(dsh)源码的结构与不变式实现(抄结构,不抄机制;dsh 是只读参照,不成为运行依赖):
- 本地 checkout:`D:\LLM\deepseek-harness-master`(完整源码,含前端 packages/client)
- 公开仓库:https://github.com/deepseek-ai/deepseek-harness(参照版本 `0.1.2-alpha.1`)
- 模块→dsh 包路径对照:docs/project-skeleton.md
