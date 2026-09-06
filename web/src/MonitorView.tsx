// -*- coding: utf-8 -*-
// 观测大盘(对标 Langfuse 的 trace explorer + 大盘):把 /api/metrics(全局聚合)
// + /api/observability(按会话明细)渲染成一张可看的监控页。从用户菜单「观测」进入。
import { useEffect, useState } from "react"
import { getMetrics, getObservability, type GlobalMetrics } from "./lib/api"

const fmt = (n?: number | null): string => (n == null ? "—" : String(n))
const fmtTok = (n?: number | null): string => {
  if (n == null) return "—"
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M"
  if (n >= 1000) return (n / 1000).toFixed(1) + "K"
  return String(n)
}
const fmtDur = (ms?: number | null): string => {
  if (ms == null) return "—"
  if (ms < 1000) return ms + "ms"
  const s = ms / 1000
  return (s < 10 ? String(Math.round(s * 10) / 10) : String(Math.round(s))) + "s"
}
const fmtCost = (c?: number | null): string => (c == null ? "—" : "$" + Number(c).toFixed(4))

// 阈值(与 scripts/check_metrics.py 默认一致):超了标红
const TH = { errRate: 0.05, errCount: 1, p95: 20000, tok: 5_000_000, empty: 0.10, lowconf: 0.30, retry: 5, degrade: 1, guard: 1, toolfail: 5 }

function card(cls: "ok" | "warn" | "bad", label: string, val: string, hint?: string) {
  return (
    <div className={"mon-card " + cls}>
      <div className="mon-card-label">{label}</div>
      <div className="mon-card-val">{val}</div>
      {hint ? <div className="mon-card-hint">{hint}</div> : null}
    </div>
  )
}

export default function MonitorView({ onOpenSession }: { onOpenSession: (sid: string) => void }) {
  const [m, setM] = useState<GlobalMetrics | null>(null)
  const [rows, setRows] = useState<any[]>([])
  const [err, setErr] = useState("")
  const [tick, setTick] = useState(0)

  useEffect(() => {
    let alive = true
    ;(async () => {
      try {
        const [gm, ob] = await Promise.all([getMetrics(), getObservability()])
        if (!alive) return
        setM(gm); setRows(ob?.per_session || []); setErr("")
      } catch (e: any) {
        if (alive) setErr("加载观测失败: " + (e?.message || e))
      }
    })()
    return () => { alive = false }
  }, [tick])

  return (
    <div className="mon-view">
      <div className="mon-head">
        <span className="mon-title">观测总览</span>
        <span className="mon-sub">{m ? `共 ${fmt(m.turns.total)} 轮 · ${fmt(rows.length)} 会话` : "加载中…"}</span>
        <button className="audit-btn" onClick={() => setTick((x) => x + 1)}>刷新</button>
      </div>
      {err && <div className="hint">{err}</div>}

      {/* 全局卡片(超阈值标红) */}
      {m && (
        <>
          <div className="mon-grid">
            {card(m.turns.error >= TH.errCount ? "bad" : "ok", "错误轮", fmt(m.turns.error) + "/" + fmt(m.turns.total),
              m.turns.error_rate != null ? "率 " + (m.turns.error_rate * 100).toFixed(1) + "%" : "")}
            {card(Number(m.latency_ms.p95) >= TH.p95 ? "bad" : "ok", "延迟P95", fmtDur(m.latency_ms.p95), "avg " + fmtDur(m.latency_ms.avg))}
            {card((m.tokens.prompt + m.tokens.completion) >= TH.tok ? "warn" : "ok", "累计token", fmtTok(m.tokens.prompt + m.tokens.completion),
              "prompt " + fmtTok(m.tokens.prompt) + " / comp " + fmtTok(m.tokens.completion))}
            {card("ok", "成本", fmtCost(m.tokens.cost), m.tokens.cost == null ? "未配单价" : "")}
            {card((m.retrieval.total > 0 && m.retrieval.no_hits / m.retrieval.total) >= TH.empty ? "bad" : "ok", "检索空结果率",
              m.retrieval.total ? ((m.retrieval.no_hits / m.retrieval.total) * 100).toFixed(1) + "%" : "—",
              fmt(m.retrieval.no_hits) + "/" + fmt(m.retrieval.total) + " 空")}
            {card((m.retrieval.total > 0 && m.retrieval.low_conf / m.retrieval.total) >= TH.lowconf ? "bad" : "ok", "检索低置信率",
              m.retrieval.total ? ((m.retrieval.low_conf / m.retrieval.total) * 100).toFixed(1) + "%" : "—",
              fmt(m.retrieval.low_conf) + "/" + fmt(m.retrieval.total) + " 低置信")}
            {card("ok", "引用率", m.citations.assistant ? ((m.citations.with_cite / m.citations.assistant) * 100).toFixed(0) + "%" : "—",
              fmt(m.citations.with_cite) + "/" + fmt(m.citations.assistant) + " 带引用")}
            {card(m.retries >= TH.retry ? "warn" : "ok", "LLM重试", fmt(m.retries), "依赖抖信号")}
            {card(m.degradations >= TH.degrade ? "bad" : "ok", "依赖降级", fmt(m.degradations), "向量库挂(知识检索降级)")}
            {card(m.guard_triggered >= TH.guard ? "bad" : "ok", "护栏拦截", fmt(m.guard_triggered), "注入/PII 等")}
            {card(m.tool_failures >= TH.toolfail ? "warn" : "ok", "工具失败", fmt(m.tool_failures), "tool_result ok=False")}
          </div>

          {/* 按模型 */}
          {Object.keys(m.models || {}).length > 0 && (
            <>
              <div className="mon-sec-head">按模型(tokens 用量)</div>
              <div className="mon-tags">
                {Object.entries(m.models).map(([mod, cnt]) => (
                  <span key={mod} className="kb-tag">{mod} · {fmtTok(cnt)}</span>
                ))}
              </div>
            </>
          )}

          {/* 坏例 trace_id 样本(点 trace_id 可直接进该会话轨迹) */}
          {m.samples && (m.samples.error_turns?.length || m.samples.tool_failures?.length || m.samples.degradations?.length) ? (
            <>
              <div className="mon-sec-head">坏例 trace_id(点进对应会话轨迹)</div>
              <div className="mon-tags">
                {(m.samples.error_turns || []).map((id) => <button key={"e" + id} className="kb-tag mon-trace" onClick={() => onOpenSession(id)} title={id}>错误 · {id.slice(0, 8)}</button>)}
                {(m.samples.tool_failures || []).map((id) => <button key={"t" + id} className="kb-tag mon-trace" onClick={() => onOpenSession(id)} title={id}>工具失败 · {id.slice(0, 8)}</button>)}
                {(m.samples.degradations || []).map((id) => <button key={"d" + id} className="kb-tag mon-trace" onClick={() => onOpenSession(id)} title={id}>降级 · {id.slice(0, 8)}</button>)}
                {(m.samples.retrieval_low_conf || []).map((id) => <button key={"r" + id} className="kb-tag mon-trace" onClick={() => onOpenSession(id)} title={id}>低置信 · {id.slice(0, 8)}</button>)}
              </div>
            </>
          ) : null}
        </>
      )}

      {/* 按会话明细 */}
      <div className="mon-sec-head">按会话</div>
      {rows.length === 0 && !err && <div className="hint">暂无会话数据(先跑一轮问答再来)</div>}
      <div className="mon-table">
        <div className="mon-tr mon-tr-head"><span>会话</span><span>轮</span><span>token</span><span>成本</span><span>错误</span><span>重试</span><span>平均TTFT</span></div>
        {rows.map((r, i) => (
          <div key={i} className="mon-tr" onClick={() => onOpenSession(r.session_id)} title={"trace " + r.session_id}>
            <span className="mon-td-title">{(r.title || r.session_id || "—").slice(0, 24)}{r.title ? "" : ""}</span>
            <span>{fmt(r.turns)}</span>
            <span>{fmtTok(r.total_tokens)}</span>
            <span>{fmtCost(r.cost)}</span>
            <span className={r.errors > 0 ? "mon-bad" : ""}>{fmt(r.errors)}</span>
            <span>{fmt(r.retries)}</span>
            <span>{fmtDur(r.avg_ttft_ms)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
