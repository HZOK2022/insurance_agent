# 08 安全护栏全景:写审批 + 各类上限 + 诚实可追溯 + 审计,及未做项

> 护栏不是单一功能,而是「防止 agent 越界」的一整套约束,横跨 ①工具 ③工具层 ⑤ ⑥ ⑨。本文把各层对应到项目里的落点和状态。

## 护栏在拦什么(三类坏事)
- 不该做的动作:写数据、发消息、向客户下承诺 → 工具护栏(写审批)。
- 收不住:无限调工具/检索、烧 token、上下文爆 → 上限护栏(config 集中)。
- 说假话/泄密:编造、越权改口径、泄敏感、不确定硬答 → 输出护栏(诚实优先/拒答转人工/引用溯源)+ 审计。

## 分层 → 项目落点
| 层 | 拦什么 | 落点与状态 |
|---|------|-----------|
| 输入护栏 | 提示注入/越权指令 | 未专门强化(可后补) |
| 工具护栏 | 写/副作用工具必须人工审批、参数可改;工具失败=一等错误结果 | **⑤ 写审批**:ApprovalCenter + loop 门控 + 审批 API + 前端审批卡(阶段5)✅;**工具失败→ok/error_code+content 脱敏(D40)**:抛异常→tool_error、未知→unknown_tool、被拒→approval_denied,loop 继续 |
| 输出护栏 | 编造/不确定/引用溯源 | present_answer(引用绑定版本)+ force_answer(诚实放弃)✅;PII 脱敏未做 |
| 收不住护栏 | 步数/token/字节/检索/预算 | config 上限,注意语义(见 05 学习"每轮工具调用上限"):`max_steps_per_turn`=整轮**总步数**(所有工具调用的真正天花板);`max_retrieve_per_turn`=**知识检索"步数"**(search_knowledge 等,不含 session_history_search,同一步多调用只算 1,只约束检索工具);`max_history_search_per_turn`=会话内回源检索独立上限;另 max_tokens_per_turn/max_tool_result_chars/daily_token_budget_per_user ✅ |
| 可追溯 | 模型可见⟺已记录、append-only、审批可审计 | events 日志 + fail-closed 注册表 + ⑨ 审计/导出(阶段6)✅ |
| 权限/鉴权 | 单写者、内部 token | SQLite 单写者 ✅;internal_token 起步占位 |

## 四条铁律 = 护栏核心
1. **模型可见 ⟺ 已记录**:一切进模型请求的(检索片段/引用/回答)都落 SQLite;新模型可见输入=新事件类型,先注册。 → 可追溯护栏。
2. **易失层可随时清空**:事实源只有 SQLite(Qdrant/Redis 可重建)。 → 数据护栏。
3. **回答必须可追溯**:不确定就拒答转人工;引用绑定版本,条款更新不失效。 → 诚实护栏。
4. **上限加在完整结果上**:步数/token/字节三重上限 + 每客服 token 预算;上限集中在 config/,禁止散落硬编码。 → 收不住护栏。

## 为什么必备(本项目)
- 客户直面的保险销售助手,可能触发写操作/对外动作 → 写工具必须审批(明文铁律)。
- 会调知识库+LLM → 必须防无限检索/烧钱、防编造。
- 要上线/内审合规 → 审批持久化可审计、事件 append-only、可导出(⑨)。
- 对比:纯只读知识问答护栏可轻(主要防编造/召回不可靠);一旦能调用改状态的工具或对外承诺,护栏不可省。

## 验证/自测(已做)
- 写审批:tests/test_approval 3 项(批准带改参/拒绝不执行/读工具不门控)。
- 上限/诚实:test_loop_usage(截断/窗口)、test_compaction、LoopConvergenceTest(检索上限+诚实说明)。
- 审计/导出:tests/test_audit 10 项。
- 工具失败:tests/test_tool_error 3 项(抛异常→ok=False+tool_error+content 不泄漏异常串、未知工具→unknown_tool、回喂脱敏)。
- 全量 `python -m unittest discover -s tests` 107 项全绿。

## 未做(上线加固,可后补)
- 输入护栏:提示注入链路、角色/指令越界检测。
- 输出护栏:PII/敏感信息脱敏(dsh 遥测脱敏瀑布我们按铁律砍了)。
- 正式鉴权:现仅 internal_token 起步占位。
- 部署/监控(阶段7):健康探测、限流、告警。