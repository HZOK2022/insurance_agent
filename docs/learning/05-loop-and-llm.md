# 05 阶段4:Agent Loop + LLM 客户端 + 引用链路(ReAct + 流式 + 指标)

> 本阶段把「问题 → 检索 → 回答」串成 dsh 式的 ReAct 循环,并让过程(判定/检索/流式/耗时)可见、可追溯。

## 这一阶段解决什么问题
- **所有问题都走检索**:寒暄"你好"也打知识库,浪费、且不符 agent 行为。需要 LLM 先判断是否需要检索。
- **看不到过程**:回答是一次性出现,不知道检索了没、推理了没、花了多久。需要真流式 + 过程事件 + 指标。
- **不可追溯**:回答引用要能定位到 chunk 原文(含历史版本)。

## 对应 dsh 源码
- `@deepseek-ai/dsh-agent-loop`:`core/agent-loop/src/index.ts` —— turn/step 边界、工具调用、每步 llm.stream、usage 累计
- `@deepseek-ai/dsh-session-title`:标题生成的确定性 fallback(我们的 app/session/title.py 按此实现)
- 引用链路:结构化输出 `{answer, citations:[{idx, chunk_id}]}`

## 设计要点
1. **ReAct 两步**:判定步(need_search)→ 生成步
   - 判定步由 LLM 输出 need_search/query;寒暄/常识 → false 不检索;具体产品/条款问题 → true 检索
   - 生成步注入检索资料后产出最终回答
2. **真流式**:`LLMClient.chat_stream`(requests stream=True)测首token延迟(TTFT);`AgentLoop.stream_run` 是生成器,每写一条事件就 yield,SSE 逐帧推流
3. **过程事件**:`turn_start / step_start / tool_call / tool_result / assistant_chunk / assistant_message / usage / turn_end`,全部落 events(铁律:模型可见 ⟺ 已记录)
4. **指标采集**:ttft_ms / run_ms / tokens_per_second / elapsed_ms,写入 usage 与 turn_end
5. **标题生成**:首条用户消息出现时,用确定性 fallback(取前 N 词 + UTF-8 截断)生成会话标题

## Python 实现
- `app/llm/client.py`:`chat()` 非流式 + `chat_stream()` 流式(逐段 yield delta + 测 ttft)
- `app/loop/loop.py`:`AgentLoop.stream_run()` 生成器逐个 yield 事件;`run()` 向后兼容
- `app/session/events.py`:注册 `step_start/step_end` 事件类型;`_validate_usage/turn` 保留指标字段
- `app/session/title.py`:clean_title_text / truncate_title_utf8 / fallback_session_title
- `app/api/services/agent_service.py` + `routers/prompt.py`:真流式 SSE(`StreamingResponse` 逐帧)

## 验收测试
- 事件类型注册表含 turn_start/step_start/step_end/tool_call/tool_result/turn_end(fail-closed 不拒绝)
- 流式生成:stream_run 逐个 yield 全部事件(实测"你好"9 事件无 tool_call;"重疾险责任免除"含 tool_call+引用)
- 指标:usage 含 ttft_ms/run_ms/tokens_per_second;turn_end 含 elapsed_ms(已扩展 validator 验证)
- 22 项 unittest 全绿

## 手动测试
- 真聊天页发"你好":底部状态先"判断是否需要检索"→ 直接作答(无检索);过程事件实时入日志,控制台可见 logging
- 发"重疾险的责任免除":状态"检索知识库中" → 回答流式打字机出现,带 [1] 角标;点角标右侧展开原文;后端日志可见 turn/step/tool/usage

## 每轮工具调用上限(参数语义,易混淆)
"一轮里能调多少次工具"由三个**独立上限**共同决定,各管各的、**互不占额度**:

| 参数 | 值 | 只管什么 | 不适用什么 |
|---|---|---|---|
| `max_steps_per_turn` | 20 | 整轮**总步数**(每个 LLM 回合=1 步,一步可含多个工具调用)——真正的"整轮所有工具调用"天花板 | —— |
| `max_retrieve_per_turn` | 5 | **知识检索类工具**(`search_knowledge` 等,**不含** `session_history_search`)的**步数** | 其它工具(算保费/记忆等)**不占**这 5 |
| `max_history_search_per_turn` | 1 | 会话内回源检索 `session_history_search`(回忆早前原文),独立计数 | 不占检索 5,也不新增步数(帮收尾) |

要点:
- **`max_retrieve_per_turn` 不是"5 次调用"**:按"**步**"计——一个 LLM 回合只要调用了检索类工具就 `n_retrieve += 1`;**同一步内发多个 `search_knowledge` 只算 1**。
- 达到检索上限且 LLM 仍想再调检索工具 → 循环**强制收尾**,调 `force_answer` 做"以已检索到的部分作答 + 以条款原文为准"的诚实兜底(**不编造完整清单**)。但这**不是**截图里那次停的原因——它只用到 1/5,是模型自身判断"检索结果是分裂片段、再查也列不全"而主动作答(遵循 SYSTEM"连续检索无新增就停止 + 诚实优先")。
- `calculate_premium`(算保费)/ `memory_save|search|forget` / 其它工具**不占**这 5,只受 `max_steps_per_turn` 兜底;写工具另有审批门控(管"是否放行",非次数)。
- 实现:`app/loop/agent_loop.py`(`n_retrieve` 计数 + 第 407 行强制收尾);配置:`app/config/config.py`(上限集中,三处均有注释)。

## 你学到了什么
- **ReAct 判定**:工具调用由 LLM 决定,而非无条件;寒暄不检索
- **生成器真流式**:事件逐个 yield,前端才能实时看到过程
- **指标采集 + 事件 schema 扩展**:ttft/tok-s 要进事件白名单才能持久化
- **标题 fallback**:确定性、不调用 LLM,首条消息取词截断

## 踩坑记录
- **生成器返回值冲突**:`_streaming_generate` 里既 yield 又想返回 tuple,导致 "too many values to unpack";改为在 stream_run 内联消费 chat_stream
- **事件 validator 白名单丢字段**:usage/turn_end 的 ttft_ms/tps 被 `_validate_usage` 丢弃;需扩展 validator 保留
- **JS 模板字符串写 Python 源码**:转义控制字符被 `tools.write` 写成真实控制字节 → 源码含 null bytes;改用 `chr()` 构造字符集
