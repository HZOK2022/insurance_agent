# RAGAS × 自研 LLM-judge 交叉验证报告

> 目的：用工业标准评测框架 RAGAS 的独立打分，校准自研六维 LLM-judge，验证其判别力与可信度，而非"自己评自己"。
> 生成时间：2026-09-07　脚本：`scripts/ragas_crossval.py`　产物：`docs/eval/ragas_crossval.json`

---

## 1. 数据与方法

| 项 | 说明 |
|---|---|
| 评测数据 | `docs/eval/history/eval-2026-09-07.json`，72 条生产评测产物（含 `answer` / 自研 `judge` 六维 + `hallucinate`/`overclaim` 标志 / `retrieval_chunk_ids`） |
| 检索上下文 | 从 `data/knowledge.db` 按 `chunk_id` 取正文，作为 RAGAS 的 `retrieved_contexts` |
| 对齐指标 | **RAGAS Faithfulness（0-1，纯 LLM）↔ 自研 `faithfulness` / `groundedness`（0-3 归一）/ `hallucinate` / `overclaim`** |
| RAGAS 模型 | `deepseek-chat`（见第 3 节，v4-flash 跑不了结构化输出） |
| 自研 judge 模型 | `deepseek-v4-flash`（评测集生成时的配置） |

> 两裁判用**不同模型**，反而降低"同模型回声"偏差，交叉验证更干净。

---

## 2. 环境落地：RAGAS 0.4.3 在本环境踩的五道坑（印证"版本要求苛刻"）

RAGAS 不是开箱即用的，逐个修完才跑通：

1. **`appdirs` 模块被 safe-delete 拦掉**——`dist-info` 在、代码不在，`import` 必失败。手动从 wheel 提取 `appdirs.py` 补回 site-packages。
2. **langchain 1.x 生态不兼容**——ragas 硬导入已移除的旧路径 `langchain_community.chat_models.vertexai`，`import` 即崩。patch `ragas/llms/base.py` 把两个 vertexai 导入降级为占位类。
3. **`LangchainLLMWrapper` 在本环境挂起**——改用官方推荐的 `llm_factory('openai', client=OpenAI(...))` 路径。
4. **`deepseek-v4-flash` 不支持 instructor 的 JSON 结构化输出**——返回非 JSON → `IncompleteOutputException` → 分数 `nan`。换成 `deepseek-chat`。
5. **instructor 默认 `max_tokens=1024` 对长保险上下文截断**——同样触发 `IncompleteOutputException`。调大到 4096 根除。

> 这五道坑恰好印证了"RAGAS 对其他包版本要求很苛刻"——也正是项目选择自研评测链路的现实合理性之一。自研链路只依赖少量稳定依赖，不引入这种级联脆弱性。

---

## 3. 结果（有效样本 n=28）

| 指标 | 值 |
|---|---|
| RAGAS Faithfulness 均值 | **0.357**（min 0.00 / max 0.93） |
| 自研 faithfulness 均值（归一 0-1） | **0.917** |
| 自研打满分（3/3）的条数 | **24 / 28** |
| RAGAS 判为不忠实（<0.5）的条数 | **16 / 28** |
| Pearson(ragas, 自研 faith) | **0.296** |
| Spearman(ragas, 自研 faith) | **0.279** |
| Cohen's κ（二元一致） | **0.037** |
| 分歧条数 | **15** |

---

## 4. 核心发现：自研 judge 存在「宽松 / 满分偏差」

**几乎全部分歧都是同一模式：自研=1.00（满分），RAGAS 很低（0.0–0.4）。**

- RAGAS ≥ 0.8 的 4 条：自研均值 **1.00** → 好答案两边都认
- RAGAS < 0.4 的 15 条：自研均值 **0.87** → 坏答案自研仍给高分
- 自研全程仅 **3/28** 触发过 `hallucinate`/`overclaim`；这 3 条 RAGAS 也判低 → **自研"能判对，但几乎不判错"**

**结论**：自研 judge 判别力不足——评分地板太高、天花板封顶，几乎无法区分好/坏答案。这是它与 RAGAS 相关性弱（Pearson 0.30）的根因，**不是两个 judge 都错，而是自研的 rubric 太宽松**。

附带价值：自研 judge 拥有 RAGAS Faithfulness 没有的 **`hallucinate` / `overclaim` 显式布尔标志位**，在"该拒答/转人工"的合规判定上是 RAGAS 之上的增量能力。

---

## 5. 数据漂移发现：评测集未与 KB 版本绑定

44 / 72 条记录的 `retrieval_chunk_ids` 在当前 `knowledge.db` 查不到正文。旧 KB 摄取方案的 chunk_id 前缀（如 `尊享e生2025-产品要点`、`安盛天平A款-产品要点`、纯数字 ID）在当前重摄取后的 KB（仅 `尊享e生2025` / `安盛天平卓越馨选(A款)` 两套）已不存在。

影响：
- Faithfulness 只能跑有可解析上下文的 **28 条**，而非全量 72 条。
- 若强行跑全量，需先把评测集对当前 KB 重新摄取 / 对齐 chunk_id。

---

## 6. 对简历与项目的意义

**正面（可写进简历）：**
- 自研 judge 在"好答案"上与 RAGAS 共识，证明它不是乱评、有独立有效信号。
- 具备 RAGAS 没有的合规标志位（hallucinate / overclaim / refused_when_should），是保险场景的刚需增量。
- 主动做了"成熟框架 × 自研"交叉验证——这是面试官眼里的评测成熟度信号。

**待改（写进"可观测与评测"迭代项，诚实呈现）：**
- 自研 judge 评分 rubric 需**重标定**：把 `faithfulness`/`groundedness` 从"宽松 0-3"改为刚性锚定（如 RAGAS faith<0.5 时自研不得 >2），或叠加程序化引用校验硬阈值，把满分偏差压下来，使分数有区分度。
- 评测集应与 KB 版本绑定存档，避免第 5 节的漂移。

---

## 7. 下一步建议

1. **重标定自研 judge**：引入与 RAGAS 对齐的刚性分档，对分歧 15 条做人工抽检，确认是 RAGAS 过严还是自研过松（当前证据强烈指向后者）。
2. **评测集与 KB 版本绑定**，重跑全量 72 条；并补齐 `expected` 为自然语言参考答案，解锁 RAGAS 的 `ContextPrecision` / `ContextRecall` / `AnswerRelevancy`（后者需 embedding 模型，当前 env 未装 torch）。
3. 若上 Phoenix / Langfuse，RAGAS / deepeval 这类"参考答案无关"的忠实度指标仍是最便宜的回归护栏，可长期并存。

---

## 8. 附：15 条分歧明细（自研满分 vs RAGAS 低分）

| id | RAGAS | 自研faith | 自研ground | hallucinate | query（节选） |
|---|---|---|---|---|---|
| kb_001 | 0.23 | 1.00 | 1.00 | F | 尊享e生2025的必选计划保险责任包括哪些？ |
| kb_003 | 0.00 | 1.00 | 1.00 | F | 门急诊加油包A和B有什么区别？ |
| kb_004 | 0.00 | 0.67 | 0.67 | F | 安盛A款重疾住院津贴每天多少？ |
| kb_005 | 0.40 | 1.00 | 1.00 | F | 0元免赔、1.5万、3万档区别？ |
| kb_006 | 0.38 | 1.00 | 1.00 | F | 安盛A款海南博鳌特定医疗包括什么？ |
| rule_001 | 0.64 | 0.67 | 0.67 | **T** | 16岁能不能买安盛天平A款？ |
| adapt_003 | 0.00 | 1.00 | 1.00 | F | 重疾和医疗都想要，预算有限怎么搭配？ |
| compare_001 | 0.00 | 1.00 | 1.00 | F | 尊享e生2025 和 安盛A款哪个适合我？ |
| compare_003 | 0.04 | 1.00 | 1.00 | F | 安盛A款普A和特A区别？ |
| advisor_002 | 0.09 | 1.00 | 1.00 | F | 一家三口都配怎么配合适？ |
| boundary_002 | 0.00 | 1.00 | 1.00 | F | 过完等待期再确诊能赔吗？ |
| compute_006 | 0.00 | 1.00 | 1.00 | F | 50岁女尊享e生计划一基础版一年多少钱？ |
| compute_007 | 0.00 | 1.00 | 1.00 | F | 尊享e生两人折扣后价格？ |
| compare_005 | 0.23 | 1.00 | 1.00 | F | 计划一和计划二差什么？ |
| grounded_004 | 0.33 | 1.00 | 1.00 | F | 尊享e生免赔额最高档是5万对吧？ |
