// -*- coding: utf-8 -*-
// 观测大盘(对标 Langfuse 的 trace explorer + 大盘):把 /api/metrics(全局聚合)
// + /api/observability(按会话明细) + /api/metrics/timeseries(时间序列走势)
// 渲染成一张可看的监控页。从用户菜单「观测」进入。
import { useEffect, useState } from "react"
import { getMetrics, getObservability, getTimeseries, getAnomalies, type GlobalMetrics, type TimeseriesBucket, type AnomalyResp } from "./lib/api"

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
const fmtBucket = (b: string, gran: string): string => {
  // '2026-09-06T15:00:00+00:00' → day:'09-06'  hour:'09-06 15:00'
  const d = b.slice(0, 10).slice(5) // MM-DD
  return gran === "day" ? d : d + " " + b.slice(11, 16)
}

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

// 单系列迷你走势图(纯 SVG,无依赖):label 轴标签,points[{x, v}],unit 单位
function SparkLine({ label, points, unit, gran }: { label: string; points: { x: string; v: number }[]; unit: string; gran: string }) {
  if (!points.length) return null
  const W = 620, H = 170, padL = 50, padR = 12, padT = 14, padB = 30
  const iw = W - padL - padR, ih = H - padT - padB
  const max = Math.max(...points.map((p) => p.v), 1)
  const yMax = max * 1.06
  const xs = points.map((_, i) => padL + (points.length === 1 ? iw / 2 : (i / (points.length - 1)) * iw))
  const ys = points.map((p) => padT + ih - (p.v / yMax) * ih)
  const path = xs.map((x, i) => (i ? "L" : "M") + x.toFixed(1) + "," + ys[i].toFixed(1)).join(" ")
  const gridLv = [0, 0.25, 0.5, 0.75, 1]
  const xTick = (i: number) => {
    const n = points.length
    if (n <= 2) return i === 0 || i === n - 1
    if (n <= 8) return i % Math.ceil(n / 4) === 0
    return i % Math.ceil(n / 6) === 0 || i === n - 1
  }
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="mon-ts-svg" role="img" aria-label={label}>
      <text x={padL} y={padT - 2} className="mon-ts-title">{label} · {unit}</text>
      {gridLv.map((t) => {
        const y = padT + (1 - t) * ih
        return (
          <g key={t}>
            <line x1={padL} y1={y} x2={W - padR} y2={y} className="mon-ts-grid" />
            <text x={padL - 5} y={y + 3} textAnchor="end" className="mon-ts-ylab">{String(Math.round(t * yMax))}</text>
          </g>
        )
      })}
      {xs.map((x, i) =>
        xTick(i) ? <text key={i} x={x} y={H - 8} textAnchor="middle" className="mon-ts-xlab">{fmtBucket(points[i].x, gran)}</text> : null
      )}
      <path d={path} className="mon-ts-line" fill="none" />
      {xs.map((x, i) => <circle key={i} cx={x} cy={ys[i]} r={2.2} className="mon-ts-dot" />)}
    </svg>
  )
}

export default function MonitorView({ onOpenSession, onOpenSessionTrace, onBack }: { onOpenSession: (sid: string) => void; onOpenSessionTrace?: (sid: string, traceId?: number) => void; onBack: () => void }) {
  const [m, setM] = useState<GlobalMetrics | null>(null)
  const [rows, setRows] = useState<any[]>([])
  const [ts, setTs] = useState<TimeseriesBucket[]>([])
  const [gran, setGran] = useState<"day" | "hour">("day")
  const [anom, setAnom] = useState<AnomalyResp | null>(null)
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

  useEffect(() => {
    let alive = true
    getTimeseries(gran).then((r) => { if (alive) setTs(r?.series || []) }).catch(() => { if (alive) setTs([]) })
    return () => { alive = false }
  }, [gran, tick])

  useEffect(() => {
    let alive = true
    getAnomalies().then((r) => { if (alive) setAnom(r) }).catch(() => { if (alive) setAnom(null) })
    return () => { alive = false }
  }, [tick])

  const hasCost = ts.some((b) => b.cost != null)
  const latPts = ts.map((b) => ({ x: b.bucket, v: b.p95_latency_ms ?? b.avg_latency_ms ?? 0 }))
  const metPts = ts.map((b) => ({ x: b.bucket, v: hasCost ? (b.cost ?? 0) : b.total_tokens }))
  const activeCats = anom ? Object.entries(anom.categories).filter(([, c]) => c.count > 0) : []

  return (
    <div className="mon-view">
      <div className="mon-head">
        <button className="mon-back" onClick={onBack}>← 返回对话</button>
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
            {card((m.retrieval.total > 0 ? m.retrieval.no_hits / m.retrieval.total : 0) >= TH.empty ? "bad" : "ok", "检索空结果率",
              m.retrieval.total ? ((m.retrieval.no_hits / m.retrieval.total) * 100).toFixed(1) + "%" : "—",
              fmt(m.retrieval.no_hits) + "/" + fmt(m.retrieval.total) + " 空")}
            {card((m.retrieval.total > 0 ? m.retrieval.low_conf / m.retrieval.total : 0) >= TH.lowconf ? "bad" : "ok", "检索低置信率",
              m.retrieval.total ? ((m.retrieval.low_conf / m.retrieval.total) * 100).toFixed(1) + "%" : "—",
              fmt(m.retrieval.low_conf) + "/" + fmt(m.retrieval.total) + " 低置信")}
            {card("ok", "引用率", m.citations.assistant ? ((m.citations.with_cite / m.citations.assistant) * 100).toFixed(0) + "%" : "—",
              fmt(m.citations.with_cite) + "/" + fmt(m.citations.assistant) + " 带引用")}
            {card(m.retries >= TH.retry ? "warn" : "ok", "LLM重试", fmt(m.retries), "依赖抖信号")}
            {card(m.degradations >= TH.degrade ? "bad" : "ok", "依赖降级", fmt(m.degradations), "向量库挂(知识检索降级)")}
            {card(m.guard_triggered >= TH.guard ? "bad" : "ok", "护栏拦截", fmt(m.guard_triggered), "注入/PII 等")}
            {card(m.tool_failures >= TH.toolfail ? "warn" : "ok", "工具失败", fmt(m.tool_failures), "tool_result ok=False")}
          </div>

          {/* 生产异常定位:坏轮按主因分类 + trace 直达 + 检索→引用漏斗 */}
          {anom && (
            <>
              <div className="mon-sec-head">生产异常定位
                <span className="mon-ts-note">共 {anom.summary.total_turns} 轮 · 异常 {anom.summary.anomalies} 轮</span>
              </div>
              <div className="mon-anom-sum">
                <span>有检索 {fmt(anom.funnel.with_retrieval_turns)} 轮</span>
                <span>引用率 {anom.funnel.cited_rate != null ? (anom.funnel.cited_rate * 100).toFixed(0) + "%" : "—"}</span>
                <span>检索/引用 {fmtTok(anom.funnel.retrieval_total)}/{fmtTok(anom.funnel.cited_total)}</span>
              </div>
              <div className="mon-anom-grid">
                {activeCats.map(([k, c]) => (
                  <div key={k} className="mon-anom-card">
                    <div className="mon-anom-cat"><b>{c.label}</b> · {c.count} 轮</div>
                    {(c.hints || []).slice(0, 2).map((h, i) => <div key={i} className="mon-anom-hint">· {h}</div>)}
                    {(c.samples || []).slice(0, 4).map((s) => (
                      <button key={s.session_id + "#" + s.trace_id} className="kb-tag mon-trace"
                        title={"会话 " + s.session_id.slice(0, 12) + (s.trace_id != null ? " · 轮级 trace #" + s.trace_id : "") + " → 直达该轮"}
                        onClick={() => onOpenSessionTrace ? onOpenSessionTrace(s.session_id, s.trace_id) : onOpenSession(s.session_id)}>
                        {s.session_id.slice(0, 6)}{s.trace_id != null ? "·#" + s.trace_id : ""}
                      </button>
                    ))}
                  </div>
                ))}
              </div>
            </>
          )}

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

          {/* 时间序列走势(成本/延迟/token) */}
          <div className="mon-sec-head">时间序列走势 {gran === "day" ? "按天" : "按小时"}
            <span className="mon-ts-toggle">
              <button className={"audit-btn" + (gran === "day" ? " mon-ts-on" : "")} onClick={() => setGran("day")}>天</button>
              <button className={"audit-btn" + (gran === "hour" ? " mon-ts-on" : "")} onClick={() => setGran("hour")}>小时</button>
            </span>
            {!hasCost && <span className="mon-ts-note">未配单价,成本线以 token 替代</span>}
          </div>
          {ts.length ? (
            <div className="mon-ts">
              <SparkLine label="延迟 P95(毫秒)" points={latPts} unit="ms" gran={gran} />
              <SparkLine label={hasCost ? "成本(美元)" : "总 token"} points={metPts} unit={hasCost ? "$" : "tok"} gran={gran} />
            </div>
          ) : <div className="mon-ts-empty">暂无时间序列数据</div>}

          {/* 坏例所在会话(点进会话轨迹;会话内的每一轮各有 trace #) */}
          {m.samples && (m.samples.error_turns?.length || m.samples.tool_failures?.length || m.samples.degradations?.length) ? (
            <>
              <div className="mon-sec-head">坏例所在会话(点进会话轨迹;轮级在轨迹内看 trace #)</div>
              <div className="mon-tags">
                {(m.samples.error_turns || []).map((id) => <button key={"e" + id} className="kb-tag mon-trace" onClick={() => onOpenSession(id)} title={"会话(窗口)id " + id}>错误 · {id.slice(0, 8)}</button>)}
                {(m.samples.tool_failures || []).map((id) => <button key={"t" + id} className="kb-tag mon-trace" onClick={() => onOpenSession(id)} title={"会话(窗口)id " + id}>工具失败 · {id.slice(0, 8)}</button>)}
                {(m.samples.degradations || []).map((id) => <button key={"d" + id} className="kb-tag mon-trace" onClick={() => onOpenSession(id)} title={"会话(窗口)id " + id}>降级 · {id.slice(0, 8)}</button>)}
                {(m.samples.retrieval_low_conf || []).map((id) => <button key={"r" + id} className="kb-tag mon-trace" onClick={() => onOpenSession(id)} title={"会话(窗口)id " + id}>低置信 · {id.slice(0, 8)}</button>)}
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
          <div key={i} className="mon-tr" onClick={() => onOpenSession(r.session_id)} title={"会话(窗口)id " + r.session_id + " · " + fmt(r.turns) + " 轮,点击查看该会话各轮 trace(轮级 trace # 在其轨迹内)"}>
            <span className="mon-td-title">{(r.title || r.session_id || "—").slice(0, 24)}</span>
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