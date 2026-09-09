# 如何新增一个业务(挂在 agent-loop 核心上)

agent-loop 核心(app/loop/agent_loop.py)与业务无关:它只负责 ReAct 循环 + 块组装 + 原生工具回喂 + 取消。
一个"业务"= system 提示词 + 工具表 + 回答展现。**核心不动,新增业务 = 新写一个 bundle。**

> 铁律(AGENTS.md):SQLite=事实源 · 易失层可清空 · 回答可追溯 · 上限集中 config · 事件只 INSERT。
> 事件类型必须先注册(app/session/events.py),否则 fail-closed。

## 业务层文件结构:app/businesses/<name>.py

每个业务导出一个 `bundle(...)`。以保险业务的 app/businesses/insurance.py 为模板:

| 契约字段 | 意义 | 例子(保险) |
|---|---|---|
| `system` | 业务 system 提示词(工具如何使用、诚实规则、回答格式) | 保险 SYSTEM(先叙述再调工具 · 可读文本+[idx] · 诚实优先) |
| `tools` | 工具表 `{name: {"schema": openai工具schema, "handler": fn(args)->{"content","reference"}}}` | `search_knowledge`(检索条款) |
| `present_answer` | `(answer_text, references) -> (blocks, citations)`,决定"展现形式" | 把回答里的 [idx] 映射回条款原文 → 结构化 blocks + 溯源 citations |
| `force_answer` | `(references) -> (blocks, citations)`,检索达上限强制结束时的诚实兜底 | 说明"未能获得完整清单,以条款原文为准" |
| `cfg` | 业务所需上限(集中 config,不散落硬编码) | `max_retrieve_per_turn` 等 |

### handler 返回契约

`handler(args) -> {"content": <喂给 LLM 的文本 str>, "reference": <业务层用的原始数据>}`

- `content`:模型在下游步骤能看到的文本(例如格式化后的检索片段)。
- `reference`:业务层用来溯源/呈现的原始对象。若 `reference` 是一个**类 chunk 字典的列表**,核心会自动透出 `retrieval` 事件(前端溯源 sources 用);非列表则不产生该事件。
- 工具失败返回 `{"content": 错误文本, "reference": None}` 即可,核心会记 `tool_result.ok=False`。

### 回答展现(present_answer)是业务层的责任

核心产出的是**裸文本回答**(`answer_text`)+ 本 turn 收集的 `references`。把它变成"客户看到的形式"是业务层的事:
- 保险:解析 `[idx]` → 绑定 `chunk_id`(溯源),返回 `blocks`(可读段落)+ `citations`(引用角标)。
- 某个只返回纯文本、无溯源的业务:让 `present_answer` 直接返回 `[{"t":"p","text":answer_text}]` 与空 `citations` 即可——**核心与前端都不用动**。

## 接线(3 处)

1. **app/api/services/container.py**:加一个缓存 getter,构建并缓存该业务 bundle。
   ```python
   @lru_cache(maxsize=1)
   def get_<name>_bundle() -> dict:
       from app.businesses.<name> import bundle
       return bundle(get_embedder(), get_qstore(), get_cfg())
   ```
2. **app/api/routers/prompt.py**:把 `get_insurance_bundle()` 换成 `get_<name>_bundle()`,传给 `agent_service.run_prompt(...)`。
   ```
   agent_service.run_prompt(container.get_store(), container.get_llm(), container.get_<name>_bundle(), sid, body.text)
   ```
3. **app/api/services/agent_service.py**:`run_prompt` 已通用(注入 `bundle["system"]/["tools"]/["present_answer"]/["force_answer"]`),无需改。

> 注意:核心 `run_prompt` 的 emit 落库(append-only)+ 逐个 yield 事件(含 assistant_chunk)给 SSE 推流;
> 所以业务层无需自己发事件,也不要在 events.py 之外定义新事件语义。

## 测试

写一个 fake-LLM 测试(不依赖真实 API,测试优先回放):
- 构造 `AgentLoop(fake_llm, bundle["system"], bundle["tools"], bundle["present_answer"], bundle["cfg"], emit=store_emit, force_answer=bundle["force_answer"])`,走 `list(loop.turn(sid, text))`。
- `emit=store_emit` 落库到一次性的 `SessionStore`,断言 store.read(sid) 的 turn/usage/turn_end/citations。
- 用真实 `bundle["present_answer"]` 验证本业务的展现(如保险的 [idx] 溯源)。
- 改业务 prompt/工具 schema/条款,必须跑回放测试。

## 关键不变式

- 核心与业务互相替换:同一核心内核,换 bundle 即换业务,呈现形式由业务层决定。
- 事件类型、config 上限、事件只 INSERT 等约束都不因新增业务而改变。
