# -*- coding: utf-8 -*-
"""上下文压缩(compaction):剪枝 -> 保尾压头 -> LLM 摘要替换头部。

面向"座席工作台"场景(DECISIONS D32):摘要蕴含"工作台已查明知识/口径/红线/未决"
这套工作态,而非某位客户的会话叙述。机制照 dsh keep-tail-press-head;
摘要内容用 §8.3 压缩指令(见 docs/context-management.md §8.3)。

设计要点:
- 先剪枝(超预算的工具结果保头尾砍中间,无模型) -> 重测 -> 低于阈值则跳过摘要。
- 保尾压头:从尾部往回累加到 retain 预算,单次划分不拆 tool_call/result 对。
- 摘要由 LLM 按 COMPACTION_INSTRUCTION 生成;帧包 CHECKPOINT_PREAMBLE + <compacted-summary>。
- 摘要必须严格短于被替换区间,否则视为压缩失败(交由调用方回退)。
- 本模块为纯函数/常量,便于单测;编排(事件/落库)在 AgentLoop 里。
"""
from __future__ import annotations

from typing import Any, Callable, Iterable

from app.utils.text import estimate_tokens, prune_tool_content

# ---- §8.3 座席工作台版压缩指令(见 docs/context-management.md §8.3)----

COMPACTION_INSTRUCTION = """你现在是保险销售座席知识检索工作台的上下文压缩引擎。这台工作台被坐席用来边回客户边查资料、找好的回复;一位坐席同时服务几十上百位客户,查询彼此独立、通常跨多位客户,而且无法区分当前是哪位客户。请把上方这段**座席的连续查询**浓缩成结构化checkpoint,让工作台在后续查询中:不重复检索已查明的知识、不给与前面口径不一致的答案、保持合规红线一致,并能承接上次没答全的问题。

严格按下面的结构输出:每个小节都保留、按顺序;用简短要点;某节没内容就写"(无)",不要删节。不要试图把上下文重构成"某一位客户的一次完整会话"——多数查询并不属于同一位客户。

## 已查明的知识与口径
- [已查过的产品/条款/责任、年龄与保额规则及结论;每条带产品版本、条款名、引用编号[n]、关键数字。作用:后续同类查询直接复用,不重复检索,如要改口须显式说明]

## 已给出的回复与话术
- [已给坐席的参考回复/话术要点,含引用与数字,保持后续口径一致]

## 未决与可追问点
- [没答全的、坐席很可能追问的(如年龄下一档、保额往上加、病种范围)、答应要补的、需转人工/审批的;以及为什么没答(缺资料/超范围/需人工)]

## 客户上下文(坐席口述,尽力而为)
- [坐席提问时附带的客户描述("有个怎么样的客户");标注为"坐席口述、可能跨多位客户、非唯一标识",只用于贴近当下那次提问。若坐席直接查事实未描述客户,写"(无)"]

## 风险与合规红线
- [该拒答/转人工/需提示健康与如实告知的点;敏感项(既往症/健康状况/职业/年龄/区域/收入/投保人关系);可用与禁忌的话术;监管与使用限制]

## 当前查询
- [checkpoint 时刻:坐席最近一次问查了什么,检索/生成到哪一步,命中/用了哪些条款]

## 下一步
- [紧接坐席最近一次查询的单一下一步:补某产品细节、补年龄/保额边界、给话术、提示转人工,或给出建议;没有则"(无)"]

## 关键上下文
- [取舍与理由(尤其为什么拒答/转人工/选某产品或口径);已用产品与版本;坐席的检索习惯与偏好;未决合规点;继续所需数据;不变式相关:SQLite 事实源、引用绑定版本、答复可追溯]

规则:
- 用简体中文写简洁记录,保留关键数字、产品名、条款名、引用编号[n]与客户/坐席原话(措辞重要时逐字)。
- 忠实记录坐席的纠正与反馈,以及工作台之前的改口/更正。
- 不要提及本次压缩或上下文被压缩。
- 只输出 checkpoint 文本,不要调用工具或做其他操作。
- 若已有 <compacted-summary> 旧块,视为上一个 checkpoint:不照抄;保留仍为真的事实、丢弃过期的,把新信息合并成同一结构。"""

CHECKPOINT_PREAMBLE = (
    "这是自动生成的检查点,浓缩了之前一段对话以释放上下文。把捕获的上下文当作既定背景,\n"
    "直接在其上继续,不要复述。直接从后面的消息继续任务,不要提及本检查点。"
)


def frame_summary(summary: str) -> str:
    """把结构化的 checkpoint 摘要封装成"帧包"(照 dsh frameSummary)。"""
    return CHECKPOINT_PREAMBLE + "\n\n<compacted-summary>\n" + summary.strip() + "\n</compacted-summary>"


def prune_tool_messages(conversation, max_chars, head_chars, tail_chars):
    """对 role='tool' 的超长 content 做保头尾剪枝。返回 (新conversation, 剪枝元数据列表)。

    剪枝元数据每一项:{"index": 被剪消息下标, "chars_removed": 去掉的字符数}。
    仅剪"喂模型的 content";reference 等其它字段不动。无剪枝时元数据为空。
    """
    if max_chars <= 0:
        return conversation, []
    out = []
    pruned = []
    for i, m in enumerate(conversation):
        if (m.get("role") == "tool" and isinstance(m.get("content"), str)):
            p = prune_tool_content(m["content"], max_chars, head_chars, tail_chars)
            if p is not None:
                pruned.append({"index": i, "chars_removed": len(m["content"]) - len(p)})
                out.append({**m, "content": p})
                continue
        out.append(m)
    return out, pruned


def _msg_estimate(m, est):
    return est(str(m.get("content") or ""))


def select_keep_tail(conversation, retain_budget, est=estimate_tokens):
    """从尾部往回累加到 retain 预算,返回"保留尾"的起始下标 k(conversation[k:] 为尾)。

    规则(照 dsh):
    - 从最后一个消息往回加,加到超过 retain_budget 即停;宁可少留也不超预算。
    - 边界落在 role='tool' 上时,回退到它所属的 assistant(tool_calls),不拆工具对。
    - 返回 k;若整段都不超预算,返回 0(无头部可压);头为空则调用方不做。
    """
    n = len(conversation)
    if n == 0:
        return 0
    tok = 0
    k = n
    for i in range(n - 1, -1, -1):
        t = _msg_estimate(conversation[i], est)
        if tok + t > retain_budget:
            break
        tok += t
        k = i
    # 兜底:至少保留最后一个消息(避免当前问题被当成头部压掉)
    if k >= n:
        k = n - 1
    # 不拆工具对:边界落在 tool 消息 -> 回退到它最近的 assistant(含 tool_calls)
    if 0 < k < n and conversation[k].get("role") == "tool":
        j = k
        while j > 0:
            j -= 1
            if conversation[j].get("role") == "assistant":
                k = j
                break
        else:
            k = 0
    return k


def build_summary_request(system, head):
    """构造"让 LLM 压缩头部"的请求:system + 被压缩的头部消息 + 压缩指令(最后一条 user)。

    需保证消息序列合法(OpenAI 协议):tool 消息必须跟在带 tool_calls 的 assistant 之后。
    压缩切分可能把 assistant(tool_calls)与 tool 结果拆到不同侧 → 会构造出孤立 tool 消息,DeepSeek 直接 400。
    这里保留 assistant 的 tool_calls、丢弃孤立/无效 tool 消息,保证合法。"""
    msgs = [{"role": "system", "content": system}]
    tool_window = False   # 最近一条 assistant 是否带 tool_calls(其后的 tool 消息才合法)
    for m in head:
        role = m.get("role") or "user"
        content = str(m.get("content") or "")
        if role == "tool":
            if tool_window and m.get("tool_call_id"):
                msg = {"role": "tool", "content": content or ""}
                msg["tool_call_id"] = m["tool_call_id"]
                if m.get("name"):
                    msg["name"] = m["name"]
                msgs.append(msg)
            continue                      # 孤立/无效 tool 消息丢弃
        if not content and not (role == "assistant" and m.get("tool_calls")):
            continue
        msg = {"role": role, "content": content or ""}
        if role == "assistant" and m.get("tool_calls"):
            msg["tool_calls"] = m["tool_calls"]
            tool_window = True
        else:
            tool_window = False
        msgs.append(msg)
    msgs.append({"role": "user", "content": COMPACTION_INSTRUCTION})
    return msgs


def collect_summary(chunks):
    """从 LLM 流式块里收集最终摘要文本(只取 text;跳过 reasoning/tool/usage)。"""
    parts = []
    for piece in chunks:
        if piece.get("kind") == "text":
            parts.append(piece.get("delta") or "")
    return "".join(parts).strip()


def truncate_summary(summary, max_tokens):
    """把摘要按估计 token 上限截断(防压缩指令产出超长)。"""
    if max_tokens <= 0:
        return summary
    if estimate_tokens(summary) <= max_tokens:
        return summary
    lo, hi = 0, len(summary)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if estimate_tokens(summary[:mid]) <= max_tokens:
            lo = mid
        else:
            hi = mid - 1
    return summary[:lo].rstrip() + "\n[摘要已按上限截断]"
