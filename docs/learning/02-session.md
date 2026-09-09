# 02 阶段1:会话地基 —— append-only 事件日志

> 本阶段建立全系统的"事实源"。之后所有可追溯、崩溃恢复、回放都在这块地基上。

## 这一阶段解决什么问题
没有持久化，agent 一旦崩溃对话就没了；没事件模型就谈不上"模型可见 ⟺ 已记录"。append-only 日志 = 真相，是追溯/恢复/回放的根。

## 对应 dsh 源码
- `packages/core/session`(SessionEvent 日志 + 事件模型)
- `packages/session/session-persistence-sqlite`(WAL + schema 版本 fail-closed)

## 设计要点
1. **事件注册表**:类型必须先注册;未注册类型在写入/加载时一律拒绝(fail-closed)
2. **append-only**:store 只暴露 append/read,无任何改写事件的方法;测试断言源码不含 UPDATE/DELETE events
3. **schema 版本 fail-closed**:meta 表 schema_version 与代码不符 → 拒绝启动
4. **WAL + busy_timeout + 单写者**(agent 服务进程唯一写入方)

## Python 实现
```python
# app/session/events.py —— 事件类型注册表 + 校验器(register_type/validate/make_event)
# app/session/store.py —— SessionStore:append()/read()/close()
#   _check_schema():meta 版本不符 → RuntimeError;日志含未注册类型 → UnknownEventError
```

## 验收测试(tests/test_session.py,11 项全绿)
- 未知事件类型 validate → UnknownEventError;缺字段/chunk 缺字段 → ValueError
- append→read 往返、seq 从 1 单调递增、after_seq 过滤
- 加载含未注册类型日志 → 拒绝;schema 版本 mismatch → RuntimeError(fail-closed)
- append-only:无 update/delete API + 源码不含对 events 的 UPDATE/DELETE

## 手动测试
- 前端事件流页(阶段1 前端,待本机构建):发请求 → 实时看到事件逐条落库

## 你学到了什么
- **日志即真相**:append-only + seq + 事件类型,让"可追溯/恢复/回放"成为免费收益
- **fail-closed**:不认识的格式不猜着读,宁可拒绝启动
- 单写者 + WAL 的简单可靠

## 踩坑记录
- `SCHEMA_version` 小写拼写 → NameError;常量命名要精确
- 测试裸 `open()` 不关 → ResourceWarning;用 `with`
