# 保险销售客服知识助手 (Insurance Sales Agent)

单机单进程的 Python agent,给线上保险销售客服提供实时、可追溯的知识问答与销售指导。
架构参考 DeepSeek Harness(dsh)的核心模块设计,但**砍掉扩展性机器,保留脊梁与不变式**。

## 技术选型(已定)

| 项 | 选择 |
|---|---|
| 语言/运行时 | Python 3.12+ |
| LLM | DeepSeek API(外部调用,已确认合规) |
| 嵌入 | bge-m3(本地,默认)或 API |
| 服务外壳 | FastAPI + SSE |
| 事实源 | SQLite(WAL · append-only · 单写者) |
| 派生索引 | Qdrant(只索引当前生效版本) |
| 加速层 | Redis(嵌入缓存/工具缓存/限流/预算) |
| 自主度 | 关键操作审批:知识查询只读放行,写入型 API 工具审批 |
| 追溯 | 回答带出处引用,点击角标定位 chunk 原文(含历史版本) |

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

## 目录

见 docs/project-skeleton.md(含每个模块对应的 dsh 源码参照路径)。
架构总览:docs/architecture-v3.md,图:docs/diagram/agent-v3.html / agent-v3.png。