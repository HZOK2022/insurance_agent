# 07 阶段6:审计/追溯(⑨)与可观测(⑥)——查询视图、导出、指标聚合、成本计量

> 在 events 日志(事实源)之上补上「查询视图 + 导出 + 指标聚合 + 成本计量」。照 dsh session-telemetry 的「事件→记录」投影结构,但砍掉外置 backend/OTel/capability seam。

## 这一阶段解决什么问题
- 审计/追溯只停留在「有日志」:按客服/时间/会话检索历史问答、合规留证导出都没有。
- 可观测只有分散指标:turn 级 token/ttft/tps 散在 usage/turn_end 事件里,没有会话级/全局聚合,没有成本计量。

## 对应 dsh 源码
- `@deepseek-ai/dsh-session-telemetry`(packages/session/session-telemetry):把 session/event 投影成 ledger 记录 {channel,time,severity,attributes,body},severity 映射(tool/result.isError、turn/end 错误→error;其余 info)。
- 但砍掉它的 backend seam / OpenTelemetry / 脱敏瀑布 / handoff 游标 / sharing 披露(AGENTS:不引入插件系统/事件总线/capability seam)。

## 设计要点
1. **只读派生**:所有指标/问答视图都从 SQLite events 日志按需计算,绝不写历史(events append-only 铁律)。
2. **投影为记录**(照 dsh):project_turn_metrics 逐事件投影成 turn 记录(question/answer/citations/model/tokens/cost/ttft/tps/耗时/severity/steps/tools/retrievals/approvals/retries)。
3. **severity 映射**照 dsh:turn/end reason 非 completed、ok=False → error;其余 info。
4. **成本诚实**:usage 的 cost_estimate 仍 None;token 数始终准确;成本仅当 config 配了单价(LLM_PRICE_INPUT_PER_1M/OUTPUT,默认 0)才算,未配价显示「—」——不编造单价、不写回历史。
5. **审计查询视图 + 导出**:history_qa(按 user_id/session_id/since/until/limit),export_session(jsonl/json/csv)。

## Python 实现
- `app/audit/queries.py`(history_qa/export_session/audit_overview)
- `app/observability/metrics.py`(project_turn_metrics/session_metrics/overall_metrics/estimate_cost/severity_of)
- `app/api/routers/audit.py`(GET /api/audit、/api/audit/{sid}/export、/api/observability、/api/observability/{sid})
- `app/config/config.py`:llm_price_input_per_1m / llm_price_output_per_1m(默认 0)
- 前端:Center 第三个 tab 「审计」(AuditView:问答列表+每轮指标+导出;App:getAudit/getObservability 加载)

## 验收测试
- `tests/test_audit.py` 10 项:history_qa 重建问答(2 turn 含正常+错误/审批/重试)、按 user 过滤、export jsonl/json/csv、audit_overview;project_turn_metrics、session_metrics 聚合(tokens/错误/重试/审批)、成本配价后计算、estimate_cost 未配价= None、severity。
- 全量 `python -m unittest discover -s tests` **104 项全绿**。
- HTTP 冒烟:obs totals(9会话/48轮/1.74M token/错误1/avgTTFT 996ms)、audit 24 条、export 有效 JSONL;前端 tsc+build 通过、served==dist。

## 手动测试
- 前端点「审计」tab:看当前会话问答列表、每轮 token/成本/检索/审批/重试;点导出下载 jsonl/json/csv;看全局/本会话指标卡。

## 你学到了什么
- **派生指标从事实源计算,不引入外置系统**:审计/可观测是「读事件 → 投影 → 聚合」,与 SQLite 解耦,单机即可闭环。
- **成本计量要诚实**:token 数是准的;单价未配置就显示「—」,不编造。单价进 config/.env 可校准。
- **照 dsh 结构、砍 seam**:只取「事件→记录投影」的骨架,丢掉 backend/OTel/脱敏等扩展性机器。