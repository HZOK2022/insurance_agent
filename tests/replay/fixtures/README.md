# 回放样本(replay fixtures)

> 改 prompt/条款/工具 schema 后必须回归;这是 AGENTS 铁律的一部分。
> 本目录存放 JSONL 录制:每行 = `{"messages": [...], "response": [...]}`(来自 tests/replay/Recorder)。
> replay 测试时:用 `ReplayLLM` 重放同样的 `messages`,只比对 `response`(允许微小差异),保证 agent 输出在 prompt/条款/工具微调时仍可控。

## 录制方法(供后续补录)

```python
from tests.replay.recorder import Recorder
from app.api.services.container import get_llm
llm = Recorder(get_llm())
# ... 让某段对话走 llm.chat_stream ...
llm.save("tests/replay/fixtures/<name>.jsonl")
```

## 文件清单

| 文件 | 来源 | 用法 |
|---|---|---|
| `kb_qa_basic.jsonl` | 5 条 kb 问答 | 改 retrieval chunker / 改 SYSTEM 时回归 |
| `agent_loop_step_budget.jsonl` | 3 步完成的最小 turn | 改 loop 步数/token 预算时回归 |

(具体文件由后续录制脚本补;本 README 留空档。)
