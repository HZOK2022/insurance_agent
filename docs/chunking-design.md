# 结构化分块方案(docs/chunking-design.md)

> 目标:把当前 **character 硬切** 升级为 **结构感知分块**——按**语义单元**切 + **层级前缀** + **超长降级** + **无结构兜底**。对条款/政策/表格/markdown 有效,纯文本兜底。**MinerU 可选,不依赖也能跑**。

## 一、整体管线
```
原始文件(doc_type)
  → ① 预处理(reader):提取 + 清洗 + 保留结构线索(标题/编号行/表头)
  → ② 结构识别:按"可配置 pattern"切成【语义单元】(带层级路径)
  → ③ 分块:单元超长 → 降级递归;识别不到 → 兜底通用切
  → ④ metadata:section/title/doc_id/version/doc_type
  → ⑤ 索引(向量化入 Qdrant + BM25)
```

## 二、解析层(reader,现有)
| doc_type | 解析 | 保留的结构线索 |
|---|---|---|
| pdf | pdfplumber | 文本(行/段落),靠"第X条"编号行 |
| docx | python-docx | **段落 + 样式 + 表格**(最可靠) |
| xlsx | openpyxl | 表头行 + 数据行 |
| md | 原文 | 保留 `#` 标题 |
| txt | 原文 | 无 |

> **MinerU 以后可选接入**(布局感知,出 `#` 层级)替换 pdf 解析 → 结构线索更强;现在不依赖。

## 三、分块层(chunker)
`chunk_documents(docs, chunk_size=1000, overlap=200, text_splitter="structured")` 按 `doc_type` 路由:

| doc_type | 策略 |
|---|---|
| policy_pdf / policy_docx | **条款纲目结构**(编号 regex:第X部分/章/节/条、(一)、1.)→ 语义单元 + 前缀 |
| markdown | **md 标题层级**(#)→ 单元 + 前缀 |
| rate_table | **按行切**(每数据行带表名+列头前缀) |
| text / 其它 | **character 兜底** |

**组件**:
- **结构识别** `_split_into_units(text, pattern_key)`:按"结构标题行"聚合为【语义单元】,维护**层级栈**记录`path`;pattern 可配置(`_STRUCT_PATTERNS`:policy/md/generic)。
- **防碎片(D58)** `_coalesce_units(units, chunk_size)`:① **只有"确有子单元"的单行容器标题不立块**(标题文本已在子块前缀里);**单行叶子条目(如 `(1)门诊肾透析费;`)必须保留**(曾因"单行即丢"误删内容,已回修——凭"有无子单元"判定);② **同级(同父)连续编号子项合并**到 ≤`chunk_size`,合并块 `section` 取**父路径**(子标题留在 content 行内,避免"标题串长");md 标题层级文档不做兄弟合并。实测尊享e生2025:MinerU 腿 626 → **150 块**(cap=1000)/ **185 块**(cap=512),≤60 字半空块 325 → 2。
- **token 预算(D59)** `chunk_structured(..., max_tokens=430)`:合并/超长降级按"整串含前缀 估算 token ≤ 预算"(embedding_tokens:tiktoken cl100k 优先,兜底启发式×1.2);ingester 不再硬编码 512 字符,改读 config `CHUNK_MAX_TOKENS`(默认 430 = bge-512 留余量)。实测:261 块、**>512 token 块 = 0**(原字符 512 模式 25 块超线)。换 bge-m3(8192)只需调大 CHUNK_MAX_TOKENS。
- **标题 vs 内容判别(D58 续)** `is_heading_like(line)`:命中编号/标记的行只有**短(≤64 字)且不以句末/分句标点结尾且不含逗号/分号**才算真标题;否则为正文。**通用启发式,非文档特定**。
- **通用结构识别(D62)** `_detect_pattern_key(text)`:先剥 md # 再判条款编号→ `policy`;否则原文有 `#` → `md`;否则通用编号(`1.`/`1.1`(层级=数字段数)/`(1)`/`一、`/`（一）`/`A.`)→ `generic`;都无 → `none`(回退字符切)。`chunk_structured` 自动探测;`chunk_documents` 除 rate_table 外一律先结构切;`probe.build_outline` 同源(同一探测+patterns+判别)。**书签仍是最通用结构源**(任何作者结构 PDF 可读,不依赖编号习惯)。
- **层级前缀** `_prefix`:"第一部分 总则 > 第六条 保险责任",写进 `meta.section/title`。
- **超长降级** `_recursive_cut(body, prefix, chunk_size)`:单元超 `chunk_size` → 按段落→句子递归,每段保留前缀。
- **无结构兜底**:识别不到 → `chunk_text`(character 切),绝不整篇 1 chunk 或乱切。

```python
# 结构 pattern(可配置/扩展)
_STRUCT_PATTERNS = {
  "policy": [(r"^第[一二三四五六七八九十百千万零]+部分",1),(r"^第...章",2),(r"^第...节",3),
             (r"^第...条",4),(r"^[一二三四五六七八九十]+[、．.]",5),(r"^\d+[．.、]",6),(r"^[（(][一二三四五六七八九十]+[）)]",7)],
  "md":     [(r"^(#{1,6})\s+", Lambda#length)],
  "generic":[(r"^\d+(\.\d+)*[．.]\s?",1),(r"^[A-Za-z]+\d*[．.]\s?",2)],
}
```

## 四、metadata
`chunk = {content, meta:{chunk_id, doc_id, version, doc_type, section(层级路径), title}}`
- `section` 供检索/引用定位"第X条";`page`(pdf,可选)供跳转。

## 五、配置(集中 config)
`text_splitter=structured|character`、`chunk_size=1000`、`chunk_overlap=200`、`max_chunk_tokens`(超长降级阈值)。

## 六、与 MinerU 的关系(已实测修正,D57)
- **现在**:pdfplumber / MarkItDown / MinerU 三路并列可选(read_text backend;CLI ingest_kb --parser;PARSER_BACKEND),分块器按 **编号 regex**切(部分>条>子项>数字项),不依赖 MinerU。
- **实测结论**:MinerU full.md 标题被拍平(1×# 主标题 + 298×##,官方 #3135/#3203 说明标题层级预测不可靠、demo 多级标题是 LLM 后处理)→ **不能依赖它的 md 层级**;真层级在 *_content_list.json/middle.json(已透传保留,未接)。
- **验证工具**:scripts/compare_parsers.py 同文件三路跑(对比表 + 每路原文 + summary.tsv);app/retrieval/ingest/probe.py 统计结构线索与“脱#→编号 regex”切块后 section 填充。
- **未接 MinerU 时**:要把它的 md 先“脱 # 前缀”再喂编号 regex(否则 `## 第一条` 配不到 `^第...条`,层级丢失——实测未脱#平均深度1.5/39超长块 vs 脱#后 2.69/7块)。

## 七、分阶段
- **P1**：chunker 结构化（`app/retrieval/chunker.py` 已重写）+ 测试（`tests/test_chunker_structured.py` 已写，未跑）。
- **P2**：接 MinerU（可选）→ pdf 走 MinerU。
- **P3**：ingest 管线接 chunker（`ingester` 已 `use chunk_documents`，自动生效）。

## 八、核心一句话
> **"结构识别(可配置 pattern) + 语义单元 + 层级前缀 + 超长降级 + 无结构兜底 + 按 doc_type 路由"，不依赖 MinerU 也能跑；MinerU 以后接上是"更稳的层级来源"，不是必需。**
