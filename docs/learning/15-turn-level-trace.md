# 15 轮级 trace_id:一次"粒度纠正"的落地

> 对应决策:DECISIONS **D82**;代码:`app/api/services/agent_service.py`(emit 挂 seq)、`app/loop/agent_loop.py`(turn_id 进日志/usage/badcase)、`app/util/logging.py`、`app/session/events.py`、`app/audit/queries.py`(history_qa)、`app/observability/metrics.py`、`web/src/App.tsx`(轨迹 #turn_id + 审计定位)、`tests/test_turn_id.py`。

## 这一阶段解决什么问题

可观测 tracing 此前把 `trace_id == session_id`(一个会话窗口 = 一个 trace_id)。排查"某一轮答错了"要**跳到整窗、再凭肉眼翻到第几轮**;事件 payload 里的 `turn` 还硬编码成 1,没有任何"第 N 轮"的稳定可引用键。

讨论定论(session=分组 / trace=一次执行应是一轮 / span=派生视图):**该给"轮"一个稳定、唯一、可复制、可直达的 id**。

## 为什么用 turn_start 的 seq,而不新造 id

- events 是 append-only 事实源,每条自带**全局自增 `seq`**(SQLite `AUTOINCREMENT` / MySQL `AUTO_INCREMENT`,`store.append` 返回 `lastrowid`)。
- 一轮的"开始"就是它的 `turn_start` 事件 → **该轮 id = turn_start 的 seq**。唯一、单调、可上溯窗口,零新增存储、零新编码。
- 比"改 trace_id 语义"(把窗口改成轮)破坏面小得多:session 仍是分组,旧链路不动,轮级用独立的 `turn_id` 表达。

## 落地要点(最小增量)

1. **seq 回到调用者**:`agent_service.run_prompt` 的 emit 把 `store.append` 的返回值挂回事件 `ev["seq"]` —— 于是 loop 拿得到、SSE 帧也带上 seq(live 轨迹可标轮)。
2. **loop 记住本轮 seq**:`agent_loop.turn()` 开头取 turn_start 事件上的 seq 为 `turn_id`;之后所有结构化日志 `extra` 与 `usage`/`badcase_snapshot` payload 都带它。`logging.py` 白名单加 `turn_id` 才会被收进 JSON/控制台行。
3. **观测/审计同口径**:`project_turn_metrics` 的每轮对象加 `turn_id` 别名;`history_qa` 每条问题带 `turn_id`(=该轮 turn_start seq),并兼容"turn_start 先于 user_message"(真实)与相反(旧数据)两种事件顺序——用 `pending` 标志区分"本轮的 turn_start 是否已就绪",避免串轮。
4. **前端可视化**:轨迹每轮 header 显示可复制的 `#turn_id`;审计行「定位该轮」→ 切到轨迹 tab,`#turn-<id>` scrollIntoView + 1.8s 闪烁高亮。
5. **events 校验器**:usage 的 `turn_id` 为可选键(旧事件不带不变),badcase 透传。不改 schema 版本(给已有事件类型 payload 加可选字段不违反 fail-closed,那是对事件类型而言)。

## 边界与教训

- **两处轮级"答案"必须同源**:usage/badcase 的 turn_id、观测的 turn_seq、审计的 turn_id 全部 = turn_start 的 seq,否则会出现"日志说第 A 轮、审计说是第 B 轮"的错位。这里用"同取 turn_start.seq"保证一致。
- **事件顺序不总是 turn_start 在前**:`tests/test_audit.py` 的 fixture 是先 user_message 后 turn_start;若 history_qa 简单取"最近一次 turn_start 的 seq"会在第二轮串到第一轮 —— 用 pending 标志修正,并在两种顺序下都测。
- **trace_id 该不该改名**:会。最终对齐(D85):**`session_id` = 窗口;`trace_id` = 一轮(= 该轮 turn_start 的 seq)**。曾临时用 `turn_id` 表达轮级、把 trace_id 留在窗口,这是偏离共识的命名,已拨回——日志/事件/观测/审计/前端一律用 `trace_id` 表示轮,`session_id` 表示窗口;旧 `turn_id` 键退役(新事件不再产生,旧事件无此键,兼容)。教训:改 UTF-8 源文件必须走 UTF-8 工具(write/edit),禁止 PowerShell 文本管道直写(编码误读曾造成一次 App.tsx 中文不可逆损坏,已 git 恢复 HEAD 后重做前端)。
## 接续(M4/D88):trace # 直达 + 步级 LLM 计量(llm_call)

D82/D85 把「轮」定成 trace 后,补了两个收口:

**① trace # 直达(排障闭环)**:手头只有 trace #(轮级 seq)时,以前要先知道会话、加载整个会话再翻——现在 `SessionStore.trace_events(trace_id)` 按全局 seq 反查会话 + 锚到所在轮(turn_start 是「该会话最近一次 seq<=目标」),返回 [turn_start, 下一 turn_start) 事件切片;`GET /api/traces/{id}` 暴露,观测页头排输入框直达该轮。设计点:容忍「轮内任意事件 seq」(排障常只拿到某条 usage/工具事件的 seq),锚定逻辑而不是要求精确的 turn_start seq。

**② llm_call 步级计量**:每步 = 一次 LLM 调用是这版 agent 的不变式,但 token/耗时原先只进日志行、usage 只在轮末累计一次 → 无法归因「哪一步贵/慢」。注册 `llm_call` 事件(step/model/pt/ct/ttft/run_ms/tps),agent_loop 每步 LLM 流结束后 emit(用 perf_counter 量真实调用 wall time,含流式接收;工具执行在调用完成之后,不占 run_ms);前端轨迹 step 头显示 LLM x · pt→ct,轮头的 工具/LLM 拆分在有 llm_call 时用实测、旧事件回退估算。

教训/边界:
- 新事件类型必须先注册(fail-closed 白名单),旧库/旧回放不产生该事件 —— 前端必须给「无 llm_call」留回退,否则历史轨迹显示会退化。
- usage(轮末累计)与 llm_call(逐步)分工:成本/token 报表继续吃 usage,逐步计量不破坏既有聚合。
- 改 UTF-8 源文件必须走 UTF-8 工具;含内嵌反引号/模板插值的文本,编辑脚本里避免再包一层反引号字符串(咬穿问题)。
**③ 让诊断"看见"(M4b):轨迹分阶梯 + 坏例卡找回** —— D79 把 dense/bm25/fused/rerank 四段分值存进 retrieval 事件、D83 存了四段计时,但"存了 ≠ 看见了":检索块前端只渲染单一最终分,四段阶梯只在 raw JSON 里;坏例友好卡(B3)的 CSS 还在、渲染逻辑在 D85 重做前端时丢了。补:轨迹检索块在事件带阶梯字段时渲染 d/b/f/r 四段(等宽小字,flex-wrap 独占一行,避免内联挤压;旧事件无字段回退原单分外观,兼容历史);badcase_snapshot 在 live/reload 两构建器都挂轮,坏轮展开体顶部渲染坏例卡。教训:**"数据进 events"与"前端能看"是两件事**——STATUS 曾把 D79 记为"前端轨迹已加阶梯行",但代码核对(DOM 实测)发现从未渲染,文档先行后代码没跟上;这次用 headless chrome 走真实 UI(trace#直达→展开轮)量出 5 块 ladder + wrapped 行,才算"修好"。
