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

## 下一步(严格按序,勿跳步;每阶段验收全绿 + STATUS/DECISIONS 更新 + 学习文档追加后才算完成)
0. **契约 + 前端壳 + mock(新增,契约先行)**
   - 后端:定 API/SSE 契约与事件类型表(只定不实现);mock 后端(脚本/录制数据驱动前端)
   - 前端:纯 HTML+JS 单页,仿 dsh 聊天区视觉(会话侧栏 + 聊天窗 + 流式消息 + 工具卡片),FastAPI 托管静态
   - 验收:前端壳可打开并"演"一次假对话;契约文档成文(docs/contract.md)
1. **session 地基** + 事件流页
   - 后端:`app/session/events.py`(事件注册表)+ `app/session/store.py`(append-only + WAL + schema 版本 fail-closed)+ 回放骨架(`tests/replay/`)
   - 前端:事件流页实时显示事件落库(可视化"模型可见 ⟺ 已记录")
   - 验收:单元全绿(未知事件拒绝 / 无 UPDATE 路径 / schema 拒载 / 崩溃可恢复)+ 回放骨架空跑 + 事件流页实时可见
2. **config 集中** + 设置页
   - 验收:config 单元测试全绿(默认值存在、阈值生效、无散落硬编码)
3. **知识摄取与检索** + 知识管理页
   - 采集三类样本(条款 PDF / 结构化字段 / FAQ)各 5-10 份
   - 前端:知识管理页(上传文档 / 看 chunk / 检索试玩)
   - 验收:三类样本冒烟全绿(摄取幂等、chunk_id 规范、版本过滤正确、过期版本不命中)
4. **loop + LLM + 引用链路** + 真聊天页
   - 前端从 mock 切真后端;聊天页角标可点(定位 SQLite 原文)
   - 验收:端到端用例绿 + 全量回放绿(条款更新后旧会话角标仍可定位)
5. **护栏与审批** + 审批卡片 UI
   - 验收:写工具必须审批、审批记录可审计(测试绿)
6. **审计与可观测** + 历史检索页 / trace 页
   - 验收:审计导出测试绿
7. **上线生产**:回放全量绿 + 部署文档 + 监控告警

## 生效中的关键决策(详情见 DECISIONS.md)
1. Python 单机单进程,SQLite 单写者
2. 无插件系统;新行为写模块内
3. 模型可见 ⟺ 已记录;事件类型先注册,fail-closed
4. 不缓存 LLM 补全;读放行/写审批
5. 回放测试第一天做
6. 引用角标链路:结构化输出 + chunk_id 带版本

## 待定问题(定了更新 STATUS 与 DECISIONS)
并发客服数/峰值 QPS · 前端形态 · 纠错反馈流 · 每月 LLM 预算 · embedding 模型确认 · 外部 API 读写清单