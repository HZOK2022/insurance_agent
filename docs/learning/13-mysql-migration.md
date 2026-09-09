# 13 · SQLite → MySQL 迁移(生产两机)

> 章节 00 的模板见 `docs/learning/00-overview.md`。本篇叙述把事实源从本地 SQLite 迁到数据库服务器的 MySQL 的动因、改造点与坑。

## 为什么

SQLite 是嵌入式、单文件、单进程,跨机器无法共享(生产是"应用一台 + 数据库一台")。事实源必须移到数据库服务器的 MySQL;Qdrant 是派生索引(可重建),Redis 是易失加速层,这俩继续留在应用服务器。

迁移不是"换个文件扩展名":SQLite 与 MySQL 的方言在几个地方分家,必须逐点处理。核心约定见 `docs/deployment.md` 与 `DECISIONS D2/D4`。

## 统一连接层 `app/db.py`

store 不再各自 `sqlite3.connect`;统一走:

- `dbmod.dial(cfg)`:`cfg.db_host` 非空 → `"mysql"`,否则 `"sqlite"`。
- `dbmod.ph(cfg)` / `dbmod.translate(sql,cfg)`:SQL 里一律写 `?`,执行前按方言换 `?`→`%s`(pymysql)。
- `dbmod.get_conn(cfg, db_kind)`:`db_kind ∈ {session, knowledge, premium}` 取值库名(`db_name` / `knowledge_db_name` / `premium_db_name`)与 SQLite 文件(`sqlite_path` / `knowledge_db_path` / `premium_db_path`)。
- `dbmod.DB(conn, cfg)`:统一适配器,让 store 用同一种 `_conn.execute(sql, params)` 同时支持两种方言;`row["col"]` 两种都行(sqlite3.Row / pymysql DictCursor)。
- `dbmod.columns(db, table)`:取列集用于增量加列(SQLite 走 `PRAGMA table_info`,MySQL 走 `information_schema.COLUMNS`)。

每个 store 的构造签名统一为 `__init__(self, path=None, cfg=None)`:`cfg` 且有 `db_host` → MySQL;否则 SQLite(路径 = 传入 `path` 或从 `cfg` 推导)。容器与脚本一律传 `cfg`,单测仍传 `path`(SQLite)。

## 逐 store 的方言坑

**保留字(dict/列名)**:MySQL 里 `key` / `type` / `scope` 是保留字,建表与查询都要反引号 `` `key` ``。SQLite 也接受反引号,所以这些列在两边都用反引号最省心。例子:`memory_entries` 的 `WHERE \`type\`=? AND \`key\`=? AND \`scope\`=?`;`products` 的 `WHERE \`key\`=? OR name=?`(PremiumStore 用 `self._q(n)` 按方言给列名加反引号)。

**UPSERT 语法**:SQLite 用 `INSERT … ON CONFLICT(cols) DO UPDATE SET col=excluded.col`;MySQL 用 `INSERT … ON DUPLICATE KEY UPDATE col=VALUES(col)`。且 MySQL 不需要指定冲突列(任意唯一键触发)。两个 store 的 `upsert_*` 都按 `_is_mysql` 分支。

**自增主键**:SQLite `INTEGER PRIMARY KEY AUTOINCREMENT` ↔ MySQL `INTEGER PRIMARY KEY AUTO_INCREMENT`(也不支持 `executescript`,MySQL 建表要逐条 + 索引容错 1061 重复)。

**索引长度**:MySQL utf8mb4 单索引前缀上限 3072 字节。`premium_rates` 的唯一键 `(product_key, item_key, dims, age_min, age_max)` 原本 `dims TEXT` 根本不能进唯一索引;改成 `product_key VARCHAR(64)` + `item_key VARCHAR(64)` + `dims VARCHAR(300)`,索引长度 ≈1712 字节才够。

**TEXT 主键/索引**:MySQL 不允许 `TEXT` 作主键或进普通索引(需前缀长度)。`chunks.chunk_id`、`documents.doc_id` 等改 `VARCHAR(191)`;大文本(`content`)用 `LONGTEXT`,只存不进索引。

**JSON 函数**:事件 `payload` 是 JSON 字符串。SQLite:`json_array_length(json_extract(payload,'$.x'))`;MySQL:`JSON_LENGTH(json_extract(payload,'$.x'))`。`/api/metrics` 用 `jlen()` 按方言切换;审计/观测读事件是走 `store.read()` 在 Python 侧解析 payload,天然与方言无关。

**`SELECT ?>=col` 这类占位符比较**:SQLite 与 MySQL 都支持(`?` 由 translate 换成 `%s` 后即 `%s>=col`),param 是字面量比较,可行。

**PRAGMA / executescript**:仅 SQLite。MySQL 分支单独建 DDL,不碰 `PRAGMA`;`sqlite3.Row` 的 `executescript` 只在 SQLite 分支用。

## 一次性迁移脚本 `scripts/migrate_to_mysql.py`

把存量 `data/*.db` 整表搬到 MySQL 对应库:3 组表(`meta/sessions/users/auth_tokens/events/memory_entries`、`chunks/documents/doc_structure`、`products/premium_rates`)。要点:

- 先构造 `SessionStore(cfg)/KnowledgeStore(cfg)/PremiumStore(cfg)` 让目标 schema 幂等建好(它们各自跑 DDL)。
- `INSERT … ON DUPLICATE KEY UPDATE` 批量写(可重跑);目标表已非空默认拒绝,`--force` 清空重迁,`--dry-run` 只统计。
- `events` 有几十万行,不能全量载入内存:分批 `LIMIT/BATCH OFFSET` + executemany。

实测(dry-run,源 SQLite 数据):session `sessions=120, users=2, events=494500`;knowledge `chunks=537, documents=2, doc_structure=496`;premium `products=2, premium_rates=6516`。

## 测试

- `tests/test_db.py`:dialect / 占位符 / translate / db_name / SQLite 连接。
- `tests/test_session_mysql.py` + `tests/test_mysql_stores.py`:连本地 MySQL(127.0.0.1 root/空密码)做各 store 往返;连不上则 `SkipTest`。**注意 MySQL `events.seq` 是全局自增,`DELETE` 不复位,断言别写死 `seq==1`**(已改为 `>=1` 且与读回一致)。
- 通用套件走 SQLite(单测不依赖外部 MySQL),全量 **297 项全绿**。

## 关键教训

1. **"能跑 SQLite ≠ 能跑 MySQL"**:差异集中在保留字、UPSERT、自增、索引长度、JSON、PRAGMA 六处,任何一处漏了都在生产才炸。
2. **反引号在 SQLite 也合法**:对保留字列统一用反引号,免去两套 SQL。
3. **索引/主键列长必须显式控制**:TEXT 不能当主键、utf8mb4 索引有长度上限,别把 SQLite 的宽松带到 MySQL。
4. **迁移脚本要可重跑 + 防覆盖 + 分批**:几十万行 events 若一次性 `executemany` 直接爆内存。
