# 17 坏轮根因细分:漏召/排序截断 vs 模型没用

> 对应决策:DECISIONS **D84**;代码:`app/observability/metrics.py`(`classify_retrieval_failure` + answer_not_cited hint)、`scripts/eval_citation.py`(gold 门控分类)、`tests/test_retrieval_failure.py`。

## 这一阶段解决什么

D78 异常定位把坏轮分到主因(检索空/低置信/无引用/…),但同是"检索了却答错",根因可能完全不同:

| 情形 | 病在哪 | 修法 |
|---|---|---|
| 正确块**根本没被召回** | 召回(retriever) | 扩召回/修 query |
| 正确块**被 top_k/重排砍掉** | 排序(rerank/top_k) | 调重排/候选池 |
| 块**排进了但模型没引用** | 模型(prompt/引用) | 改 prompt/输出约束 |

三类修法完全不同,混在一起只能瞎试。

## 关键约束:哪两类能自动判

- **model_not_used / gold_miss 可以确定性判定**——只要知道"正确答案该召回哪些块"(gold)。
- 纯靠四段分值(D79)**判不了漏召**:分值只说明"进了候选的块排序如何",不能说明"正确的块压根没进来"。

所以:
- **离线(eval 线)**:评估集用例加可选 `expected.relevant_chunk_ids`(黄金标注)→ `classify_retrieval_failure(retrieved, cited, gold)`:
  - `gold ∩ retrieved` 空 → `gold_miss`(漏召或被截断,离线快照分不清两者,都指向"召回/候选"侧);
  - gold 进了 retrieved 却没被 cited → `model_not_used`(指向 prompt);
  - gold 被引用 → 闭环正常。
- **生产在线**:不猜漏召/排序错;只把已有 `answer_not_cited` 的 hint 用 D79 分数补强("检索 N 次(最高分 X)但回答 0 引用 → 更像模型没用检索")。

## 接线

- `eval_agent.run_case` 已把每轮 `retrieval_chunk_ids`(最终返回集合)带进报告;
- `eval_citation.check_one` 读到 `expected.relevant_chunk_ids` 时跑分类器,`gold_miss/model_not_used` 记 `retrieval_failure` 且 ok=False;
- 没有 gold 的用例行为不变(不臆测)。

## 边界

- "被 top_k 砍掉(在候选但不在最终返回)"需要**候选快照**才能和漏召分开——events 只存最终返回;离线用 gold 近似,gold 块没进最终返回一律归 gold_miss(召回/候选侧),若要细分需另存候选 id 快照(量级大,收益仅离线,暂不做)。
- 这是确定性纯函数,零 LLM 依赖,合成测试即可覆盖,不必跑真评估。
