# 上下文管理(技术方案)

> **这是本项目(insurance-agent)要实现的上下文管理技术方案**。dsh 的实现研究见 docs/learning/09-01-dsh-context-management.md(参照,非本项目)。
> 目标:用「保尾压头」替换当前单轮方案,在项目简单架构(非 Cordis/surface 状态机)上实现 dsh 的**思想 + 事件契约**。

---

## 1. 背景与问题(现状)

- **跨轮上下文丢了**:app/loop/agent_loop.py 的 turn() 只构造 conversation=[user text],不读历史(旧 loop.py 有、D30 重构后丢)→ 追问无法参考上文。
- **工具内容无上限**:businesses/insurance.py 的 _format_chunks 把 top_k 块全塞给模型;agent_loop._run_tool 不截断;tool_result 事件 result_truncated 硬编码 False;max_tool_result_chars(8000)配置闲置。
- **无窗口/压缩/快照**:无 contextWindow 追踪、无 compaction 事件、无 request 快照、无 llm_retry。
- **后果**:①追问接不上;②大检索结果撑爆窗口;③改 prompt 无回放可回归(AGENTS.md 硬要求)。
- **范围澄清**:本方案针对**保险 RAG agent**(8181 后端),其模型可见上下文 = 系统提示词(insurance.py 的 SYSTEM)+ 工具 schema(search_knowledge)+ 对话,共三段;它**不含 AGENTS.md**。AGENTS.md 是**编码 agent(dsh)**的工作区上下文(以 user-role <system-reminder> 消息注入),不属于本 RAG agent 的上下文组件,故不在本方案统计/压缩范围内。

---

## 2. 目标

在项目里实现:跨轮上下文 + 窗口上限 + 超窗压缩(先剪工具结果、再压摘要、保尾压头)+ 请求快照/回放。全程 append-only、可审计可回放、不破坏引用角标(铁律 1/3)。

---

## 3. 数据模型与事件类型(先注册,fail-closed)

新增进 app/session/events.py 并在 __EVENT_TYPES 注册:

| 事件类型 | 载荷 | 用途 |
|---|---|---|
| request_context | {model, context_window, system_tokens, tools_tokens, messages_tokens, prompt_tokens, completion_tokens, compression_triggered} | 记请求窗口组成;每 turn 两条快照(回合开始=请求信封、回合结束=含回答的完整对话,D31) |
| request_header | {reason, system_len, history_len, window} | 请求快照 → 重建/回放 |
| compaction_start | {from_seq, to_seq, reason} | 开始压缩(替换区间) |
| compaction_summary | {summary, shadowed_seqs} | 摘要内容 + 被替换 seq |
| compaction_end | {reason, chars_saved} | 压缩结束 |
| compaction_prune | {seq, shadowed_token_count, chars_removed} | 工具结果剪枝的影子定价 |
| llm_retry | {attempt, err} | 重试观测 |

**约束**:这些类型必须先注册才能产生(否则写库即拒);events 表只 INSERT,绝不 UPDATE/DELETE;schema_version 同上。

---

## 4. 分阶段实现(每阶段:代码触点 + 测试)

### 阶段 A · 恢复跨轮上下文(最优先,回归)
- **做**:在核心/业务层加 build_history(store, session_id),把 user_message→user、assistant_message→assistant、assistant_narration→叙述 拼成 conversation;每轮 msgs = [system] + history + [当前 user + 检索资料] + [context]。
- **代码触点**:app/loop/agent_loop.py(turn 或加 history 参数)、app/businesses/insurance.py、app/api/services/agent_service.py(从 store 载历史)。
- **关键坑**:老回答的 [idx] 是**当轮编号**,喂进下一轮会串。history 里**剥掉旧 [n]**(保留文本),或改结构化 citations(每条 {idx, chunk_id} 与当轮检索绑定)。
- **测试**:单测——多轮 events → build_history 输出正确 user/assistant 序列;e2e——追问能参考上句。

### 阶段 B · 容量 + 窗口上限
- **做**:config 加 context_window(per model);每轮按码点/估算 token 算 system+history+资料+当前 总长;超窗保留近期、丢弃/压最早;记 request_context(D31:每 turn 两条快照——回合开始=请求信封(compression_triggered=窗口是否裁剪),回合结束=conversation 并入回答后重估(前端"对话消息"取 latest ⇒ 回答计入上下文占用)。)
- **代码触点**:app/config/config.py、app/loop/agent_loop.py。
- **测试**:超长 history → 窗口 ≤ 预算 + request_context 落库。

### 阶段 B-1 · 工具结果落地截断(D12 落地)
- **做**:prune_tool_content(content) 按 Array.from 码点测长;超 max_tool_result_chars(8000)就保头(约 4000)+尾(约 1000)、中间折叠成标记;在 _run_tool 应用到喂模型的 content,result_truncated=True;**reference(原始 chunks)仍完整**进 retrieval 事件(引用/溯源不丢);记 compaction_prune(被删 token/字符)。另外 _format_chunks 限条限长。
- **代码触点**:app/businesses/insurance.py、app/loop/agent_loop.py、app/session/events.py、app/config/config.py。
- **测试**:超长文本 → 断言头尾保留+中间标记+result_truncated=True+原文仍在 reference。

### 阶段 C · 压缩(先剪枝后摘要,保尾压头)【已实现,摘要=§8.3 座席工作台指令】
- **做**(app/loop/agent_loop.py `_compact_conversation`,生成器):estimate>budget 时:①`prune_tool_messages` 剪工具结果(无模型)→②重测→③低于预算跳过摘要→④`select_keep_tail` 保留尾(窗口×0.16,**不拆 tool_call/result 对**)→⑤先 yield `compaction_start`(前端显示"压缩中")→LLM 按 §8.3 指令摘要头部→⑥摘要必须 < 被替换区间,否则 yield 失败 `compaction_end` 并回退(只丢历史正文、不拆工具对)→⑦yield `compaction_summary`/`compaction_end`,头部替换为 system 帧包(CHECKPOINT_PREAMBLE + <compacted-summary>)。
  - **触发**:pressure(回合开始 + 回合中每步,窗口×0.8)与 context-overflow(回合中硬窗口);`reason` 写进 compaction_start/end。
  - **持久化**:build_history 遇 compaction_summary 折成一条 system 帧包、跳过 shadowed_seqs(append-only,原文留日志)。
  - **前端**:compaction_start→横幅「上下文窗口压缩中…」、end→已压缩;轨迹记 start/summary/end;引用 sources 按块文本 [idx] 位置映射(修"同 chunk_id 去重致角标不可点")。
- **代码触点**:app/compaction/compactor.py(§8.3 COMPACTION_INSTRUCTION/CHECKPOINT_PREAMBLE/frame_summary/prune_tool_messages/select_keep_tail/build_summary_request/collect_summary/truncate_summary)+ app/loop/agent_loop.py + app/session/context.py + app/config/config.py + web/src/App.tsx。
- **配置**:`compaction_threshold_ratio=0.8` / `compaction_retain_ratio=0.16` / `compaction_max_tokens=2000` / `max_tool_result_chars=8000`(B-1 追加即限长)。
- **测试**:tests/test_compaction(超窗触发/低于阈值不触发/摘要为空回退/不拆工具对/回合中 pressure 触发/摘要请求含 §8.3/frame_summary/prune/folding)+ 全量 84 项绿。

### 阶段 D · 请求快照 + 回放【已实现】
- **做**:turn 起始 emit `request_header`{reason, model, system_len, history_len, window}(请求快照,供重建"当时到底发了什么")。`tests/replay/recorder.py`(Recorder 包装 LLM,记录 messages+response→JSONL)+ `replayer.py`(ReplayLLM 按序返回录制响应,请求不一致抛错)+ `tests/test_replay.py`(录制→存载→回放→同一 assistant_message;改 prompt → 检测请求变更→turn_end=error)。
- **代码触点**:app/loop/agent_loop.py(emit request_header)、app/session/events.py(注册 request_header)、tests/replay/、tests/test_replay.py。
- **测试**:`python -m unittest` 87 项全绿(新增 test_replay 2 项)。

### 阶段 E · 重试/退避【已实现】
- **做**:`LLMClient.chat/chat_stream` 对 429 / 5xx / 网络超时·断连做**有上限的指数退避重试**(重试 `llm_retry_max_tries`=3、基数 500ms、上限 8s、抖动);**永久错误(4xx 非 429)不重试直接抛;流中途断连不重试**。每次重试 `on_retry({attempt, err})` → 记 `llm_retry` 事件(经 `_llm_retry_kw`,仅真实 client 传,不破 fake)。
- **配置**:config 加 `llm_retry_max_tries` / `llm_retry_base_delay_ms` / `llm_retry_max_delay_ms`;`get_llm` 传入。
- **测试**:tests/test_retry 4 项 + 全量 91 项绿。
- **代码触点**:app/llm/client.py、app/session/events.py。
- **测试**:mock 429 → 退避重试 + 事件。

---

## 5. 与 dsh 映射(借思想+契约,不抄状态机)

| dsh | 本项目落地 | 是否照抄 |
|---|---|---|
| surface/fold 状态机、surfaceOp 折叠 | 用「事件→每轮拼 conversation→压缩」替代 | **不抄**(简化) |
| request/context | request_context 事件 | 抄契约 |
| request/header | request_header 事件 | 抄契约 |
| compaction 摘要(保尾压头) | 阶段 C 同样思路 | 抄思想+契约 |
| tool-result prune(保头尾+标记) | 阶段 B-1 同样算法 | 抄思想+契约 |
| fork/投影 | 不做 | 不抄 |

---

## 6. 铁律合规(不可破坏)

- append-only:压缩/剪枝写成 compaction_* 事件,**不覆盖**被替换的原文(原文仍在日志可回放)。
- 模型可见⟺已记录:历史、摘要、剪枝后内容都在日志;被替换原文不丢(reference 完整)。
- fail-closed:新事件类型先进注册表;schema 版本同上。
- 引用可追溯:角标→chunk_id(带版本);检索快照完整;剪枝只剪喂模型的 content,不剪落库的 reference。
- 上限集中 config:threshold_ratio/retain_ratio/max_tool_result_chars/max_tokens 都进 config,禁止散落硬编码。

---

## 7. 落地顺序与验收

- 顺序:**A(跨轮,回归)→ B-1(工具截断,真缺口)→ B(容量+窗口)→ C(压缩)→ D(快照+回放)→ E(重试)**。
- 验收:每阶段单测绿 + 一次真实多轮问答(后端环境)验证 + 压缩前后 A/B(能区分产品/不撑爆窗口/追问接上)+ 回放测试绿 + DECISIONS.md 记一条新决策。
---

## 8. 场景澄清:座席工作台(重要,直接影响"压缩该保留什么")

> **本方案最初把"用户"当成"单个终端客户的一通长对话"。真实部署不是这样。** 这个澄清只改变"压缩该保留什么/压缩指令",不改变机械机制(窗口上限/剪枝/保尾压头/事件契约/append-only/请求快照)。

### 8.1 真实使用场景
- 本助手的使用者是**线上保险销售的客服(座席)**,不是终端投保人本人。
- 座席**一人同时服务几十上百位客户**,是"边回客户、边在助手上查资料、找好的回复"。
- 座席**不会**每问一句就来查一句——查询**断续、按需**,很多彼此独立。
- **无法区分当前查询对应哪位客户**:座席可能附一句描述("有个怎么样的客户"),也可能**直接查自己不熟悉的问题**(不带客户背景)。

### 8.2 对方案前提的修正
| 原前提 | 修正后 |
|---|---|
| 单一"用户"一长对话,跨轮追问可参考上文 | "用户"= 座席,一人对应多客户;查询跨客户、碎片化,不能假设"上句=同一客户同一产品" |
| 压缩目标 = 保留"这段对话讲到哪"(客户诉求叙述) | 压缩目标 = 保留**座席工作台的"已查明知识 + 口径 + 合规红线"**,让后续查询不重查、不给与前面口径不一致的答案 |

> ⚠ **跨轮上下文(阶段 A build_history)仍要做,但用途收窄**:用它做**产品/术语消歧 + 口径衔接**;**不要**据此假定"还是同一位客户"而说出承接性话术(座席的追问可能跳到不同客户/不同产品)。

### 8.3 压缩指令(座席工作台版)
作为**最后一条 user 消息**追加在"被压缩区间对话"之后:

    你现在是保险销售座席知识检索工作台的上下文压缩引擎。这台工作台被坐席用来边回客户
    边查资料、找好的回复;一位坐席同时服务几十上百位客户,查询彼此独立、通常跨多位
    客户,而且无法区分当前是哪位客户。请把上方这段**座席的连续查询**浓缩成结构化
    checkpoint,让工作台在后续查询中:不重复检索已查明的知识、不给与前面口径不一致的
    答案、保持合规红线一致,并能承接上次没答全的问题。
    
    严格按下面的结构输出:每个小节都保留、按顺序;用简短要点;某节没内容就写"(无)",
    不要删节。不要试图把上下文重构成"某一位客户的一次完整会话"——多数查询并不属于
    同一位客户。
    
    ## 已查明的知识与口径
    - [已查过的产品/条款/责任、年龄与保额规则及结论;每条带产品版本、条款名、引用
      编号[n]、关键数字。作用:后续同类查询直接复用,不重复检索,如要改口须显式说明]
    
    ## 已给出的回复与话术
    - [已给坐席的参考回复/话术要点,含引用与数字,保持后续口径一致]
    
    ## 未决与可追问点
    - [没答全的、坐席很可能追问的(如年龄下一档、保额往上加、病种范围)、答应要补的、
      需转人工/审批的;以及为什么没答(缺资料/超范围/需人工)]
    
    ## 客户上下文(坐席口述,尽力而为)
    - [坐席提问时附带的客户描述("有个怎么样的客户");标注为"坐席口述、可能跨多位
      客户、非唯一标识",只用于贴近当下那次提问。若坐席直接查事实未描述客户,写"(无)"]
    
    ## 风险与合规红线
    - [该拒答/转人工/需提示健康与如实告知的点;敏感项(既往症/健康状况/职业/年龄/区域/
      收入/投保人关系);可用与禁忌的话术;监管与使用限制]
    
    ## 当前查询
    - [checkpoint 时刻:坐席最近一次问查了什么,检索/生成到哪一步,命中/用了哪些条款]
    
    ## 下一步
    - [紧接坐席最近一次查询的单一下一步:补某产品细节、补年龄/保额边界、给话术、
      提示转人工,或给出建议;没有则"(无)"]
    
    ## 关键上下文
    - [取舍与理由(尤其为什么拒答/转人工/选某产品或口径);已用产品与版本;坐席的
      检索习惯与偏好;未决合规点;继续所需数据;不变式相关:SQLite 事实源、引用绑定
      版本、答复可追溯]
    
    规则:
    - 用简体中文写简洁记录,保留关键数字、产品名、条款名、引用编号[n]与客户/坐席原话
      (措辞重要时逐字)。
    - 忠实记录坐席的纠正与反馈,以及工作台之前的改口/更正。
    - 不要提及本次压缩或上下文被压缩。
    - 只输出 checkpoint 文本,不要调用工具或做其他操作。
    - 若已有 <compacted-summary> 旧块,视为上一个 checkpoint:不照抄;保留仍为真的事实、
      丢弃过期的,把新信息合并成同一结构。

**封包**(CHECKPOINT_PREAMBLE + <compacted-summary> 包裹):

    这是自动生成的检查点,浓缩了之前一段对话以释放上下文。把捕获的上下文当作既定背景,
    直接在其上继续,不要复述。直接从后面的消息继续任务,不要提及本检查点。
    
    <compacted-summary>
    …(上面 8 节)…
    </compacted-summary>

### 8.4 保留什么(8 节)
| 节 | 保留什么 | 为什么 |
|---|---|---|
| 已查明的知识与口径 | 查过的产品/条款/责任、年龄与保额规则及结论;每条带版本+条款+引用[n]+数字 | 座席反复查同类问题,结论立即可复用,避免重查/答不一致 |
| 已给出的回复与话术 | 已给坐席的参考回复/话术要点 | 不重复生成、口径一致 |
| 未决与可追问点 | 没答全的、很可能追问的、答应补的、需人工/审批的、为什么没答 | 承接上次没给全的 |
| 客户上下文(坐席口述) | 问题里附带的客户描述,标注"坐席口述、可能跨多客户、非唯一标识" | 只用于贴近当下那次提问,不当身份 |
| 风险与合规红线 | 该拒答/转人工/提示健康告知的点、敏感项、禁忌话术 | 跨查询一致,保险销售重点 |
| 当前查询 | 坐席最近一次问查了什么、检索/生成到哪步、用哪些条款 | 精确接续最近一次 |
| 下一步 | 紧接最近一次查询的单一动作:补细节/补边界/给话术/提示转人工/建议 | 单一下一步 |
| 关键上下文 | 为什么选某产品/口径、产品版本、坐席检索习惯、未决合规点、缺的数据、SQLite 事实源+引用可追溯 | 决策/偏好/约束 |

### 8.5 对机械机制与后续的约束
- 机械机制(窗口上限/工具结果剪枝/保尾压头/事件契约/append-only/请求快照)不变——只关心 token 预算与记忆卫生,与场景无关。
- **变的是压缩指令**(§8.3):摘要蕴含"工作台的知识/口径/红线",而非"某位客户的会话叙述";客户描述只是尽力而为的辅助,不构成客户身份。
- **备选**:若 compaction 在此碎片化、跨客户场景收益有限,可做**结构化的"知识/口径缓存"**(产品→条款→结论→引用版本)让同类查询直接命中;这是超出 dsh 的新增,是否做待定(见 DECISIONS 待定)。
