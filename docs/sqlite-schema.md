# SQLite 事实源设计(docs/sqlite-schema.md)

参考 dsh:core/session(append-only 事件日志)+ session-persistence-sqlite(schema 版本 fail-closed)。
**只 INSERT,不 UPDATE 历史。** 单写者 = 唯一的 agent 服务进程。

## 数据分层边界:什么存 SQLite

| 内容 | 存储 | 说明 |
|---|---|---|
| 对话内容、检索片段快照、回答、引用 | events(SQLite) | 事实源;模型可见 ⟺ 已记录 |
| 工具调用/结果(截断后)、审批、token 账本 | events / approvals(SQLite) | 可审计 |
| 文档原文 + 历史版本 | chunks(SQLite) | 引用寻址,条款更新不失效 |
| 向量 | Qdrant | 派生索引,可从 chunks 重建 |
| 嵌入/工具缓存、限流计数 | Redis | 可丢失,清空照常跑 |
| 条款 PDF/Word 原件 | 文件系统 | 源头文档;chunks 存解析快照 + source_path |
| 超大工具输出 | 截断入库,完整内容落文件 | 防膨胀 |

边界规则:凡进入模型请求的内容,必须在 SQLite 中可重建;向量与缓存不在其列。
详见 docs/qdrant-schema.md 与 docs/redis-usage.md。

## 连接与 PRAGMA

```sql
PRAGMA journal_mode=WAL;        -- 读写并发
PRAGMA busy_timeout=5000;       -- 写锁等待
PRAGMA foreign_keys=ON;
PRAGMA synchronous=NORMAL;      -- WAL 下足够,兼顾性能
```

## 1. meta —— schema 版本(第一天就做,fail-closed)

```sql
CREATE TABLE meta (
  key   TEXT PRIMARY KEY,          -- 'schema_version' | 'format_version'
  value TEXT NOT NULL
);
-- 启动时:读取 schema_version,与代码内常量比对;
-- 不认识 / 高于当前版本 → 拒绝启动,绝不静默迁移未知格式(dsh 同款机制)。
```

## 2. sessions —— 会话元数据

```sql
CREATE TABLE sessions (
  id         TEXT PRIMARY KEY,     -- uuid
  title      TEXT,
  user_id    TEXT NOT NULL,        -- 客服工号
  created_at TEXT NOT NULL,
  status     TEXT NOT NULL DEFAULT 'active'  -- active | closed
);
```

## 3. events —— append-only 会话日志(核心,只 INSERT)

```sql
CREATE TABLE events (
  seq        INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL REFERENCES sessions(id),
  type       TEXT NOT NULL,        -- 注册表类型,见下
  ts         TEXT NOT NULL,        -- ISO8601 UTC
  payload    TEXT NOT NULL         -- JSON,按 type 校验(Pydantic)
);
CREATE INDEX idx_events_session ON events(session_id, seq);
CREATE INDEX idx_events_type    ON events(type);
CREATE INDEX idx_events_ts      ON events(ts);
```

### 事件类型注册表(允许集合,启动时校验;未注册类型 → 拒绝加载日志)

| type | payload 要点 | 说明 |
|---|---|---|
| `user_message` | text, client_time | 客服提问 |
| `retrieval` | query, chunks:[{chunk_id, score, doc_id, version, section, source, content}] | **检索片段注入前写入**(铁律 1;content 即原文快照,历史角标靠它可点) |
| `assistant_message` | text, citations:[{idx, chunk_id}] | 最终回答+引用(结构化输出) |
| `assistant_chunk` | delta | 流式增量,仅回放/前端用 |
| `tool_call` | tool, args | 工具调用(含外部 API) |
| `tool_result` | tool, ok, result_truncated, error? | 结果(截断后) |
| `approval_request` | tool, args, reason | 写入型工具发起审批 |
| `approval_decision` | status(approved/denied/expired), decided_by | 审批决定(可审计) |
| `reject_answer` | reason | 不确定拒答(转人工),留痕 |
| `usage` | model, prompt_tokens, completion_tokens, cost_estimate | token 账本 |
| `turn_start` / `turn_end` | — | 回合边界 |

## 4. chunks —— 文档原文登记处(引用寻址 + 历史版本)

```sql
CREATE TABLE chunks (
  chunk_id      TEXT PRIMARY KEY,  -- 稳定 id:doc_id:version:section:index
  doc_id        TEXT NOT NULL,
  version       TEXT NOT NULL,
  effective_from TEXT,             -- 生效日期(条款)
  effective_to  TEXT,              -- 失效日期(NULL=现行)
  section       TEXT,
  title         TEXT,
  content       TEXT NOT NULL,     -- 原文(点角标展示用)
  source_path   TEXT,
  doc_type      TEXT,              -- policy_document | structured | faq | sales_script
  checksum      TEXT,
  ingested_at   TEXT NOT NULL
);
CREATE INDEX idx_chunks_doc ON chunks(doc_id, version);
CREATE INDEX idx_chunks_type ON chunks(doc_type);
```

历史版本策略:旧版本文档**保留在 chunks,不删除**;Qdrant 只索引现行版本。
chunk_id 带版本号 ⇒ 旧引用定位旧版本原文,条款更新不失效。

## 5. approvals —— 审批流水(可审计)

```sql
CREATE TABLE approvals (
  id         TEXT PRIMARY KEY,     -- uuid
  session_id TEXT NOT NULL,
  tool       TEXT NOT NULL,
  args_json  TEXT NOT NULL,
  status     TEXT NOT NULL,        -- pending | approved | denied | expired
  decided_by TEXT,                 -- 审批人工号
  reason     TEXT,
  requested_at TEXT NOT NULL,
  decided_at  TEXT
);
CREATE INDEX idx_approvals_status ON approvals(status);
CREATE INDEX idx_approvals_session ON approvals(session_id);
```

## 6. 审计视图 + 导出

```sql
-- 审计 = events 的查询视图(铁律 1 免费提供)
CREATE VIEW audit AS
SELECT session_id, ts, type, payload
FROM events
WHERE type IN ('user_message','retrieval','assistant_message',
               'tool_call','tool_result','approval_request','approval_decision','reject_answer')
ORDER BY seq;
-- 导出:按 user_id/时间/产品筛选 → JSON/CSV(合规留证)
```

## 备份

WAL 模式下:定期 `VACUUM INTO 'backup.db'` 或 sqlite3 .backup;Qdrant 可由 chunks 全量重建(黄金法则)。
