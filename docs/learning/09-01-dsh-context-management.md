# dsh 上下文管理(参照研究)

> ⚠ **这是 dsh(DeepSeek Harness)的实现,不是本项目的实现。**
> 参考源码:D:/LLM/deepseek-harness-master/packages/(core/session、compaction/*、core/agent-loop、core/system-prompt)。

---

## 一句话

模型"能记住"的信息量有限。dsh 的做法 = **给模型看一个有序的"上下文",快满时整理**:最新对话**一字保留**,更早的**压成摘要**;整个过程写成日志,可审计、可回放。

---

## 一、实际 prompt 长什么样(压缩前)

给模型看的上下文 = **五块,按顺序拼**(①② 常驻从不压缩,③ 是消息表面;示例:保险销售,用户问"重疾险责任免除包括哪些"):

    ┌─ [① 系统提示词(SYSTEM)] ───────────────────────────────┐
    │ (这是【system】这条消息,内容因 agent 而异,顺序固定:)        │
    │ · harness 身份:"You are an AI agent powered by ..."      │
    │ · persona(标准 preset 的 deployment:persona,order 0):     │
    │   "You are a coding agent powered by {{model}}..."       │
    │ · agent 规则(示例=保险销售助手):                         │
    │   你是保险销售知识助手。铁律:检索到才回答;不确定转人工;      │
    │   只输出可读文本,引用处标 [idx]。                        │
    │ · 工具用法指导(每个工具一段):                             │
    │   search_knowledge:检索保险知识库,参数 {"query":"..."},    │
    │   查到先写一句"我检索到…"再作答。                        │
    │ ⚠️ 这里【不含】AGENTS.md。AGENTS.md 是"工作区上下文",      │
    │   作为一条 user-role <system-reminder> 消息注入③a(见下), │
    │   【不是】system 消息、也【不是】persona 段。             │
    └───────────────────────────────────────────────┘
    ┌─ [② 工具 schema(TOOLS)] ────────────────────────────────┐
    │ {"name":"search_knowledge","description":"检索保险知识库",   │
    │  "parameters":{"properties":{"query":{"type":"string"}}}} │
    └───────────────────────────────────────────────┘
    ┌─ [③a 工作区上下文(AGENTS.md/CLAUDE.md,user role)] ────────┐
    │ <system-reminder>Instructions from: AGENTS.md...       │
    │ ...(AGENTS.md 全文)                                   │
    │ <system-reminder>Instructions from: CLAUDE.md...       │
    │ ...                                                   │
    │ (这是一条【user】消息,不是 system;计在"对话消息"里。      │
    │  它是"常驻指令":dsh 每轮做 baseline 对账并重新注入,      │
    │  所以总在、不压缩掉。)                                 │
    └───────────────────────────────────────────────┘
    ┌─ [③ 对话(USER/ASSISTANT/TOOL 来往记录)] ──────────────────┐
    │ USER:      重疾险责任免除包括哪些?                          │
    │ ASSISTANT: 我需要检索责任免除条款。(thinking)              │
    │ ASSISTANT: (tool_call) search_knowledge({"query":"重疾险 责任免除"}) │
    │ TOOL:      [1] (A款:12) 第二十一条 被保险人因下列原因之一…  │
    │            [2] (A款:15) …                                  │
    │ ASSISTANT: 重疾险责任免除包括:1)故意自伤;2)酒后驾驶…[1][2]    │
    └───────────────────────────────────────────────┘
    ┌─ [④ 运行时上下文快照(最近的输入末尾追加)] ─────────────────┐
    │ Current runtime context. This snapshot supersedes            │
    │ earlier runtime-context snapshots.                           │
    │  provider: deepseek · model: deepseek-v4-flash · cwd: …      │
    └───────────────────────────────────────────────┘

**关键**:①(系统)+②(工具 schema)基本不变、**从不压缩**;③(工作区上下文 ③a、运行时上下文快照、对话/工具返回)越聊越长,**是压缩的对象**。其中 ③a 工作区指令(AGENTS.md/CLAUDE.md)虽在压缩候选区里,但 dsh 会**重新注入**,所以永远在、不会消失。

---

## 二、什么时候触发、怎么压、保留与边界

**触发**:
- **pressure(每两步之间)**:上下文用到 **约 80%**(thresholdTokens = 窗口 × 0.8)就整理一次;
- **context-overflow(写爆)**:本次回复超出窗口(模型报错),就整理后**重试一次**(超限只重试一次)。

**怎么压**:
1. 先给"大段工具返回"瘦身:超过 8192 字符就**留头(4096)+尾(1024)、中间折叠成标记**,并在日志记一条"删了多少 token"。
2. 再把"更早的对话"压成一段摘要(LLM 生成),**替换**掉那段。

**哪些保留 / 哪些压 / 边界**:
- **保留**:最近的**约 16% 窗口**的对话,原样逐字;
- **压缩**:这之前的所有更早对话,合成**一段摘要**;
- **边界**:在"保留尾"和"要压的老内容"之间切,**不能切在"一次工具调用"中间**(调用与返回不能拆散),要切在"完整一轮/一次完整工具调用结束"处。

> **三个容易误会的点(澄清)**:
> - **"16%" 是"窗口(contextWindow)的 16%",不是"对话总长的 16%"**。保留的就是"最近约 16% 窗口那么多 token"的对话,从尾部往回数到这么多就停。
> - **①(系统提示词)+②(工具 schema)不在"16% 保留"里**。保留 16% 只针对**消息表面(③,含工作区上下文 ③a/运行时上下文快照/对话)**;①+② 每次请求重新拼出、从不压缩,在窗口之外。注意 AGENTS.md(③a)是③里的 user 消息,会被计入 16% 保留的遍历范围,但 dsh 会重新注入它,所以它始终存在。
> - **"80% 触发"和"16% 保留"口径不同**:**80%(触发)算的是"全量"**,= system + tools + 对话(计量里 totalTokens = estimateHeader(= system+tools) + surfaceTokens(= 对话)),所以 **80% 包含系统提示词和工具 schema**;但 **16%(保留)只算对话**(被遍历的是 surface 节点 = 对话部分),**不含** system/tools。

> **数值示例(窗口 = 1M token)**:
> - 阈值:80% → thresholdTokens = 1,000,000 × 0.8 = **800,000**;保留尾 16% → retainTokens = 1,000,000 × 0.16 = **160,000**。
> - 假设 system 提示词 + 工具 schema 约 **10,000**(固定)。当对话(surface)= **790,000** 时,总计 = 10,000 + 790,000 = 800,000 → **触发压缩(pressure)**。
> - 压缩时:**保留**最近 **160,000** 的对话(从尾部往回数);把更早的 790,000 − 160,000 = **630,000** 对话压成一段摘要。
> - 压缩后:10,000(header)+ 摘要(假设约 5,000)+ 160,000(最近)≈ **175,000** < 800,000 → 不再压。
> - (工具结果单独按"字符"算:超 **8192 字符**就保 4096 头 + 1024 尾、中间折叠成标记。)

---

---

## 二·补、压缩流水线的**精确调用顺序**(源码核实)

不是"直接摘要"。`compaction-basic/src/index.ts` 的 `compactIfNeeded()` 里,**无论 `pressure` 还是 `context-overflow` 按同样的骨架走**:

```
[触发] 测 token
  ├─ pressure(自动,每两步之间):meter.measure() 总token >= thresholdTokens(= 窗口×0.8)才继续;低于则 return,不动。
  └─ context-overflow(写爆):模型请求被 provider 确认超出窗口才继续,绕过 0.8。
[① 剪枝(无模型)] 若有 toolResultPruner -> prune.pruneSession(session)   <-- 先把超预算的工具结果保头/尾+标记
[② 重测]        measurement = meter.measure(session)                  <-- 单一 token meter 重估
[③ 判断]        若重测 < thresholdTokens -> return null 【跳过摘要】(剪枝自己就降压了,不用总结)
[④ 摘要]        否则 selectCompactableRange(...)(切在"不拆工具对"的完整边界)
                  -> summarize()  <-- 对【已被剪枝的表面】LLM 摘要,替换头部区间
```

要点:
- **先剪、再决定要不要摘要**;剪枝常自行消除压力 -> 摘要可被整个跳过。
- 摘要只在"剪完仍超阈值"时发生,读的是**剪枝后的表面**。
- 触发口径(0.8)算**全量**(system+tools+对话 surface);保留(0.16)只算**对话 surface**;摘要必须比被替换区**更短**(否则一次压缩失败)。

---

## 二·补2、**谁在何时"读回原始日志"**(源码核实)——dsh 没有模型召回

被压缩/剪枝替换掉的**原文始终留在 append-only 的 `session.events` 里**,但**没有任何机制让模型主动去查它**:

| 读取方 | 干什么 | 何时/触发 | 是不是模型 |
|---|---|---|---|
| **客户端 UI(人)** | 把 `session.events` 渲染成完整消息列表,滚回去就能看到 | **手动、无自动触发**(UI 始终在) | 否 |
| **内部审计/回放(代码)** | 用 `sourceEventSeqs`(compaction-tool-result-pruner/src/index.ts:172)把被替换事件回指到原始事件,读"产生该结果的精确输入" | **开发/测试**触发;用于验证与 token 定价(README:"replay recovers the exact input that produced the pruned result") | 否 |
| **模型本身** | 看不到、也不能查被压缩前的历史;**没有"读历史"工具** | — | — |

> 结论:dsh 的压缩是 **durable 替换**(旧区换成摘要),不是"可回退的展示"。要原始细节 = **人看 UI** 或 **开发者审计回放**,**不是模型召回**。若想要"模型按需召回历史",那是**超出 dsh 的新增**(得给模型加一个读历史的工具)。

---

## 二·补3、摘要压缩指令(COMPACTION_INSTRUCTION + 保留信息)(源码核实)

`compaction-basic/src/summarizer.ts` 的 `COMPACTION_INSTRUCTION`,作为**最后一条 user 消息**追加在“被压缩区间对话”之后,让模型把上方对话浓缩成结构化 checkpoint:

    You are now acting as a compaction engine for this AI coding assistant. Condense the
    conversation ABOVE into a structured checkpoint that lets another model resume the work
    with no loss of essential context.

    Output EXACTLY the Markdown structure below: keep every section, in order. Use terse
    bullets, not prose paragraphs. Write "(none)" for an empty section — never drop a section.

    ## Primary Request and Intent
    - [the user's original and evolving goals; quote verbatim where the exact wording matters]

    ## Key Technical Concepts
    - [technologies, frameworks, patterns, and conventions in play]

    ## Files and Code
    - [exact path: why it matters, key changes or snippets]

    ## Errors and Fixes
    - [error: how it was resolved, plus any related user feedback]

    ## Pending Jobs
    - [explicitly requested work not yet completed]

    ## Current Work
    - [precisely what was in progress at this checkpoint]

    ## Next Step
    - [the single next action, directly in line with the most recent request, or "(none)"]

    ## Critical Context
    - [decisions and their rationale, constraints, user preferences, open questions, data needed to continue]

    Rules:
    - Write concise English engineering prose. Preserve exact file paths, commands, error strings,
      identifiers, numeric values, function signatures, and syntax fragments.
    - Capture user feedback and explicit instructions faithfully, especially corrections.
    - Do NOT mention this summarization request or that the context was compacted.
    - Output only the checkpoint text: do not call any tool or take any other action.
    - If the conversation already contains a <compacted-summary> block, it is a PRIOR checkpoint.
      Do not copy it forward verbatim: preserve still-true facts, drop stale ones, and merge newer
      information into a single consolidated summary under the same structure.

**封包**(frameSummary):`CHECKPOINT_PREAMBLE + <compacted-summary> … </compacted-summary>` —

    This is an automatically generated checkpoint condensing an earlier span of the conversation
    to free up context. Treat the captured context as established background and build on it
    without restating it. Continue the task directly from the messages that follow, without
    acknowledging this checkpoint.

    <compacted-summary>
    …(上面 7 节摘要)…
    </compacted-summary>

**保留的信息**(7 节):

| 节 | 保留信息 |
|---|---|
| Primary Request and Intent | 用户原始/演进目标,关键措辞原样 |
| Key Technical Concepts | 技术/框架/模式/约定 |
| Files and Code | 精确路径、为何重要、关键改动/片段 |
| Errors and Fixes | 错误 + 解法 + 用户反馈/纠正 |
| Pending Jobs | 明确要办但未完成的工作 |
| Current Work | checkpoint 时刻进行中的内容 |
| Next Step | 与最近请求一致的下一步 |
| Critical Context | 决策/理由/约束/用户偏好/未决问题/继续所需数据 |

**规则要点**:terse bullets;空节写 `(none)` 不丢节;**保留精确路径/标识符/数字/命令/错误串/函数签名/语法片段**;忠实捕获用户反馈与纠正;**不提及本次压缩**;已有旧 `<compacted-summary>` 则**合并而非照抄**。

---
## 二·补4、压缩触发与工具结果裁剪(pressure / context-overflow / ToolResultPruner)(源码核实)

> 来源:`packages/compaction/compaction-basic/src/index.ts`(compactIfNeeded)、`packages/compaction/compaction-tool-result-pruner/src/index.ts`(ToolResultPruner)、`packages/llm/token-meter/src/*`(contextBreakdown / estimate)。

**两种触发**:
- **pressure**:回合间/步间按 窗口×0.8(threshold_tokens)判断,低于不动。
- **context-overflow**:回合中**写爆硬窗口**时,绕过 80% 阈值、立即压。

**pressure 流程**(compactIfNeeded):
1. `resolveModelInfo` → `contextWindow` → `resolveCompactSpec(policy, contextWindow)`(threshold≈0.8、retain≈0.16)。
2. `measurement.totalTokens < thresholdTokens` → return null(不压)。
3. 否则:若注入了 `toolResultPruner` → **`prune.pruneSession()`(无模型、确定性)** → 重测。
4. 重测仍 ≥ 阈值 → `selectCompactableRange(session, measurement, retainTokens)` 选被压区间 → `compactRegion(start, end)`(LLM 摘要)替换;循环直到 < 阈值。

**context-overflow 流程**:先 prune → 重测 → `selectCompactableRange(..., 0)` → compactRegion。

**ToolResultPruner(工具结果裁剪)**:
- **只在压缩时运行,不在追加时截断** —— 工具结果追加进 surface 时是**全量**的。
- 遍历当前 surface 上的 `tool/result` 节点,对超 `thresholdChars` 的文本做**保头(headChars)+尾(tailChars)+中间 PRUNE_MARKER**。
- 每条裁剪:先 `session.append('compaction/prune', {shadowedRange, shadowedSeqs, shadowedTokenCount})`(影子定价),再 `session.append('tool/result', …, surfaceOp=replace)`(替换)。被替换原文仍在日志(sourceEventSeqs 回指)。

**surface 与工具结果**:
- `SURFACE_EVENT_TYPES = {user/message, assistant/message, tool/result}` → **tool/result 是 surface 事件**,模型可见。
- `contextBreakdown` = **systemTokens + toolsTokens + messageTokens**:
  - `toolsTokens = estimateToolsTokens(header)` = `JSON.stringify(header.tools).length/4` → 这是**工具 schema 定义**,不是结果。
  - `messageTokens` = surface fold(`estimateMessage`,含 `tool-result` block)→ **工具结果算在 messageTokens**。
- shadow-price 协议:`compaction/summary` 与 `compaction/prune` 携带 `shadowedTokenCount`,让 O(1) fold 与自己的 append 对账,替换后 messageTokens 相应减少。

> 与本项目对照:本项目是"追加即限长"(阶段B-1 工具结果落地截断,≤8000 字符),dsh 是"追加全量、压缩时才裁";本项目 `build_history` 丢弃历史工具结果,dsh 的 tool/result 留在 surface 跨轮(见 docs/context-management.md §8.5 的取舍)。

---

## 三、压缩后 prompt 长什么样(同示例)

    ┌─ [① 系统提示词] ──(与压缩前完全一样,不变)──┐
    ┌─ [② 工具 schema] ──(与压缩前完全一样)──┐
    ┌─ [③ 对话] ────────────────────────────┐
    │ (历史摘要 checkpoint)                     │
    │ 之前:客户问"重疾险责任免除",助手检索A款并作答   │
    │ (含故意自伤、酒后驾驶等),引用[1][2]。          │
    │ (最近保留,原样)                              │
    │ USER:      那等待期是多久?                    │
    │ ASSISTANT: 需要检索等待期条款。(thinking)     │
    │ ASSISTANT: (tool_call) search_knowledge({"query":"等待期"}) │
    │ TOOL:      [1] (A款:20) …                    │
    │ ASSISTANT: 本产品等待期 30 天…[1]             │
    └───────────────────────────────────────┘
    ┌─ [④ 运行时上下文快照] ──(最新,原样)──┐

**变化点**:③ 里**老的那段**被换成了**一行摘要**;其余(①②、最近对话、环境提醒)原样。老原文仍在日志,可回翻。

---

## 四、继续压缩(闭环)

又到 80% → 再压:这次"更早"的部分 = **上一段摘要 + 更多近期对话**,再压成**一段新摘要**("摘要的摘要")。如此往复。

- **不会堆爆**:每到 80% 就整理,且摘要必须比被替换的内容**更短**(否则失败),所以窗口永远有上限。
- **不会失忆**:最近原文逐字保留;更早的变成概览;要细节可从日志翻回原文(全存了,只是没全喂)。

---

## 五、关键概念(用大白话)

| dsh 术语 | 大白话 |
|---|---|
| session log | 全量会话日志(只追加,事实源) |
| surface | 给模型看的"对话窗口"(日志的投影) |
| surfaceOp replace + sourceEventSeqs | "用新内容替换旧内容",并记下被换掉的是哪几段 |
| request/context | 记一次请求用多大窗口(provider/model/contextWindow) |
| request/header | 请求快照,能重建"当时到底发了什么" |
| compaction | 把更早对话压成摘要 |
| tool-result prune | 工具返回太大时,保头尾砍中间 |
| compaction/prune | 记录"删了多少 token"(便于扣预算) |
| contextWindow | 模型一次能装下的窗口大小 |

> 事件类型是 **代码级白名单**(known-event-types.ts 的 Set),**不是数据库表**;读到白名单外的类型会拒(fail-closed)。dsh 没有 SQLite 表(本项目的 events 表是项目自己的)。fork/投影是 dsh 的,不属于"抄"的讨论范围。
