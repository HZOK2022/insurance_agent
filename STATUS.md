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
| ① | **Agent Loop(ReAct turn/step)** | **已实现 ReAct**(判定/工具/流式/指标,D20) |
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
| 5 | 看不到统计(耗时/tok-s/etc) | **部分**:后端已采集 ttft_ms/run_ms/tps/elapsed_ms 落 usage/turn_end;前端统计栏未渲染 |
| 6 | 看不到上下文使用情况 | **未做**:context 占比采集未接 |

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

## 四、下一步
**C-2 前端落地(进行中)**
- [ ] 轨迹 tab 渲染完整 tool/推理链(当前空占位)
- [ ] 统计栏显示 dsh 式"轮·步|LLM 耗时|tok/s|缓存命中|输入/输出tok"(指标已采集)
- [ ] 前端真流式截图验证(打字机+状态;逻辑已改,待真实浏览器确认)

**C 余下 / D(原始计划剩余)**
- [ ] 上下文占比采集 + 前端显示(第 6 条)
- [ ] 写工具审批 + 审批卡片 UI;审计导出测试;知识管理页;回放测试全量;上线部署

## 五、已知不一致(已修正)
- docs/learning 原只有 00-03;已补 04(检索)、05(loop/LLM/引用链路)。06-08(护栏/审计/上线)待对应阶段完成再补。

## 六、C 阶段验证证据
- 后端流式:SSE 55 帧实时到达(带 ts),turn_start→…→assistant_chunk→turn_end 按序
- 指标:usage 含 ttft_ms/run_ms/tps;turn_end 含 elapsed_ms(实测 TTFT=996ms)
- "你好" 9 事件无 tool_call;"重疾险责任免除" 含 tool_call+retrieval+引用
- 22 项 unittest 全绿;selftest 建会话后自动删除(remaining 0)
