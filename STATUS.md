
# STATUS.md —— 当前进度与下一步(每次会话第一份读物)

> 本文档是唯一权威进度记录。每次开工先读本文件;完成里程碑后立即更新。

## 一、原始计划(架构 v3,9 模块)
架构总览见 docs/architecture-v3.md;模块→dsh 参照见 docs/project-skeleton.md。

| # | 模块 | 状态 |
|---|------|------|
| ⑧ | 知识检索(摄取管道/Qdrant/混合检索) | **核心已跑通**(A条款 56 块,score 0.59-0.68) |
| ⑦ | 服务化外壳(FastAPI+SSE) | **已建 + 真流式**(端口 8181,SSE 逐帧) |
| ② | LLM 客户端(DeepSeek 流式+结构化) | **已建含 chat_stream**(流式+TTFT) |
| ④ | 会话与上下文(append-only 日志) | 已建(11 测试绿)+ 自动标题 |
| ① | **Agent Loop(ReAct turn/step)** | **已重构为核心+业务层**(agent_loop.py 通用核心 + businesses/insurance.py 业务;loop.py 已退役) |
| ③ | 工具层 | 骨架(只读检索;写审批未实现) |
| ⑤ | 安全护栏(写审批) | **未实现** |
| ⑥ | 可观测(指标/回放测试) | **指标已采集**;回放测试未做 |
| ⑨ | 审计/追溯 | 基础(events 日志;查询视图/导出未做) |

## 二、用户反馈的 6 条问题(当前状态)
| # | 问题 | 状态 |
|---|------|------|
| 1 | 分不清问题/结果,无标识 | **已解决**:每条消息下方 dsh 式时间戳 chrome + 左右布局 |
| 2 | 后回答覆盖前回答,只显示在第一个问题后 | **已解决**:send/loadEvents 按序 append,多轮不覆盖 |
| 3 | 所有问题都走检索,不符 ReAct | **已解决**:LLM 判定 need_search,寒暄不检索 |
| 4 | 看不到推理/工具调用过程 | **已解决(后端)**:stream_run 逐个 yield turn/step/tool/assistant_chunk;前端打字机+状态已改(待截图) |
| 5 | 看不到统计(耗时/tok-s/etc) | **已解决**:usage/turn_end 下发 turn 级 ttft_ms + 整轮口径 tokens_per_second(真实 token 数);前端 live+reload 渲染完整统计栏 |
| 6 | 看不到上下文使用情况 | **未做**:context 占比采集未接 |
| 7 | 重启后会话"丢失"(误报) | **已解决**:根因=启动窗口 12s(顶层 import sentence_transformers)+ 前端静默吞错;embedder 惰性 import(窗口→~3s)+ 前端"后端未就绪,正在重试…"自动重试;浏览器自测通过;8181 后端需重启生效 |

## 三、已完成(B/C 阶段为主)
- [x] 架构 v3 · schema · 骨架 · AGENTS/DECISIONS/README/STATUS
- [x] 阶段0-2:前端壳 + 会话地基(events 注册表/store 11 测试)+ config 集中(6 测试)
- [x] 阶段3 检索核心:摄取(A条款 56 块)+ Qdrant + chunker 3 测试绿
- [x] **B 阶段:loop 重构 ReAct**——判定(need_search)+ 工具调用 + 流式生成;事件 turn_start/step_start/step_end/tool_call/tool_result;启用 assistant_chunk
- [x] **B 阶段:事件 schema 扩展**——注册 step_start/step_end;usage/turn_end 保留指标字段
- [x] **真流式**——LLMClient.chat_stream(测 TTFT);AgentLoop.stream_run 生成器逐个 yield;agent_service/prompt.py SSE 逐帧(实测 55 帧实时)
- [x] **日志**——AgentLoop 每步 logging.info(turn/decide/retrieve/generate/end),控制台可见执行过程
- [x] **会话标题生成**——app/session/title.py 照 dsh fallback;首条用户消息自动生成标题
- [x] **selftest 自动清理**——建会话→测试完自动 delete_sessions_meta
- [x] **前端**——消息时间戳 chrome(formatClock 照 dsh);assistant_chunk 打字机 + 实时状态;tool_call 卡片;多轮不覆盖;对话/轨迹选项卡标题下方;气泡贴内容(几何测量验证)
- [x] **前端端口 8181 + 学习文档 04/05 补全**
- [x] **loop 退役 + 核心/业务分层**——app/loop/loop.py 删除;旧单测迁移到核心 agent_loop.py + 保险业务层(present_answer/force_answer),`python -m unittest` 31 项全绿;核心 `turn()` 修复为"每写一条事件即 yield"(照 dsh / agent_service 契约)供 SSE 逐帧推流;新增 docs/businesses.md(如何新增业务)。
- [x] **会话标题刷新**——会话首条消息自动生成标题(store.append 的 user_message 触发 fallback);但前端 sessions 只在 mount/new/delete/rename 时刷新,发送后 send() 不刷新,侧栏/标题仍显示【新会话】。已修:send() 的 finally 里补 setSessions(await listSessions()),回合结束后标题即时反映。验证:store 级 e2e(run_prompt 首条 user_message 后标题已生成)、生产库 data/agent.db 的 8 个会话均有标题;沙箱无法连真机,待真机硬刷新确认。

## 四、下一步
**C-2 前端落地(进行中)**
- [ ] 轨迹 tab 渲染完整 tool/推理链(当前空占位)
- [x] 统计栏:消息 chrome 显示"用时 X秒 · 首token Xms/X秒 · X tok/s"(usage/turn_end 下发 ttft_ms + tokens_per_second,真实 token 数)
- [ ] 前端真流式截图验证(打字机+状态;逻辑已改,待真实浏览器确认)

**C 余下 / D(原始计划剩余)**
- [ ] 上下文占比采集 + 前端显示(第 6 条)
- [x] **核心+业务层迁移,D31**——loop.py 退役;剩余:真机 E2E 跑核心+业务层、回放测试基建(project-skeleton 第 82 行)
- [ ] 写工具审批 + 审批卡片 UI;审计导出测试;知识管理页;回放测试全量;上线部署

## 五、已知不一致(已修正)
- docs/learning 原只有 00-03;已补 04(检索)、05(loop/LLM/引用链路)。06-08(护栏/审计/上线)待对应阶段完成再补。
- (前端)① 流式期间曾把 LLM 原始 `{"action":...}` JSON 当回答暴露(见 D24)——已修:流式只显"正在思考…",`assistant_message`(解析块)到达才渲染块;② 发送后首个 token 前(实测从 turn_start 到首个 assistant_chunk 约 **10.5s**)整屏零反馈(见 D25)——已修:发送即显"正在思考…"占位,结束未落 `assistant_message` 则显"回答生成中断,请重试。"。验证:node 渲染分支判定 + `vite build` 通过;新包 `index-C7N0oSQy.js` 含 `正在思考`/`回答生成中断`/`streaming-hint`,旧文案"正在生成回答"已移除;index.html 引用 `/assets/index-C7N0oSQy.js`。**待做:** 真机 E2E(DeepSeek+Qdrant)复测"发送即反馈、流式不暴露 JSON"。
- (后端,turn-dangling)触发主因=D27:LLM 给 citations 只带 chunk_id 无 idx → `_resolve` 传 {"idx": None} → `_validate_assistant_message` 做 int(c["idx"]) = int(None) 崩 → assistant_message 写不进 → turn 悬挂(即库里 095c36f32fb1 与 D24 截图同根因)。已修:`_resolve` 对缺失/非法 idx 按 len(out)+1 顺序编号;validator 对 None/非数字 idx 用 try/except 兜底 len(cites),不再崩。辅因=D26(健壮性):`loop.stream_run` 加 try/except/finally——`except GeneratorExit` re-raise、`except Exception` 记 reason=error 并 logger.exception,finally 一律补写终结事件(GeneratorExit 只落库不 yield);assistant_chunk 改为先落库再推送。验证:无 idx citations 的 retrieve→answer → citations=[{"idx":1},{"idx":2}],turn_end.reason=completed,事件链完整;尾端错误 → turn_end(reason=error) 优雅收尾;gen.close() → turn_end(reason=interrupted);`python -m unittest` **29 项全绿**(新增 CitationNoIdxTest 1 项 + LoopTerminalTest 3 项)。**待做:** 真机 E2E 重跑"重大疾病包括哪些"确认无 int(None)、回答结构化、引用正常;异常路径下步骤级 step_end 是否补齐。
- (循环收敛/诚实性,见 D28)一 turn 曾连发 12 次检索、最后自称"100种完整"却只列 17 种。已修:config 加 `max_retrieve_per_turn=5`;`loop.stream_run` 加检索计数,≥上限注入"立即基于已有资料回答"强制指令,仍返回 retrieve 则强制诚实说明(完整清单以条款原文为准);SYSTEM 增"诚实优先/严禁声称共N种或完整列表/连续检索无进展就停止"。验证:retrieve-forever → tool_call=5(原 10),转诚实说明,turn_end=completed;`python -m unittest` **31 项全绿**(新增 LoopConvergenceTest 2 项)。**待做:** 真机复测检索收敛与诚实文案。
- (前端交错显示,见 D29)思考原先挤进一条折叠 Think、工具卡全堆上面;已改为行模型:`role=user|think|tool|answer` 按事件序交错渲染——每步"思考"行紧跟其触发的工具卡,末尾是"回答"。验证:① `vite build` 通过,新包 `index-C47nclya.js`(含 think/answer/tool 行,旧"正在生成回答"已移除);② Node 模拟 send() 状态机对假 SSE → 行序 `[user, think1, tool1, think2, answer]`(think 在 tool 前、answer 最后)= dsh 交错。**待做/已知限制:** 历史会话 reload 只能重建 tool+answer 行(中间步骤 reasoning 在 assistant_chunk 未分组),需后端把每步 reasoning 分成独立事件;真机 E2E 复测实时交错。
- (对齐 dsh,见 D30)loop 从自定义 {action,query} JSON 重构为 dsh agent-loop 结构:client.py 支持原生 tools;loop.py 每步传 SEARCH_TOOL schema、流式收 reasoning/text/tool-call 块,有 tool-call→执行 search_knowledge 并以 tool-role 回喂→下一步,无 tool-call→该步 text 解析为结构化 answer+citations 结束;SYSTEM 改为 agent+tools 引导(需资料先写叙述再调工具,足够就输出 JSON)。前端行模型增 role:"text"(叙述行):pendingText 累积、tool_call 时落成叙述行、assistant_message 时丢弃。验证:fake-LLM e2e(叙述+原生工具调用+回喂+答案)事件链完整;`python -m unittest` 31 项全绿(test_loop_usage 重写为原生 tool-call 协议);`vite build` 通过(新包 index-BzoL2r-v.js 含 role:"text");node 模拟 dsh 协议 → 行序 [user, think1, text1(叙述), tool1, think2, answer],断言 user,think,text,tool,think,answer。**待做:** ① 真机 E2E 按新协议跑通;② rebackup 回放测试基建(project-skeleton 第 82 行要求,改 prompt 必须跑回放,现无);③ 叙述逐字流式(现 tool_call 时一次性落行)。
- (D30 补:原生 tools 走不通,已切 json+叙述)deepseek-v4-flash 对原生 function-calling 兼容性差(真机 400:V4 拒绝 tool_choice、多轮必需回传 reasoning_content);沙箱无法真机验证,故切到 json_mode 每步一个 JSON{narration,tool,query}/{narration,answer,citations},用【已检索资料】system 消息回喂;新增 assistant_narration 事件,前端 role:text 叙述行。验证:json+narration e2e(叙述+工具→回答)事件链含 assistant_narration;31 项测试全绿;vite build 通过(新包 index-DkWhkxOY.js)。

## 六、C 阶段验证证据
- 后端流式:SSE 55 帧实时到达(带 ts),turn_start→…→assistant_chunk→turn_end 按序
- 指标:usage 含 ttft_ms/run_ms/tps;turn_end 含 elapsed_ms(实测 TTFT=996ms)
- "你好" 9 事件无 tool_call;"重疾险责任免除" 含 tool_call+retrieval+引用
- 22 项 unittest 全绿;selftest 建会话后自动删除(remaining 0)
- 前端 live+reload DOM 实测:工具卡实时插在回答前(user→tool→…→assistant,5s/10s/27.7s/41s 轮询一致);统计栏完整(用时 119秒 · 首token 915ms · 110 tok/s);25 项 unittest 全绿
- 重启恢复自测:页面在启动窗口打开 → 侧边栏"后端未就绪,正在重试…"(非"暂无会话")→ 后端就绪后自动恢复 2 个持久化会话(代理日志 502×2 → 200,无需刷新);import app.main 1.92s / /api/health 2.76s(原 12.1s)
