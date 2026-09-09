# 16 耗时归因:检索四段计时 + 工具执行耗时

> 对应决策:DECISIONS **D83**;代码:`app/retrieval/search_tool.py`(四段计时)、`app/businesses/insurance.py`(handler tool_meta)、`app/loop/agent_loop.py`(工具计时 + 5 元 `_run_tool`)、`app/session/events.py`(tool_result.elapsed_ms / retrieval.timings 可选键)、`web/src/App.tsx`(工具卡/轮头耗时)、`tests/test_tool_timing.py`。

## 这一阶段解决什么

M0 给了"第几轮";M1 回答"**慢在哪**"。此前观测只有 turn 总耗时 + step 耗时:一轮 20 秒,是检索慢、还是某次 LLM 慢、还是哪个工具拖的?只能猜。

## 两个埋点位置

1. **检索内部四段**:`search_knowledge` 是一条直线:embed → 稠密 → (BM25+融合) → rerank。给它一个可选 `timings` dict 出参,就地 `time.perf_counter` 记 `embed_ms/dense_ms/bm25_ms/rerank_ms`。**关键取舍:耗时不进模型 content**(D6 检索上下文保持干净),只经 handler 的 `tool_meta` 走事件。
2. **工具执行耗时**:在 agent_loop 里用一个 `_run_timed` 包住 `_run_tool` 计时。**只给"真实执行"计时**:审批等待、未批准、达上限未执行这些分支不产生误导性耗时(tool_ms=0)。

## 数据怎么从 handler 流到事件

```
search_knowledge(..., timings={})   → timings 填四段耗时
handler 返回 {content, reference, tool_meta:{retrieval_timings_ms}}
_run_tool 把 dict 返回的 tool_meta 作为第 5 个返回值带出(返回值从 4 元 → 5 元)
loop:tool_result 事件 + elapsed_ms;retrieval 事件 + timings
events 校验器:tool_result.elapsed_ms / retrieval.timings 均为可选键(旧事件不变)
```

为此 `_run_tool` 从 4 元组改成 5 元组(…, meta)——所有返回分支都要补 `{}`,**任何外部 4 元解包会直接崩**(教训:`tests/test_retrieval_fallback.py` 两处按 4 元解包,改 arity 时被全量测试当场抓出来)。

## 前端"看得见"

- 轨迹工具卡:✓ 旁边显示执行时长;检索工具卡下面一排"嵌入/稠密/BM25/重排"分段耗时标签。
- 每轮 header:显示 `工具 X · LLM≈Y`。LLM 时长没有独立埋点——因为流式 LLM 与工具在同一 step 内交替,**用"步总耗时 − 工具耗时"近似**,够回答"是工具拖的还是模型慢"。

## 边界

- rerank 只在候选 >1 时跑 → `rerank_ms` 只在此时记录(测试里 1 个候选就不会有 rerank_ms)。
- bm25 只在 `hybrid_weight>0` 时记;纯稠密不记(如实反映走了哪条路径)。
