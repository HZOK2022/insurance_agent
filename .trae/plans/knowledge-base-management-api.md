# 实现方案:知识库管理API(管理员增删改查)

## Context

当前项目现状:
- `KnowledgeStore`(knowledge.db, SQLite)只有 `upsert_chunks`/`all_chunks`/`get_chunk`/`count`，**没有按文档列表/分页/删除**
- 摄取全靠 CLI(`ingest_kb.py`/`rebuild_qdrant.py`)，**没有 Web API**
- `users` 表没有 `role` 字段，**无法区分管理员/普通客服**
- 按文档删除后，**Qdrant/BM25 需要同步删除/重建**，否则检索会不一致

需求:新增**管理员独占的知识库管理后端 API**，支持:
- 文档列表分页
- 文档内 chunks 分页
- 上传/摄取新文档
- 删除整个文档
- 全量重建 Qdrant 索引(遵守黄金法则)

黄金法则(不可违反):
- `SQLite = 事实源`;`Qdrant = 可重建的派生向量索引`;`BM25 = 内存派生稀疏索引`
- 删文档允许，因为它是**内容管理操作**，不是修改 `events` 审计历史

## 实现方案与步骤(分期)

### 阶段 A:后端 API + 权限 + 存储扩展(纯后端可测)

#### 步骤 1:users 表加 role 字段(增量迁移)
**修改:** [`app/session/store.py`](file:///d:/LLM/insurance-agent/app/session/store.py)
1. 在 `_ddl()` 的 `CREATE TABLE users` 中加 `role TEXT NOT NULL DEFAULT 'agent'`
2. 在 `_ensure_schema()` 中增量迁移:检查 `role` 列不存在则 `ALTER TABLE users ADD COLUMN`
3. `create_user()` 接受可选 `role` 参数(默认 `agent`)
4. `get_user()` 返回结果包含 `role` 字段

**修改:** [`app/api/services/auth_service.py`](file:///d:/LLM/insurance-agent/app/api/services/auth_service.py)
- `seed_admin_if_empty()`:播种默认管理员时，指定 `role='admin'`

#### 步骤 2:扩展 KnowledgeStore(list/delete)
**修改:** [`app/retrieval/knowledge_store.py`](file:///d:/LLM/insurance-agent/app/retrieval/knowledge_store.py)

新增方法:
```python
def list_documents(self, page: int = 1, page_size: int = 50) -> dict:
    """按 doc_id 聚合，分页返回文档列表: {total, page, page_size, items: [{doc_id, doc_type, product_category, chunk_count, last_updated}]}"""

def list_chunks(self, doc_id: str, page: int = 1, page_size: int = 100) -> dict:
    """返回指定文档的 chunks 分页列表"""

def delete_document(self, doc_id: str) -> int:
    """删除该文档所有 chunks，返回删除条数"""
```

#### 步骤 3:扩展 QdrantStore(按 doc_id 删除)
**修改:** [`app/retrieval/qdrant_store.py`](file:///d:/LLM/insurance-agent/app/retrieval/qdrant_store.py)

新增方法:
```python
def delete_by_doc_id(self, doc_id: str) -> int:
    """用 Qdrant filter 删除所有 payload.doc_id == doc_id 的点，返回删除条数"""
    # 使用 self._retry_call() 错误处理，和其他方法一致
```

#### 步骤 4:抽取摄取核心到可复用服务(CLI/API 共用)
**新建:**
- `app/retrieval/ingest/reader.py` → `read_text()`/`_read_docx()`/`_read_xlsx()`/`build_docs()`(从 `ingest_kb.py` 抽取)
- `app/retrieval/ingest/ingester.py` → `class Ingester` 封装摄取逻辑:
  - `__init__(kstore, qstore, embedder, cfg)`
  - `ingest_text(text, meta)` → 切块 → upsert SQLite → 嵌入 → upsert Qdrant
  - `ingest_file(file_path, doc_id, version, category)` → 读文件 → 切块 → 入库
  - `full_reindex()` → 全量重建 Qdrant 从 SQLite

**修改:**
- `app/retrieval/ingest/__init__.py` → 导出 `Ingester`/`read_text`/`build_docs`
- `scripts/ingest_kb.py` → 只保留 CLI 参数解析，复用 `Ingester`

#### 步骤 5:新增 Pydantic schemas
**新建:** [`app/api/schemas/kb.py`](file:///d:/LLM/insurance-agent/app/api/schemas/kb.py)

Schemas:
- `PaginationQuery` → 分页参数
- `DocumentItem`/`DocumentListResponse` → 文档列表
- `ChunkItem`/`ChunkListResponse` → chunks 列表
- `IngestTextRequest` → 文本摄取请求
- `DeleteDocumentResponse` → 删除响应
- `ReindexResponse` → 全量重建响应

#### 步骤 6:新增 KB 服务层
**新建:** [`app/api/services/kb_service.py`](file:///d:/LLM/insurance-agent/app/api/services/kb_service.py)

Services:
- `list_documents(kstore, page, page_size)`
- `list_chunks(kstore, doc_id, page, page_size)`
- `delete_document(kstore, qstore, doc_id)` → 先删 SQLite → 再删 Qdrant → 返回状态
- `ingest_text(ingester, text, meta)` → 入口
- `full_reindex(kstore, qstore, ingester)` → 全量重建

**修改:** [`app/api/services/container.py`](file:///d:/LLM/insurance-agent/app/api/services/container.py)
- 新增 `get_knowledge_store()` singleton (`@lru_cache(maxsize=1)`)
- 新增 `get_ingester()` 复用全局 `embedder`/`qstore`

#### 步骤 7:鉴权中间件新增 admin 检查
**修改:** [`app/main.py`](file:///d:/LLM/insurance-agent/app/main.py)
- 在 `_auth_and_ratelimit` 中，对路径 `/api/kb/*` 额外检查用户 `role == 'admin'`
- 非 admin 返回 `403 Forbidden`
- 全局 `api_token` 视为 admin(兼容现有配置)

#### 步骤 8:新增 KB 路由
**新建:** [`app/api/routers/kb.py`](file:///d:/LLM/insurance-agent/app/api/routers/kb.py)

端点(全 `/api/kb` 前缀):
- `GET /api/kb/documents` → 文档列表分页
- `GET /api/kb/documents/{doc_id}/chunks` → 文档 chunks 分页
- `POST /api/kb/ingest/text` → 摄取文本文档
- `DELETE /api/kb/documents/{doc_id}` → 删除文档
- `POST /api/kb/reindex` → 全量重建 Qdrant

**修改:** `app/main.py` → `app.include_router(kb.router)`

#### 步骤 9:BM25 刷新机制
**修改:** [`app/businesses/insurance.py`](file:///d:/LLM/insurance-agent/app/businesses/insurance.py)
- 当前 BM25 是闭包懒加载(`_get_hybrid()`)，修改为可"标记脏"
- KB 增删改后，设置脏标记 → 下次检索时自动重建 BM25 从当前 SQLite
- 暴露 `invalidate_bm25()` 给 API 服务层调用

## 一致性保证(不变式)

| 不变式 | 保证方式 |
|---|---|
| **先写 SQLite，后写 Qdrant** | 服务层顺序:SQLite 删除/写入成功 → 再操作 Qdrant |
| **Qdrant 失败不回滚 SQLite** |  golden rule 允许:Qdrant 是派生，全量 reindex 总能修复 → 返回部分成功警告 |
| **BM25 最终一致** | 任何写入后立即标记脏，下次检索懒重建 → 最多一次搜索 stale，下一次就新鲜 |
| **单写者不变** | 所有写入仍走 agent 服务进程，和之前一致 → SQLite 单写者约束维持 |
| **Qdrant 总能从 SQLite 全量重建** | `POST /reindex` 端点就是干这个的 → 任何不一致点一下就修好了 |

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| 大库全量 reindex 慢 | 接受现状，这是 golden rule 要求的，且 admin 操作罕见;以后再优化 |
| BM25 重建阻塞检索 | 懒重建在第一次搜索时做，本来就是这个模式;大库最多慢一次，以后就快了 |
| 旧引用删了点不开 | 前端 citation 点击处理 404，提示"文档已从知识库删除" |
| 非admin越权 | 中间件每层都检查 role，API 层也会拒，没问题 |

## 测试

### 单元测试(新增)
- `tests/test_knowledge_store_admin.py` → 测试 list/delete/pagination
- `tests/test_kb_api_auth.py` → 测试 401/403/admin 能访问
- `tests/test_ingester.py` → 测试抽取后的摄取逻辑

### 验证点
1. 增量迁移:已有 users 表不崩，默认 role=agent，默认 admin 播种后 role=admin ✔️
2. 删除文档:SQLite/Qdrant 同步删除，BM25 刷新后搜不到 ✔️
3. 分页:边界情况(第一页/最后一页/空库)正确 ✔️
4. 权限:非admin调用 `/api/kb` → 403 ✔️

### 集成测试(人工)
1. 启动服务，admin 登录
2. 调用 list documents → 空列表
3. ingest 一个测试文档 → 成功
4. list documents → 看到它 ✔️
5. list chunks → 看到切块 ✔️
6. delete 文档 → 成功
7. list documents → 没了 ✔️
8. full reindex → 跑完计数正确 ✔️

## 阶段 B:前端知识管理页(概要)

后续做前端时按这个来:
1. `web/src/lib/api.ts` → 加 KB 接口类型和调用函数
2. `web/src/components/KnowledgeManagement.tsx` → 新建组件:文档列表 + chunks 列表 + 上传模态框 + 删除按钮 + 重建按钮
3. `web/src/App.tsx` → 侧边栏加"知识管理"入口，仅 admin 可见
4. 跟着现有 CSS 写样式，保持一致

## 关键文件清单

| 文件 | 操作 |
|---|---|
| `app/session/store.py` | 修改(加 role 列 + 增量迁移) |
| `app/api/services/auth_service.py` | 修改(播种 admin 设 role) |
| `app/retrieval/knowledge_store.py` | 修改(加 list/delete) |
| `app/retrieval/qdrant_store.py` | 修改(加 delete_by_doc_id) |
| `app/retrieval/ingest/reader.py` | 新建(抽取文件读取) |
| `app/retrieval/ingest/ingester.py` | 新建(抽取摄取核心) |
| `app/retrieval/ingest/__init__.py` | 修改(导出) |
| `scripts/ingest_kb.py` | 修改(refactor 用 Ingester) |
| `app/api/schemas/kb.py` | 新建 |
| `app/api/services/kb_service.py` | 新建 |
| `app/api/services/container.py` | 修改(加 get_knowledge_store/get_ingester) |
| `app/main.py` | 修改(加 admin 检查 + 注册路由) |
| `app/api/routers/kb.py` | 新建 |
| `app/businesses/insurance.py` | 修改(BM25 脏标记刷新) |
| `tests/test_knowledge_store_admin.py` | 新建 |
| `tests/test_kb_api_auth.py` | 新建 |
