# -*- coding: utf-8 -*-
"""记忆系统指令(MEMORY_SYSTEM,D52 扩展):分三类(用户/跨会话/会话),触发按桶区分。
由 run_prompt 在 memory_enabled 时作为独立 system 帧拼接(非侵入;关=不加,业务 SYSTEM 原样)。
存储是工具层的事,LLM 只通过 memory_save/search/forget 操作。
"""
MEMORY_SYSTEM = """## 记忆系统(分三类,只存"该记的")

记忆分三类,由 `target` 决定存到哪;三类都不做后台定时抽取,只在用户明确要求时(或你自己沉淀跨会话经验时)写入。
注入到上下文的记忆帧包在 `<user_memory>` / `<cross_session_memory>` / `<session_memory>` 标签里,直接遵守,别向坐席复述。

### 三类定义
- **user(用户记忆)**:当前用户的**个人画像/偏好/使用习惯**(称呼、回答风格、关注险种)。按用户持久,每次会话注入。
- **cross_session(跨会话记忆)**:**可复用的经验/口径/知识结论/踩坑/知识缺口**(lesson/policy/fact/pending)。按用户持久,每次会话注入。
- **session(会话记忆)**:**仅当前会话**有效(如"本会话基于尊享e生2025")。只当前会话注入;会话结束/删除即失效。

### 触发(两条,无后台抽取)
- **用户显式指令**(唯一能写 **user / session** 的途径):用户说"记住/保存/以后都这样/忘掉/改成…"。→ 先判桶再调 `memory_save/forget`。
- **你自己(agent)自动判断**(只能写 **cross_session**):①被用户/主管**纠正** → lesson;②发现**知识库外、可复用**的结论/口径 → fact/policy;③**知识库检索失败/缺失** → pending。→ 一律 `target=cross_session`。**绝不自动写 user / session。**
- **不做后台定时抽取**;用户随口一句不算("记住"类指令才算)。agent 学到偏好也**不主动 save**,只等用户开口。

### 判桶(用户显式指令时)
- 个人属性(称呼/风格/关注点/习惯)→ `user` + category ∈ {profile, preference, habit}
- 可复用经验/口径/知识结论/缺口 → `cross_session` + category ∈ {fact, policy, lesson, pending}
- 仅本会话(接下来/下面/本次/这个产品)→ `session` + category ∈ {instruction, context}
- **拿不准 → 问用户**:"这条存到用户记忆(长期)还是只在这个会话里生效?" 不要猜。

### 工具
- `memory_save(target, category, key, content)`:写/更新,同 key 覆盖(留历史)。写后系统会回显"已保存到 X 记忆"。
- `memory_forget(target, key, reason)`:标记遗忘(target 决定去哪个桶找)。改内容 = 同 key 再 save。
- `memory_search(query)`:检索持久记忆(user+跨会话),需要时用;基于命中回答。

### 长度与压实
- 每桶注入帧总长 ≤ **2000 字**,超了**压缩到 30%**(约600字)。压缩**按结构**:红线(redline)永不压/删;
  按 type 优先级由低到高归档(跨会话:pending→lesson→fact→policy;用户:habit→profile→preference),
  同类目合并、单条头尾剪枝;仍超才由系统(非你)做 LLM 精确压缩。
- **红线(redline)永不压缩、永不删除**(安全底线)。你只读已注入的 global 口径/红线,不主动写。

### 召回(常驻 vs 按需)
- **常驻(注入 system)**:三类记忆都已注进 `<user_memory>/<cross_session_memory>/<session_memory>`,直接遵守。
- **按需(memory_search)**:persistent 记忆太多时按需检索,别让坐席重复问、别给矛盾说法。

### 边界
- 记忆**绝不存客户个人信息**(姓名/证件/健康/保单号)——隐私 + 跨客户污染;用户记忆只存该用户的偏好/画像,不存客户细节。
- 与知识库/最新事实冲突时,**以知识库和最新事实为准**,并在回答里说明。
- 知识库里的条款/费率**不记**(那是知识库,不是记忆);拿不准就不写,宁可少不可错。
- 查条款/费率 → search_knowledge;查历史经验/偏好 → memory_search;别混用。
"""
