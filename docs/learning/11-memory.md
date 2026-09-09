# 11 记忆系统:会话回源 + 跨会话沉淀 + 客服画像(D52)

## 这一阶段解决什么问题

坐席工作台(D32)场景下,三个真实痛点:
1. **长会话压缩丢细节**:多次 `compaction`(阶段C 保尾压头)后,早期**过程原文**(早期 user_message 全文、早期检索 chunk、旧对话逐字)被逐层摘要模糊化;坐席同会话回问"前面那个 XX"时模型答不上。
2. **跨会话无沉淀**:换会话后,已查明的知识、给过的口径、踩过的坑丢了,导致**重复检索、口径不一致**。
3. **客服无个人偏好**:每个坐席回答风格、常用话术、注意事项无法保持一致。

## 对应 dsh 源码

dsh 本体**没有独立的"记忆"模块**(它的长记忆靠 context/压缩 + 检索工具)。本项目是扩展,参照两套外部机制:
- **trae**:四通道(user_profile / project_memory / topics.md / session jsonl),按"作用范围"分层、逐级蒸馏。
- **workbuddy**:三层(云端画像 / 跨会话检索 conversation_search / 本地用户级+项目级),L1 只读、L2/L3 agent 主动写。

本项目**砍掉它们的散文件形态**(.`.md`/`.jsonl`),遵守黄金法则落 SQLite;并砍掉 **workbuddy 的云端画像与全局 conversation_search**(单机、无云端;座席场景跨客户检索=噪音+污染+PII)。

## 设计要点(为什么这么做)

- **三层定位驱动**(先定职责再推导记什么):
  | 层 | 定位 | 职责 |
  |---|---|---|
  | L1 会话级 | 防遗忘 | 本会话回源(补压缩丢的细节) |
  | L2 跨会话 | 沉淀 | 换会话后复用(知识/口径/经验) |
  | L3 画像 | 偏好 | 客服可编辑、常驻注入 |
- **默认不记**:知识库覆盖的、单次查询答案一律不记;只记"对未来回答有增量价值"(纠错/知识库外事实/缺口/显式记住)。否则记忆被知识查询淹没(=知识库劣质副本)。
- **会话 id 由系统注入,不暴露给模型**:检索工具 schema 无会话字段;`_run_tool` 把当前 `session_id` 传给 handler(用 `_handler_accepts_session` 兼容,不接受则不传)。模型描述"找什么",系统决定"在哪个会话找"——结构上杜绝跨会话/越权/幻觉 id。
- **不做全局 conversation_search**:D32 跨客户场景下,全局检索捞到跨客户混合噪音。
- **判别"只返回被压缩掉的原文"**:handler 用 `build_history` 得到"已进上下文"的 seq 集合并**排除**,只返回被 compaction 影子/窗口裁剪掉的早前事件——保证检索结果的**增量价值**,不浪费 token。
- **独立上限**:`session_history_search` 单独计数(`max_history_search_per_turn`),不占知识检索收敛(`max_retrieve_per_turn`);只在"本轮还调知识检索类工具"且达知识上限时才强制收尾(`_has_kw_tool`)。

## Python 实现(关键片段)

核心注入 `_run_tool`:
```python
def _run_tool(self, name, args, start_idx=0, session_id=None):
    ...
    if _handler_accepts_session(tool["handler"]):
        raw = tool["handler"](args, start_idx, session_id=session_id)
    else:
        raw = tool["handler"](args, start_idx)   # 不接受的 handler 保持旧调用
```

业务层 `_make_history_handler`(app/businesses/insurance.py):
```python
def _make_history_handler(store, cfg):
    def handler(args, start_idx=0, session_id=None):
        evts = store.read(session_id)                        # 只用系统注入的当前会话
        vis = {m.get("seq") for m in build_history(store, session_id) if m.get("seq") is not None}
        cand = [e for e in evts if e["type"] in ("user_message","assistant_message") and e.get("seq") not in vis]
        # 按 query 特征重叠打分取 top;截断;返回 【本会话早前记录】帧
        ...
    return handler
```

## 验收测试

`tests/test_history_search.py` 8 项:
- `test_no_store_or_session`:store 缺/无 session_id → 返回不可用
- `test_retrieves_shadowed_excludes_visible`:检索到被 compaction 影子的早前原文,排除仍在上下文的(增量价值)
- `test_no_match`:query 无命中 → "本会话无相关早前记录"
- `test_does_not_cross_session`:注入 A 的 id 只查 A,绝不返回 B 的内容(结构性杜绝跨会话)
- `test_truncation`:超长原文截断
- `QueryFeaturesTest`/`TextOfBlocksTest`:纯函数

全量 `python -m unittest discover -s tests`:**154 项绿**(新增 8;4 error 为 test_guard 的 starlette TestClient `app=` 环境兼容,与本轮无关)。

## 手动测试:前端怎么玩

暂无前端操作(本期为后端工具)。可通过回放/真机:坐席问一个被压缩过的早前内容,模型若判定回指,应先写叙述再调 `session_history_search`。

## 你学到了什么

- **记忆按"定位"设计,不按"分类"堆**:先定职责,再推导该记什么、谁来写、何时召回。
- **状态 vs 历史分离**:可变状态(可 UPDATE 的表)与不可变历史(events append-only)并存;记忆变更也写事件(可审计)。
- **会话作用域由系统强制,不信任模型**:id 是执行上下文,由核心注入。
- **克制优先**:默认不记,知识库是事实源,记忆是知识库外沉淀。

## 踩坑记录

- 给所有 handler 统一传 `session_id` 会 TypeError(测试 fake handler / 旧 handler 签名不接受)→ 用 `_handler_accepts_session`(inspect)按能力注入,不破坏旧调用。
- `compaction_summary.shadowed_seqs` 用的是**全局递增 seq**,不是"本会话内第几条"——测试里必须用 `append` 返回值,硬编码 [1] 会在多会话下错位。
