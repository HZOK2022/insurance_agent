# STATUS.md —— 当前进度与下一步(每次会话第一份读物)

## 阶段
**设计完成,尚未写代码。** 开工前必读:AGENTS.md → 本文件 → DECISIONS.md。

## 已完成
- [x] 架构设计 v3(图:docs/diagram/agent-v3.html / agent-v3.png)
- [x] SQLite / Qdrant / Redis 三份 schema 设计(docs/)
- [x] 项目骨架目录(app/ 十一个包 + tests/ + scripts/)
- [x] AGENTS.md(常设指令 + 会话协议)、DECISIONS.md(决策记录)、README.md(准备清单)

## 进行中
- 无(待开工)

## 下一步(严格按序,勿跳步;每阶段验收全绿 + STATUS/DECISIONS 更新后才算完成)
1. **session 地基**(全系统依赖它):`app/session/events.py`(事件注册表)+ `app/session/store.py`(append-only + WAL + schema 版本 fail-closed)+ 回放测试骨架(`tests/replay/`)
   - 验收:单元测试全绿(未知事件类型拒绝 / events 无 UPDATE 路径 / schema 版本不匹配拒绝启动 / 崩溃重启可恢复)+ 回放骨架可空跑
2. `config/config.py`:集中所有上限/预算/审批阈值,禁止散落硬编码
   - 验收:config 单元测试全绿(默认值存在、阈值生效、无散落硬编码抽查)
3. 收集三类知识样本(条款 PDF / 结构化字段 / FAQ)各 5-10 份,搭摄取管道
   - 验收:三类样本冒烟测试全绿(摄取幂等、chunk_id 规范、版本过滤正确、过期版本不命中)
4. 引用验收端到端用例:{answer, citations} → 角标 → SQLite 原文(含历史版本)
   - 验收:端到端用例绿 + 全量回放绿(条款更新后旧会话角标仍可定位)

## 生效中的关键决策(详情见 DECISIONS.md)
1. Python 单机单进程,SQLite 单写者
2. 无插件系统;新行为写模块内
3. 模型可见 ⟺ 已记录;事件类型先注册,fail-closed
4. 不缓存 LLM 补全;读放行/写审批
5. 回放测试第一天做
6. 引用角标链路:结构化输出 + chunk_id 带版本

## 待定问题(定了更新 STATUS 与 DECISIONS)
并发客服数/峰值 QPS · 前端形态 · 纠错反馈流 · 每月 LLM 预算 · embedding 模型确认 · 外部 API 读写清单