# 评估与可运维(evaluation & ops)—— 设计与计划

> 定位:内部"销售客服副驾驶(copilot)"。本文件记录 **评测(质量门)** 与 **可观测/可运维** 的方案,以及"轻量自建、不引入重型框架"的决策依据(D40)。

---

## 0. 决策要点(D40)

- **评测 + 可观测:自建轻量,不引入重型框架**(LangSmith/Langfuse/Ragas/DeepEval/Phoenix;Prometheus/Grafana/OTel/Sentry/Loki)。
- 理由:单机、单进程、单模型(DeepSeek)、小团队、内部客服工具;重型框架是"大团队/多模型/多节点"才需要的,现阶段不必要(AGENTS 砍重机制基调)。
- **顺序:先评测(质量门),后可运维(可观测)**。

---

## 1. 评测模型(五层)

| 层 | 评什么 | 谁来评 |
|---|---|---|
| ① 结果层 | 准确性 / **groundedness**(答案是否被来源支撑) / faithfulness(忠于资料) / 完整性 / 安全合规 / 有帮助 | LLM-judge |
| ② 过程层(轨迹) | 工具选择对不对、参数对不对、检索充分性、收敛性(不空转)、步骤必要性 | 规则 + LLM-judge |
| ③ 引用层 | 每个 [idx] 是否指向支撑该断言的 chunk(precision/recall);角标可点可溯 | 规则(程序化) |
| ④ 效率/成本层 | 步数、TTFT、总延迟、token/成本 | 规则(指标) |
| ⑤ 边界/鲁棒层 | 答不上→诚实拒答/转人工;prompt 注入→无视;越界→拒绝 | 规则 + 专用用例 |

## 2. 评估集

- 文件: `docs/eval/eval_set.json`(数据,可版本化)。
- 每条:`{id, type, user_query, expected: {must_cite?, must_honest?, must_compute?, not_overclaim?, ...}, rubric_hints}`(期望**行为**,非期望精确文本)。
- 类型覆盖:知识查询 / 投保规则(年龄)/ 保额适配 / 算费配置 / 产品对比 / 边界诚实 / 合规不夸大 / 引用追溯。
- 规模:初期 20-40 条,后续扩。

## 3. LLM-judge 评估器

- 脚本 `scripts/eval_agent.py`:读评估集 → 跑 agent(用当前 SYSTEM+工具)→ 收集回答 + 事件轨迹 → 用 DeepSeek 当"裁判"(按 rubric 打分,返回 reason + score)→ 汇总。
- 输出:`eval_report.md` / JSON:整体质量分、各维度得分、覆盖率、违规率、失败案例。
- 也可复用"回放测试"(录制 I/O → 改后再放)做回归(阶段 D)。

## 4. 可运维(可观测)

### 4.1 结构化日志 + trace id
- 每个 turn 一个 `trace_id`(= 会话 session_id + 该 turn 的 seq,或 uuid)。
- 一条日志:`{ts, level, trace_id, session_id, turn, step, event_type, tool, latency_ms, prompt_tokens, completion_tokens, model, error}`;JSON 格式;INFO/WARN/ERROR 分级;滚动文件(`data/logs/app.log`)。
- 两层:**审计层**(SQLite append-only 事件,已有,可回放)= 事实源;**运行层**(结构化运行日志)= 排查运维。

### 4.2 Metrics(从事件日志聚合,不另起炉灶)
- `/metrics`(或统计查询)从 `events` 表聚合:
  - 量:turn 数、工具调用数、步数分布。
  - 延迟:TTFT、每步、总时长(平均/P50/P95)。
  - 成本:prompt/completion token、累计成本。
  - 质量:检索命中率(no_hits 率)、引用率、转人工率、拒答率、评估得分。
  - 分类:按模型(flash/pro)、按产品、按问题类型。
- 事件日志(append-only)是事实源 → 从它聚最准、可重放。

### 4.3 告警
- 简单脚本/阈值告警:错误率>X、延迟>Y、日 token 预算>Z、Qdrant/Redis/DeepSeek 依赖异常。
- 与排查联动:告警带 `trace_id` → 用事件日志回放该 turn。

## 5. 实施顺序(备忘)

1. **评测**:评估集(种子 10 条 → 扩 20-40)+ 评分标准 → LLM-judge 脚本 → 报告 → 质量基线。
2. **可运维**:结构化日志 + trace id → /metrics 聚合 → 告警脚本。
3. 迭代:评估集扩、judge 调、指标上告警。

## 6. 依赖
- 评测需 agent 能跑(已有);评估集/rubric 是"先想清楚量什么"的先决材料。
- 可运维需在 agent loop 加少量埋点。
