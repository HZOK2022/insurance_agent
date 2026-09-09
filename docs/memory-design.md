# 记忆系统设计(docs/memory-design.md)

> 本模块是 Agent 的"记忆"横切能力,按三层定位设计(不是分类堆砌)。参照 dsh core/session 的
> append-only 事件日志,但记忆作为**可修正的状态**落在 SQLite(黄金法则),与 events 的**不可变历史**
> 分层并存。使用场景见 DECISIONS D32:线上保险销售座席工作台,一位座席服务几十上百客户,
> 查询彼此独立、跨客户、无法区分客户。

## 0. 设计前提(三条铁律如何落地)

| 铁律 | 落地方式 |
|---|---|
| SQLite = 唯一事实源 | 记忆落 SQLite 表,不落散文件(不学 trae 的 .md/workbuddy 的 .jsonl) |
| events 只 INSERT | 记忆"当前状态"在**可 UPDATE 的表**;每次变更写一条 **事件**(append-only 审计),历史全在 events |
| 模型可见 ⟺ 已记录 | 记忆注入模型前写 `memory_injected` 事件;新事件类型先注册(fail-closed) |

**核心原则:可变状态(memory/画像)与不可变历史(events)分离。**
和现有 `sessions.title` 可 UPDATE、但事件仍在 events 完全一致。

---

## 1. 三层定位(先定职责,再推导记什么)

| 层 | 定位 | 核心价值 | 现有基础 |
|---|---|---|---|
| **L1 会话级** | 当前会话的"工作记忆",防遗忘 | 防重复检索、防前后矛盾、早前细节可回源 | events 全量 + build_history + compaction(§8.3 checkpoint) 已在 |
| **L2 跨会话级** | 换会话后"值得带过去"的沉淀 | 不重复检索、口径一致、经验复用 | 无(新增) |
| **L3 客服画像** | 使用者的偏好,与单次对话无关 | 全程一致的回复风格 | 无(新增) |

三层**写入来源、召回方式、生命周期各不相同**,不能混。

---

## 2. 记忆类型总表(写入 system prompt 的蓝本)

> 设计原则:**默认不记,只有"对未来回答有增量价值"才记**。知识库覆盖的、单次查询答案一律不记。

| 类型 | 作用域 | 记什么(定义) | 不记什么(边界) | 写入源 | 召回 |
|---|---|---|---|---|---|
| redline 红线 | global | 合规边界(该拒答/转人工/不可承诺/需提示) | 产品数字、话术 | 人工维护,蒸馏**禁止**写 | 每次会话注入 |
| policy 口径 | global | 公司确定的"怎么答"(费率表述/免责解释/对比话术) | 个人风格、客户个案 | 人工维护为主,蒸馏可提议需人工确认 | 条件注入+检索 |
| preference 偏好 | user | 客服个人风格/常用话术/注意点 | 产品事实、客户信息 | **客服本人编辑** | 每次会话注入 |
| fact 补充事实 | global/user | **知识库外**且未来会用的事实(内部通知/未收录产品) | 知识库已有的(那是知识库的事) | 自动蒸馏(带来源)+手动 | 检索 |
| lesson 经验 | user | 踩坑/被纠正/客户常问盲区(人群规律,脱敏) | 客户个体档案 | 自动蒸馏 + 客服可确认/删除 | 检索 |
| pending 知识缺口 | global | 知识库缺失/检索失败的点 | — | 自动蒸馏 | 检索,出口=补知识库 |

**判据(蒸馏/写入共用)**:检索命中知识库的一律不记;单次查询答案不记;客户个体信息不跨会话;拿不准就不记。

---

## 3. L1 会话级:session_history_search(本会话历史检索)

### 3.1 为什么需要
长会话多次压缩(阶段C 保尾压头 + §8.3 checkpoint)后,**结论**被保护住,但**早期过程原文**(早期 user_message 全文、早期检索完整 chunk、旧对话逐字)被逐层摘要模糊化。坐席同会话回问"前面那个 XX/刚才查过的"时模型答不上。

### 3.2 关键决策:不做全局跨会话检索(conversation_search)
原因(D32):坐席服务几十上百客户、查询跨客户、无法区分客户 → 全局检索捞到的是**跨客户混合噪音 + 污染 + PII 风险**,且模型 query 无法准确表达要找哪段。跨会话真正要复用的走 **L2 蒸馏记忆**(已筛选),不走原始全文检索。

### 3.3 工具 schema(**不含会话字段**)
```python
HISTORY_SEARCH_TOOL = {"type": "function", "function": {
    "name": "session_history_search",
    "description": "检索【当前会话】早前的完整原文(用户提问/助手回答/检索过的条款片段)。"
                   "仅当本次问题明确回指本会话早前内容(如『前面说的那个』『刚才查过的』『之前你说』"
                   "『首轮那个客户』『你之前给的口径』、或要复用/改口早前结论),"
                   "且该内容经上下文压缩后已不在当前可见上下文时使用。"
                   "普适的条款/知识问题一律用 search_knowledge,不要用本工具。",
    "parameters": {"type": "object", "properties": {
        "query":       {"type": "string", "description": "要找回的早前内容的主题/关键词"},
        "past_rounds": {"type": "integer", "description": "可选,只看最近 N 轮;省略则搜整个会话"}},
        "required": ["query"]}}}
```

### 3.4 作用域安全:会话 id **由系统注入**,不暴露给模型
每个会话有唯一 `sessions.id`(uuid.hex[:12])。检索必须用它限定本会话,但**不放进模型可传参数**——由核心 loop 调用 handler 时注入当前 `session_id`。理由:模型传 id 会幻觉/传错/越权跨会话;坐席访问边界由系统强制。

**改动**:核心 `_run_tool(name, args, start_idx, session_id=None)`,调用 handler 时传 `session_id`;业务层 handler 统一接 `(args, start_idx=0, session_id=None)`(现有工具加默认参数兼容);bundle 注入 `store`。

### 3.5 触发条件(回指信号三维判定,写进 SYSTEM)
```
- 工具优先级:普适条款/知识问题 → search_knowledge;仅当问题**明确回指本会话早前内容**时,
  才用 session_history_search。
- session_history_search 触发(三条同时满足才用):
  ① 问题明确指涉本会话早前内容(回指):"前面/刚才/之前/首轮/那个客户/你之前说/记得你问过/前面的口径";
  ② 该内容已不在当前上下文(被压缩、或被早期轮次/窗口裁剪覆盖);
  ③ 不找回就答不准或答不全。调用时先写一句叙述,再调工具。
- 不要用它:全新问题、知识库能答的条款问题、当前上下文已含的信息、跨会话/别的客户的内容。
- 拿不准就先 search_knowledge;确需回忆本会话历史才 session_history_search,每轮最多 1 次。
```

### 3.6 防滥用三层关卡
| 关卡 | 做法 |
|---|---|
| ① 次数 | `max_history_search_per_turn`(config,默认 1),超了提示"已达本会话历史检索上限" |
| ② 只返回"不在当前上下文"的原文 | 复用 `build_history` 得"已进上下文"的 seq 集合,检索时**排除**那些 seq,只返回被 compaction 影子/窗口裁剪掉的早前事件 —— 保证增量价值 |
| ③ 相关度守卫 | 检索后按 query 关键词打分排序,命中太低返回"本会话无相关早前记录"(不硬塞无关历史) |

结果截断复用 `prune_tool_content` / `max_tool_result_chars`(防膨胀)。

---

## 4. L2 跨会话记忆

### 4.1 `memory_entries`(当前状态,可 UPDATE)
```sql
CREATE TABLE memory_entries (
  id               TEXT PRIMARY KEY,
  user_id          TEXT NOT NULL,          -- 客服账号;'global' = 全员共享
  scope            TEXT NOT NULL,          -- 'global' | 'user'
  kind             TEXT NOT NULL,          -- redline|policy|preference|fact|lesson|pending
  key              TEXT NOT NULL,          -- 语义标识(覆盖去重)
  text             TEXT NOT NULL,          -- 喂模型的内容
  source_session_id TEXT,
  source_event_seq  INTEGER,
  confidence       TEXT DEFAULT 'auto',    -- auto|explicit|verified
  status           TEXT DEFAULT 'active',  -- active|archived(标记不物理删)
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL,
  UNIQUE(user_id, scope, kind, key)
);
```
- 覆盖=按 `(user_id,scope,kind,key)` upsert,旧值留在 events(append-only)。
- 忘记=归档(archived)+ 写 `memory_archive`,不 DELETE。

### 4.2 事件注册表(events.py,先注册再落库)
- `memory_upsert`{entry_id, scope, kind, key, text, confidence, reason, old_text?}
- `memory_archive`{entry_id, reason}
- `memory_injected`{scope, count, tokens}(注入前写,铁律1)

### 4.3 写入来源
| 通道 | 触发 | confidence |
|---|---|---|
| 显式 | 坐席"记住 X"→ 按作用域路由(见 §6) | explicit |
| 蒸馏 | 压缩时 + 会话结束时(复用 §8.3 checkpoint) | auto |
| 人工 | 管理员维护 global 红线/口径 | verified |

### 4.4 蒸馏触发(不每轮)
- **主通道**:compaction 触发时,§8.3 checkpoint 已生成 → 顺手拆成条目落记忆(≈零额外 LLM)。
- **副通道**:会话结束时整会话提炼 diff。
- **显式指令**立即写(不蒸馏)。
- 每轮蒸馏:❌(贵、碎片化、重复)。代价:自动记忆非实时(显式仍实时)。

### 4.5 召回
- **注入式(会话开始)**:redline + preference + 高优先级 policy(按 `memory_inject_max_tokens` 预算取 top-N)。
- **检索式(按需)**:`memory_search` 工具(照 search_knowledge 的 handler 契约),模型需要历史经验/结论时调用。
- `memory_search` 与 `search_knowledge` 区分:知识=条款,记忆=经验/口径/坑。

---

## 5. L3 客服画像(agent_profiles,客服可编辑)

### 5.1 表(条目式,可增删改)
```sql
CREATE TABLE agent_profiles (
  id         TEXT PRIMARY KEY,
  user_id    TEXT NOT NULL,
  key        TEXT NOT NULL,       -- 'style'/'cautions'/'preferred_products'/...
  value      TEXT NOT NULL,
  kind       TEXT DEFAULT 'preference',  -- preference|lesson(系统沉淀,客服可改)
  source     TEXT DEFAULT 'manual',      -- manual|auto|verified
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(user_id, key)
);
```

### 5.2 编辑通道
- API:`GET /api/profile`、`PUT /api/profile/{key}`、`DELETE /api/profile/{key}`(会话 token 鉴权,只能改自己)。
- 前端:"我的偏好"面板(列表+增删改)。
- 审计:每次编辑写 `memory_upsert`(`source='manual'`)。
- **审批边界**:客服改自己画像放行(个人偏好,留审计);global 红线/口径走主管审批(可后续用 `users.role`)。

### 5.3 与自动蒸馏关系
画像 = 客服手写(`source='manual'`)+ 客服确认过的蒸馏条目(`verified`)——冲突时客服手写优先;系统蒸馏只写 L2 `memory_entries`,不写画像。

---

## 6. 写入路由(显式"记住 X")
按**作用范围**分类存放,放错类会污染:
| 说 | 归属 | 原因 |
|---|---|---|
| "以后我回答要简洁" | user preference | 个人偏好 |
| "公司规定 XX 必须提示健康告知" | global redline/policy | 影响全员→审批 |
| "尊享e生免赔额 1 万" | global fact 或 user lesson | 知识结论 |
| "客户王先生高血压拒保过" | **不跨会话**(会话内) | PII + 跨客户污染 |
| "答应客户明天补费率表" | pending(知识缺口) | 工作台待补,非客户承诺 |

落库校验:检测客户身份词/健康信息 → 拒绝跨会话存储,提示"该信息仅在当前会话有效"。

---

## 7. config 上限(集中,铁律4)
```
memory_inject_max_tokens       # 每次会话注入预算(≈800)
memory_max_entries_per_user    # 活跃条目上限,超出提示整理
memory_search_top_k            # 记忆检索返回条数
memory_entry_max_chars         # 单条记忆上限(≈300)
max_history_search_per_turn    # 本会话检索每轮上限(=1)
memory_distill_*               # 蒸馏条件
```
PII:自动蒸馏内容过 `redact_pii`(复 D46);既往症等业务敏感项允许存但按 §8.3 标红线。

---

## 8. 前端形态
- 客服"我的记忆"页 = user 级全部(手写 preference + 蒸馏 lesson/fact,可确认/删除/编辑)。
- 全局记忆(redline/policy)admin 可见可管(主管审批)。
- 审计 tab 可加"评测/失败归因"视图(后续 A 项)。

---

## 9. 实施分期
| 期 | 内容 | 闭环 |
|---|---|---|
| **P1** | session_history_search(§3)+ 记忆表/事件/注入(§4/§5)+ memory_search 工具 | 召回+本会话回源闭环 |
| **P2** | 显式"记住/忘掉"路由 + 写审批 + 前端"我的记忆"页 | 人工写入闭环 |
| **P3** | 自动蒸馏(压缩时+会话结束)+ 会话归档摘要 | 自动写入闭环 |

## 10. P2.0 落地(非侵入 + 长度压实)
- **可插拔开关**:`memory_enabled`(config,默认 False)。关 = 不注册记忆工具/不加指令帧/不注入,行为与未加记忆**完全一致**(非侵入);开 = `container.get_insurance_bundle` 经 `app/memory/tools.attach_memory` 叠加。
- **独立包**:`app/memory/`(store.py 存储 / system.py 指令 / tools.py 三工具+接入),删除整个包即可整体移除;核心 loop 不改(复用 P1 session_id 注入),业务 SYSTEM 源码不改(MEMORY_SYSTEM 独立帧,run_prompt 在 memory_enabled 时拼接)。
- **存储**:`memory_entries` 表(SessionStore._ddl 建);`MemoryStore` 独立连接(同 agent.db 单写者)。同 `(user_id,scope,type,key)` 覆盖。
- **事件**:`memory_upsert`/`memory_archive`/`memory_injected`(events.py 注册,fail-closed)。
- **工具**:`memory_save`/`memory_search`/`memory_forget`。handler 用核心注入 session_id 解析归属(结构上杜绝跨会话);写/忘追加 memory_upsert/archive 事件(审计);语义豁免 D38 审批(写 SQLite 内部状态,非外部副作用)。
- **召回**:常驻注入 = `build_memory_frame`(MEMORY_SYSTEM + MemoryStore.inject_frames 读红线/偏好/口径,按 memory_inject_max_tokens 取);检索 = memory_search(按需)。
- **长度压实**:单条 `memory_entry_max_chars` 截断;总量 `memory_total_budget_chars` 超限触发压实(LLM 合并同类/删取代/按优先级从低到高归档/剪枝,redline 永不压);config 集中。
- **验证**:tests/test_memory.py 10 项 + 全量 164 项绿(4 error 为 test_guard 环境)。
