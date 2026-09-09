# 04 阶段3:知识摄取与检索(三条管道 + Qdrant + 引用)

> 本阶段把条款/产品/FAQ 三种材料摄取进 Qdrant,形成可检索的知识库,并让回答引用能定位到 chunk 原文。

## 这一阶段解决什么问题
- 销售条款/产品数据是散落的 PDF/Excel,客服答不上具体数字;需要结构化检索。
- 回答要有出处;不看原文的"答"不可信,尤其条款版本敏感。
- 不同材料形态(条款/结构化数据/FAQ)要不同处理,FAQ 须人工审核才入检索。

## 对应 dsh 源码
- dsh 无内置 RAG;参照 mcp-client 接外部服务的思路,自建摄取 → 嵌入 → Qdrant 检索链路。

## 设计要点
1. **三条摄取管道**(D11):条款 PDF/Word(结构分块)、结构化数据(直查或转文本块)、FAQ(人工审核入库)
2. **chunk_id 稳定 + 版本过滤**:chunk_id = doc_id:version:section:index,旧版本保留在 SQLite(引用绑定版本)
3. **混合检索**:character 切块 1000/200 → bge-large-zh-v1.5(本地 CPU,1024 维)→ Qdrant collection insurance_knowledge(dense Cosine)→ top_k 20 → 可选外部重排(SiliconFlow)取前 3
4. **存储隔离**(D16):集合 insurance_knowledge、Redis db 2、SQLite data/agent.db,与其它项目不冲突

## Python 实现
- `app/retrieval/chunker.py`:character 切块 1000/200
- `app/retrieval/embedder.py`:bge-large-zh-v1.5 本地嵌入
- `app/retrieval/qdrant_store.py`:dense Cosine + UUID5(chunk_id) 入 Qdrant
- `app/retrieval/reranker.py`:外部(SiliconFlow)重排(选配)
- `app/retrieval/search_tool.py`:search_knowledge → RetrievalChunk[]

## 验收测试
- 摄取 A 条款全文 → 56 块 → 嵌入 → 入 Qdrant
- 查询"责任免除/免赔额/续保条件"召回正确(score 0.59-0.68)
- chunker 单测 3 项绿

## 手动测试
- 知识管理页(待建):上传条款 → 看 chunk → 检索试玩
- 真聊天问答带角标,点角标定位 chunk 原文(含历史版本)

## 你学到了什么
- **三类材料三种管道**:不硬套一个流程;FAQ 必须人工审核
- **chunk_id 带版本**:引用可追溯,条款更新不失效
- **存储隔离**:collection/db/文件名与其它项目隔离,避免数据污染

## 踩坑记录
- rag_env 解释器需含 sentence_transformers/qdrant_client;基础 Python311 的 torch 损坏

## 产品类别(product_category,D41)
- chunk 除 doc_type(文档类型)外,再加 product_category(保险类别)区分医疗险/重疾险/意外险等。
- 归类逻辑集中在 app/retrieval/categories.py(classify 按 doc_id 关键词);KnowledgeStore 加列+迁移+幂等重算;ingest_kb 按 doc_id 判定(--category 可覆盖);search_tool.to_chunk 透出;前端源标题显示类别。
- 要点:doc_type 不等于 product_category(前者文档形态,后者保险品类);类别随 doc_id 判定,规则变更在打开表时重算。
- 注意:live 检索需 Qdrant 重建才带类别;localhost:6333 需有 Qdrant 服务(环境依赖)。

- 嵌入模型路径 bge-large-zh-v1.5 需配置在 .env;Qdrant 需先启动(默认 http://localhost:6333)
