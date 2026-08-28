# AGENTS.md

给在本仓库写代码的 agent 的常设指令。项目背景与选型见 README.md,架构见 docs/architecture-v3.md。

## 项目定位

单机单进程 Python agent(保险销售客服知识助手)。架构参考 DeepSeek Harness(dsh)的核心模块,但**砍掉扩展性机器,保留脊梁与不变式**。

## dsh 参照源码(只读参照,不复制、不成为运行依赖)

- 本地 checkout(当前机器):`D:\LLM\deepseek-harness-master`(解压副本,非 git 仓库,无历史)
- 公开仓库(可克隆/查历史):`https://github.com/deepseek-ai/deepseek-harness`
- 参照版本:`0.1.2-alpha.1`(apps/cli/package.json);每个模块的参照包路径见 docs/project-skeleton.md

## 黄金法则(不可破坏)

SQLite = 事实源 · Qdrant = 可重建的派生索引 · Redis = 可丢失的加速层。
易失层(Redis/Qdrant)全空,系统照常跑,只是慢。任何易失层不得成为唯一真相。

## 四条铁律

1. **模型可见 ⟺ 已记录**:进模型请求的一切(含检索片段、引用、回答)都落 SQLite。新的模型可见输入 = 新事件类型,必须先注册。
2. **易失层可随时清空**:事实源只有 SQLite。
3. **回答必须可追溯**:不确定就拒答转人工;引用绑定版本,条款更新不失效。
4. **上限加在完整结果上**:步数 / token / 字节三重上限 + 每客服 token 预算;上限集中在 config/,禁止散落硬编码。

## 写代码约束

- events 表**只 INSERT,绝不 UPDATE / DELETE 历史**;恢复与修改一律走新事件。
- 事件类型必须先进注册表(app/session/events.py);日志出现未注册类型 = 拒绝启动(fail-closed)。schema 版本同规则(meta 表)。
- 检索片段在注入模型**之前**写入日志。
- 不缓存 LLM 补全;不缓存"被依赖的读"工具结果(读了缓存再写真实数据 = 脏写);缓存命中也要落日志。
- 审批:读取型工具放行,写入型工具必须审批;审批记录持久化、可审计。
- 不引入插件系统 / 事件总线 / capability seam——新行为写在所属模块内(loop / llm / tools / session / retrieval / guardrails / audit / observability)。
- 每个模块的 dsh 参照实现见 docs/project-skeleton.md:照抄结构,不抄机制。
- 测试优先回放(录制-回放,不依赖真实 API);改动 prompt / 条款 / 工具 schema 必须跑回放测试。
- 单写者:SQLite 写入只允许 agent 服务进程。

## 阶段验收(Definition of Done)

1. 阶段完成 = 该阶段验收测试全绿 + STATUS.md 已更新(完成/进行中/下一步)+ 新决策已入 DECISIONS.md + **学习文档 docs/learning/NN-*.md 已追加**(每章模板见 00),四者缺一不可。
2. 测试分层:单元(注册表 fail-closed / append-only / schema 版本 / config 阈值)· 回放(录制-回放,不依赖真实 API;改 prompt / 条款 / 工具 schema 必须跑)· 端到端(引用角标链路)。
3. 每写完一个模块立即写它的测试,不许攒到阶段末尾一次补。
4. 测试描述行为:改行为就改测试,并在 DECISIONS.md 说明为什么。

## 会话协议(防遗忘,开工必读)

1. 每次开始工作(新会话或长会话中途)先依次读:**AGENTS.md → STATUS.md → DECISIONS.md**。前两份一分钟内读完,禁止凭印象开工。
2. 推理过程中一旦发现自己"在猜设计",立即重读 STATUS.md 与相关 docs,不要凭记忆复述架构。
3. 每个里程碑完成后**立即**更新 STATUS.md(完成/进行中/下一步);新决策**立即**写入 DECISIONS.md,不许攒到最后。
4. 文档与代码不一致时:先查 DECISIONS.md 与 docs/architecture-v3.md 判断哪边错再改;禁止两边同时静默漂移。
5. 常读层(本文件 + STATUS.md + DECISIONS.md)按需更新,长文档按链接展开——新代理先读常读层。

## 文档索引

- 架构:docs/architecture-v3.md;图:docs/diagram/agent-v3.html / agent-v3.png
- SQLite:docs/sqlite-schema.md · Qdrant:docs/qdrant-schema.md · Redis:docs/redis-usage.md
- 骨架与 dsh 参照:docs/project-skeleton.md
- 学习教程(过程叙事,比 README 详细):docs/learning/00-overview.md