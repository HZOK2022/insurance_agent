# AGENTS.md

给在本仓库写代码的 agent 的常设指令。项目背景与选型见 README.md,架构见 docs/architecture-v3.md。

## 项目定位

单机单进程 Python agent(保险销售客服知识助手)。架构参考 DeepSeek Harness(dsh)的核心模块,但**砍掉扩展性机器,保留脊梁与不变式**。

## dsh 参照源码(只读参照,不复制、不成为运行依赖)

- 本地 checkout(当前机器):`D:\LLM\deepseek-harness-master`(**完整源码仓库,含前端 packages/client**;非 git 仓库、无历史)
- 公开仓库(可克隆/查历史):`https://github.com/deepseek-ai/deepseek-harness`
- 参照版本:`0.1.2-alpha.1`(apps/cli/package.json);每个模块的参照包路径见 docs/project-skeleton.md
### ⚠ 前端源码位置(改前端**必须先看这里,禁止只从发布版 lib/*.js 编译产物反推**)

- dsh 前端源码目录:`D:\LLM\deepseek-harness-master\packages\client\`
  - 对话 UI 核心:`packages\client\ui-conversation\src\client\chat\`(MessageIconActions.tsx / message-chrome.ts / MessageItem.tsx / turn-assistant.ts / tool-node-reader.ts)
  - 会话 UI:`packages\client\ui-session\src\`;模型选择 ui-model-selection;引用 ui-reference;原语 ui-primitives;布局 ui-layout
  - 源码命名是 `ui-conversation`(在 packages/client/ 下),不是 `dsh-client-ui-conversation`(那是发布名,只有编译产物)——别搜错
- **铁律(AGENTS 新增)**:改任何前端交互(消息chrome/状态/工具节点/统计栏/hover/角标)之前,必须先读上面对应的 `packages\client\*` 的 .ts/.tsx 源码,搞清 dsh 的实现(布局、hover、时序、状态),再动手。**禁止**凭记忆/凭发布版编译产物猜测,否则必然偏离 dsh、返工。
- 发布版编译产物(仅当源码缺某细节时参考,需标注可信度):`D:\Program Files\...\@deepseek-ai\dsh\node_modules\@deepseek-ai\dsh-client-ui-*\lib\*.js`


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
- 前端 = Vite + React 18 + TS(web/):借鉴 dsh 视觉,不引入前端 Cordis/slot/模块图;状态用轻量 store;样式用 CSS 变量,不引入 Tailwind/组件库。改前端后本机 `npm run build` 或 `npm run dev` 验收(沙箱无法构建前端)。

## 运行与清理(强制)

1. 临时/中间文件(构建产物、临时脚本、录制样本、中间数据)用完即删,项目目录不留残余。
2. 测试/演示启动的服务与端口(uvicorn、vite dev、任何 node server)结束后必须 kill,禁止留在后台;用后台 job 启动的,结束即 job_kill。
3. 项目目录(D:\LLM\insurance-agent)是唯一权威源;在别处(如沙箱镜像)构建/编辑后,必须复制到项目目录并删除镜像。
4. 后端 reload 只监视 app/(run.py 已配 reload_dirs=["app"]);**不要**往项目根写会被 reload 扫描的临时产物(自测截图等放 OS temp 目录),删除临时文件不会惊动 reloader。

- **前端/交互改动必须自测(分两类,先分类再选验证手段)**:改完 web/ 或交互逻辑后,运行 `python scripts/selftest_ui.py`(build→起后端→造会话→截图)。
  - **视觉类**(配色/布局/间距/图标/动效):用视觉桥/人工判读截图,确认符合预期。
  - **几何类**(换行/宽度/对齐/是否竖排/气泡大小):**禁止只用视觉判读**(OCR 区分不了"每字一行"vs"一行两个字")。必须用 DOM 测量断言——注入脚本用 `Range.getClientRects()`/`getBoundingClientRect()` 量出真实行数与尺寸(如 `你好` 必须 LINES=1 且 W≈贴内容宽),用 `chrome --headless --dump-dom` 读出数字,断言通过才算修好。
  - 交付前**核对端口返回的 index.html 资源 hash 与磁盘 dist 一致**(避免"我改了、你浏览器用的是旧构建");并提醒用户硬刷新(Ctrl+F5)清缓存。
  - 不得仅改代码就让用户验证,也不得重复"我修好了"却无测量证据。
## 修复与验证协议(防"我改完=修好"的假阳性)

1. **先复现,后动手**:用户报告 bug 后,必须先用其确切条件把现象**造出来**(能测量到"错误行为"),确认根因,才允许改代码。复现不出来就把还缺的信息问清楚,禁止带假设直接改。
2. **修复 = 有可测证据**:几何类缺陷必须 A/B 证明"旧规则确实触发、新规则确实不触发",并给出测量数字(行数/尺寸),而非"看起来可以了"。
3. **交付前核对用户加载的产物**:确认正在服务的 index.html 引用的资源 hash == 磁盘最新 dist,避免用户硬刷新后依然加载到旧包导致"误以为没修好"。
4. **诚实沟通**:未实际执行的操作不得声称已完成(如"已写进 AGENTS.md")。每轮结束前自查:我声称做过的,是否真的做了并有据可查。

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
