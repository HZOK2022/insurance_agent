// -*- coding: utf-8 -*-
// 回答诊断:把"模型实际看到的召回块"和"模型说了什么/引了什么"交叉核对,
// 区分【幻觉】(答了但无召回支撑)vs【召回/知识缺口】(需要但未召回)。
import { useEffect, useState } from "react"
import { listSessions, listEvents, type Session, type PEvent } from "./lib/api"

interface Rec { chunk_id: string; section: string; title: string; score?: number; content: string; product_name?: string }
interface Turn {
  n: number; query: string; reason?: string; elapsed_ms?: string
  recs: Rec[];   // 该 turn 喂给模型的检索块(去重)
  answer: string; hasAnswer: boolean; cites: { idx: number; chunk_id: string }[]
}

function flat(blocks: any[]): string {
  return (blocks || []).map((b: any) => (b && (b.t === "ul" || b.t === "ol")) ? ((b.items || []).join("\n")) : ((b && b.text) || "")).join("\n").trim()
}

function buildTurns(evs: PEvent[]): Turn[] {
  const turns: Turn[] = []
  let cur: Turn | null = null
  const recMap = new Map<string, Rec>()
  let ansBlocks: any[] = []
  let ansCites: { idx: number; chunk_id: string }[] = []
  let streamText = ""   // 流式正文(中断无最终 messages 时兜底)
  const finalize = () => {
    if (!cur) return
    cur.recs = Array.from(recMap.values())
    cur.answer = flat(ansBlocks) || streamText.trim()
    cur.cites = ansCites
    // 只要有"正文/被中断的流式",也算可诊断(常是问题现场)
    cur.hasAnswer = cur.hasAnswer || cur.answer.length > 0
  }
  evs.forEach((e) => {
    const p = e.payload || {}
    if (e.type === "turn_start") {
      if (cur) { finalize(); turns.push(cur) }
      recMap.clear(); ansBlocks = []; ansCites = []; streamText = ""
      cur = { n: turns.length + 1, query: "", recs: [], answer: "", hasAnswer: false, cites: [] }
    } else if (cur && e.type === "user_message") {
      cur.query = p.text || cur.query
    } else if (cur && e.type === "assistant_chunk") {
      const d = p.delta || ""
      if (p.kind === "text") streamText += d
    } else if (cur && e.type === "retrieval") {
      ;(p.chunks || []).forEach((c: any) => { if (c && c.chunk_id && !recMap.has(c.chunk_id)) recMap.set(c.chunk_id, c) })
    } else if (cur && e.type === "assistant_message") {
      ansBlocks = p.blocks || []; ansCites = p.citations || []; cur.hasAnswer = true
    } else if (cur && e.type === "turn_end") {
      cur.reason = p.reason; cur.elapsed_ms = p.elapsed_ms != null ? String(p.elapsed_ms) + "ms" : ""
      finalize()
    }
  })
  if (cur) { finalize(); turns.push(cur) }
  return turns.filter((t) => t.hasAnswer)
}

// 交叉核对:每个引用 → 是否在召回集;以及"答了却几乎没引用/零召回"
function classify(t: Turn) {
  const inSet = new Set(t.recs.map((r) => r.chunk_id))
  const ok = t.cites.filter((c) => inSet.has(c.chunk_id)).length
  const bad = t.cites.filter((c) => !inSet.has(c.chunk_id)).length
  const noCite = t.answer.length > 0 && t.cites.length === 0 && t.recs.length > 0
  const noRecall = t.recs.length === 0 && t.answer.length > 0
  const silent = t.recs.length === 0 && t.answer.length === 0
  return { ok, bad, noCite, noRecall, silent }
}

function Summary({ t }: { t: Turn }) {
  const c = classify(t)
  const chips: { k: string; cls: string; label: string }[] = []
  if (c.ok > 0) chips.push({ k: "ok", cls: "ok", label: `${c.ok} 有据` })
  if (c.bad > 0) chips.push({ k: "bad", cls: "bad", label: `${c.bad} 引用但召回集没有` })
  if (c.noCite) chips.push({ k: "nocite", cls: "warn", label: "答了但全程无引用(嫌疑:未用检索/幻觉)" })
  if (c.noRecall) chips.push({ k: "norecall", cls: "warn", label: "零召回却给回答(嫌疑:缺口+幻觉)" })
  if (c.silent) chips.push({ k: "silent", cls: "ok", label: "零召回+未作答(行为正确/诚实)" })
  return <div className="diag-chips">{chips.map((x) => <span key={x.k} className={"diag-chip " + x.cls}>{x.label}</span>)}</div>
}

export default function DiagnoseView({ onBack }: { onBack?: () => void }) {
  const [sessions, setSessions] = useState<Session[]>([])
  const [sid, setSid] = useState("")
  const [turns, setTurns] = useState<Turn[]>([])
  const [open, setOpen] = useState<number | null>(null)

  useEffect(() => { listSessions().then(setSessions).catch(() => {}) }, [])

  useEffect(() => {
    if (!sid) { setTurns([]); setOpen(null); return }
    listEvents(sid).then((evs) => {
      const ts = buildTurns(evs)
      setTurns(ts)
      setOpen(ts.length ? ts[0].n : null)   // 自动展开第一轮
    }).catch(() => setTurns([]))
  }, [sid])

  return (
    <div className="kb-manager">
      <div className="kb-head">
        <div className="kb-head-left">
          {onBack && <button className="kb-back-btn" onClick={onBack}><svg className="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="15 18 9 12 15 6"/></svg> 返回对话</button>}
          <span className="kb-title">回答诊断</span>
          <span className="kb-subtitle">选会话 + 轮次,核对「模型看到的召回块 ↔ 回答/引用」,区分幻觉 vs 召回缺口</span>
        </div>
        <div className="kb-head-right">
          <select className="kb-form-input" style={{ width: 320 }} value={sid} onChange={(e) => { setSid(e.target.value); setOpen(null) }}>
            <option value="">选择会话…</option>
            {sessions.map((s) => <option key={s.id} value={s.id}>{s.title}</option>)}
          </select>
        </div>
      </div>
      <div className="kb-body">
        {!sid && <div className="kb-empty">先在右上角选择一个会话,再点轮次展开诊断</div>}
        {sid && turns.length === 0 && <div className="kb-empty">该会话没有可诊断的回答轮次(可能只是寒暄/未生成回答,换一个会话试试)</div>}
        {turns.length > 0 && (
          <div className="diag-list">
            {turns.map((t) => (
              <div key={t.n} className="diag-turn">
                <div className="diag-turn-head" onClick={() => setOpen(open === t.n ? null : t.n)}>
                  <span className="diag-turn-title">第 {t.n} 轮</span>
                  <span className="diag-turn-query">{t.query || "(无问题文本)"}</span>
                  {t.reason && <span className="diag-turn-meta">reason={t.reason}{t.elapsed_ms ? " · " + t.elapsed_ms : ""}</span>}
                  <span className="diag-turn-chev">{open === t.n ? "▾" : "▸"}</span>
                </div>
                {open === t.n && (
                  <div className="diag-turn-body">
                    <Summary t={t} />
                    <div className="diag-sec">
                      <div className="diag-sec-label">① 模型看到(该轮撤回 {t.recs.length} 块)</div>
                      <div className="diag-recs">
                        {t.recs.length === 0 && <div className="diag-none">本轮零召回(检索未取到内容)</div>}
                        {t.recs.map((r) => (
                          <div key={r.chunk_id} className="diag-rec">
                            <span className="kb-tag">{r.chunk_id}</span>
                            {r.section && <span className="kb-tag">{r.section}</span>}
                            {r.title && r.title !== r.section && <span className="kb-tag">{r.title}</span>}
                            <div className="diag-rec-content">{r.content}</div>
                          </div>
                        ))}
                      </div>
                    </div>
                    <div className="diag-sec">
                      <div className="diag-sec-label">② 模型回答(引用 {t.cites.length} 处)</div>
                      <div className="diag-ans">{t.answer || "(回答为空)"}</div>
                    </div>
                    <div className="diag-sec">
                      <div className="diag-sec-label">③ 引用 ↔ 召回核对</div>
                      <div className="diag-cites">
                        {t.cites.length === 0 && <div className="diag-none">无引用(见上方嫌疑判定)</div>}
                        {t.cites.map((c) => {
                          const inSet = t.recs.some((r) => r.chunk_id === c.chunk_id)
                          return (
                            <div key={c.idx} className={"diag-cite " + (inSet ? "ok" : "bad")}>
                              <span className="kb-tag">[{c.idx}]</span>
                              <span className="kb-tag">{c.chunk_id}</span>
                              <span>{inSet ? "✅ 该块在召回集中(有据)" : "⚠ 该块不在召回集中(引用了未喂给模型的块)"}</span>
                            </div>
                          )
                        })}
                      </div>
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
