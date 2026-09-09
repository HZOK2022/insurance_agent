# 06 阶段5:写工具审批(安全护栏)——人工批准/改后批准/拒绝/挂起

> 本阶段给"写入型工具"加人工审批门控:loop 遇到写工具先发 `approval_request` 并**阻塞等待**;前端弹审批卡,人批准(可改参数)/拒绝(必填原因)/挂起,决定经 HTTP 端点写入并唤醒等待的 turn,再决定是否执行工具。

## 这一阶段解决什么问题
- **写工具裸奔**:agent 一旦能调用会"改变外部状态"的工具(发消息、改数据、提交申请),没有人的把关就可能执行不该执行的动作。
- **审批不可追溯**:批准/拒绝/改了什么参数,都要有记录,符合铁律3(答复可追溯)与 AGENTS「写入型工具必须审批;审批记录持久化、可审计」。

## 对应 dsh 源码
- 写审批在 dsh client/agent-loop 里是"工具执行前的人机协同"分支(工具 schema 带 `write` 标记,执行前走审批通道)。我们**砍掉扩展性机器,保留脊梁**:用单进程 `threading.Event` 模拟"pending → 决定 → 唤醒"。

## 设计要点
1. **门控判定**(`agent_loop` 工具调用点):
   `_gated = tool.get("write") and approval is not None and cfg.write_tools_approval != "auto"`
   → 只有标记 `write:True` 的工具才被门控;读工具放行;`write_tools_approval=manual`(默认)需人工,`auto` 仅开发。
2. **阻塞等待 + 双路径**:`ApprovalCenter.new_request` 生成 `request_id` 并登记 pending → loop `yield approval_request`(SSE 推给前端)→ `wait(request_id)` **阻塞 turn**;决定由其它请求(`POST /api/sessions/{sid}/approval`)经 `decide` 写入 `threading.Event` 唤醒。
3. **决定语义**:仅 `status=="approve"` 执行工具,参数用 `edited_args or args`(改后批准用 edited_args,普通批准用原 args);其余 status(`reject`/`defer`/`timeout`)都不执行,并以 tool 消息回喂模型("写操作「X」未被批准(status),未执行。")——loop 不崩,继续按 rejection 生成回答。
4. **审计**:`approval_decision` 事件由审批端点写入 store(append-only,含 request_id/status/edited_args/reason/decided_by);`approval_request` 事件由 loop 写入。两类都进事件注册表(fail-closed)。
5. **前端审批卡**:收到 `approval_request` 置 `pendingApproval`;卡片显示工具名+完整参数(可编辑 JSON)+ 四个按钮(批准/改后批准/拒绝+原因/稍后);提交即清卡。

## Python 实现
- `app/guardrails/approval.py`:`ApprovalCenter`(`new_request/decide/wait`,线程安全,超时默认 300s)
- `app/loop/agent_loop.py`:工具调用点门控 + `approval_request` yield + `wait` + 批准用改后参数/拒绝回喂
- `app/session/events.py`:注册 `approval_request`/`approval_decision` + 校验器(白名单字段)
- `app/config/config.py`:`write_tools_approval="manual"`、`approval_exempt_tools` 占位
- `app/api/services/container.py`:`get_approval()`(单例 ApprovalCenter)
- `app/api/routers/approval.py`:`POST /api/sessions/{sid}/approval`(写 `approval_decision` 事件 + `center.decide`)
- 前端 `web/src/App.tsx`:SSE 处理 `approval_request` → 审批卡;`web/src/lib/api.ts`:`submitApproval`

## 验收测试
- `tests/test_approval.py` 3 项:
  1. 批准带改后参数 → 工具被执行且用 edited_args
  2. 拒绝 → 工具不执行;tool_result 事件存在;turn 正常结束
  3. 读工具(无 `write`)→ 不触发 approval_request(不门控)
- 全量 `python -m unittest discover -s tests` **94 项全绿**
- 端点冒烟:`POST /api/sessions/{sid}/approval` 对不存在 request_id 返回 `{"ok": false}`(不崩、可审计)

## 手动测试
- 前端 `npm run build` + `npx tsc --noEmit` 通过;审批卡样式可先临时把某写工具置 `write:True` 观察。
- 真实触发需业务层有写工具(当前保险 bundle 只有检索/保费计算,均只读);加写工具后自动被门控。

## 你学到了什么
- **门控点是"默认拒绝"之外的选择**,不是单向执行:工具带上 `write` 标记后,执行权交由人。
- **单进程如何"在另一个请求里唤醒阻塞循环"**:`threading.Event` + 进程内单例 center,把"等审批的 turn"和"前端决定"解耦。
- **审批语义要覆盖所有分支**:批准(改/不改)、拒绝(必填原因)、挂起/超时都变成"不执行并回喂模型"——loop 永不因未批准而中断,rejection 也进了对话历史。
- **审计与 fail-closed**:`approval_request`/`approval_decision` 都是模型-可见/受控事件,先注册再落库;审批记录持久化可回看。
