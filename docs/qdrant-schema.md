# Qdrant 知识库设计(docs/qdrant-schema.md)

只存**现行生效版本**的向量;原文与历史版本在 SQLite(事实源)。Qdrant 是派生索引,可随时全量重建。

## Collection:insurance_knowledge

| 项 | 值 |
|---|---|
| 命名向量 | `dense`(bge-large-zh-v1.5,1024 维,Cosine)+ `sparse`(BM25 类,混合检索) |
| 距离 | Cosine |
| 分片/副本 | 单机默认即可 |
| 向量维度跟随嵌入模型 | 换模型=新 collection+全量重建(见下) |

## Payload(最小化,原文放 SQLite)

```json
{
  "chunk_id": "P10086:v3.2:section4:12",   // 稳定 id:doc_id:version:section:index
  "doc_id": "P10086",
  "version": "v3.2",
  "section": "section4",
  "doc_type": "policy_document",             // policy_document|structured|faq|sales_script
  "effective_from": "2026-03-01",
  "effective_to": null,                      // null = 现行
  "source": "product/P10086/条款v3.2.pdf",
  "tenant": "internal",                      // 预留多租户
  "embedding_model": "bge-large-zh-v1.5",     // 重建依据
  "title": "责任免除"
}
```

**Payload 索引**(keyword 字段全部建索引,查询过滤用):
`doc_id` `version` `doc_type` `effective_from` `effective_to` `tenant` `embedding_model`

## Point id:UUID5(chunk_id)

用 `uuid.uuid5(NAMESPACE, chunk_id)` 生成确定性 point id ⇒ 重复摄取天然幂等(upsert 同一点),版本更新=新 chunk_id=新点。

## 查询(混合检索 + 版本过滤)

```text
条件过滤:doc_type(可选),effective_from <= today AND (effective_to IS NULL OR effective_to >= today),tenant
prefetch:dense(20)+ sparse(20) → RRF 融合 → top-k(20) → 外部重排取**前 3**
```

命中后:取 payload 的 chunk_id → 回 SQLite chunks 取 content(供 prompt 注入与引用展示)。

## 摄取(三条管道)

1. **条款文档**:PDF/Word 解析 → **character 切块**(chunk_size 1000, overlap 200;优先按章节/条款边界,保持语义完整)→ 嵌入 → upsert
2. **结构化数据**:产品参数/费率表 → 转成"自然语言描述块"或直接 SQLite 直查(不强行向量化);确需向量的走管道 1 逻辑
3. **FAQ/话术**:人工审核通过(approved)才入库;带来源与审核时间

每条摄取写 `ingested_at`,换版流程:新版本 chunk 先写入 → 检索条件改为新版本 → 旧版本在 Qdrant 标记/删除(保留 SQLite 历史)。

## 重建与备份

- 嵌入模型升级:新 collection + 按 embedding_model 重嵌(SQLite chunks 是源)
- 备份:Qdrant snapshot 可选;事实源在 SQLite,重建成本=一次摄取管道重跑
