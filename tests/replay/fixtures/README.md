# 回放样本(replay fixtures)

> 改 prompt/检索参数/工具 schema 后的零成本回归:ReplayLLM 重放录好的 LLM 响应,
> 请求不一致即 AssertionError —— 这就是回归信号(说明你的改动改变了进入 LLM 的内容)。

## 录制 / 回放

```bash
# 录制(真实 LLM + 真实检索,一次几毛钱):
python scripts/record_replay_fixture.py

# 回放(零 API 成本):
llm = ReplayLLM(records)                    # tests/replay/replayer.py
for ev in run_prompt(store, llm, bundle, sid, "尊享e生2025的等待期是多少天?"):
    ...
```

## 文件清单

| 文件 | 内容 | 录制时间 | 回归保护范围 |
|---|---|---|---|
| `kb_qa_basic.jsonl` | 1 条知识问答(4 次 LLM 调用:检索决策+生成) | 2026-09-07 | SYSTEM prompt / 检索 top_k / 工具 schema / 事件序列 / 引用解析 |

## 对拍要点

- 回放后核对事件序列(`turn_start → retrieval ×N → assistant_message → turn_end`)与引用 chunk_id 列表
- 若改了 SYSTEM prompt → messages 不一致 → ReplayLLM 抛错(**预期**,此时需重录 fixture 再验证生成质量用 eval_run)
- fixture 依赖知识库内容(chunk_id 引用);知识全量重建后需重录
