import { useEffect, useRef, useState } from "react"
import type { ReactNode } from "react"
import { listSessions, createSession, listEvents, deleteSession, renameSession, sendPrompt, type Session, type PEvent, type Citation } from "./lib/api"
import "./App.css"

const SIDEBAR_MIN = 220, SIDEBAR_MAX = 420, SIDEBAR_DEFAULT = 240
const SIDEBAR_COLLAPSED = 56, SIDEBAR_AUTO_COLLAPSE = 1024
const DETAILS_MAX = 520, DETAILS_DEFAULT = 360, CENTER_MIN = 640

function I({ children }: { children: ReactNode }) { return <svg className="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">{children}</svg> }
function clamp(v: number, min: number, max: number) { return Math.min(max, Math.max(min, Math.round(v))) }
function solve(vp: number, side: number, det: number, narrow: boolean) { let s = side === 0 ? SIDEBAR_COLLAPSED : side; let d = det; while (vp - s - d < CENTER_MIN) { if (d > 0) { d = Math.max(0, d - 20) } else if (!narrow && s > SIDEBAR_COLLAPSED) { s = SIDEBAR_COLLAPSED } else { break } } return { sidebar: s, center: Math.max(0, vp - s - d), details: d } }
function ColHandle({ pos, onDrag }: { pos: number; onDrag: (dx: number) => void }) { const start = useRef(0); return (<div className="col-handle" style={{ left: pos - 3 }} onPointerDown={(e) => { e.preventDefault(); e.currentTarget.setPointerCapture(e.pointerId); start.current = e.clientX }} onPointerMove={(e) => { if (e.currentTarget.hasPointerCapture(e.pointerId)) onDrag(e.clientX - start.current) }} onPointerUp={(e) => { if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId) }} />) }

interface Block { t?: string; text?: string; items?: string[] }
// 行模型:每条消息是一个"节点"——user | think(该步推理,交错在工具之间) | tool(工具卡) | answer(最终回答)
interface Msg { id: string; role: "user" | "think" | "text" | "tool" | "answer"; text?: string; reasoning?: string; blocks?: Block[]; citations?: Citation[]; sources?: Source[]; tool?: { name: string; ok: boolean; args?: any; running?: boolean }; time?: string; streaming?: boolean; runMs?: number; ttftMs?: number; tps?: number }
// 照 dsh formatLatencySeconds/formatTokensPerSecond:<10s 一位小数,>=10s 取整;tps >=10 取整
function fmtDur(ms?: number): string { if (ms == null) return ""; if (ms < 1000) return ms + "ms"; const s = ms / 1000; return (s < 10 ? String(Math.round(s * 10) / 10) : String(Math.round(s))) + "秒" }
function fmtTps(v?: number): string { if (v == null) return ""; const c = Math.max(0, v); return (c >= 10 ? String(Math.round(c)) : String(Math.round(c * 10) / 10)) + " tok/s" }
// 上下文用量:~9.9K / ~720K / ~1M(照 dsh)
function fmtCtx(n?: number): string { if (n == null) return "—"; if (n >= 1_000_000) { const v = n / 1_000_000; return "~" + (v >= 10 ? String(Math.round(v)) : String(Math.round(v * 10) / 10)) + "M" } if (n >= 1000) { const v = n / 1000; return "~" + (v >= 10 ? String(Math.round(v)) : String(Math.round(v * 10) / 10)) + "K" } return "~" + String(n) }
// 任务耗时:2m25s / 45s / 800ms
function fmtElapsed(ms?: number): string { if (ms == null) return ""; if (ms < 1000) return ms + "ms"; const s = Math.round(ms / 1000); if (s < 60) return s + "s"; return Math.floor(s / 60) + "m" + (s % 60) + "s" }
// 照 dsh formatMessageClock:同日 HH:mm;本年度其它天 {m}月{d}日 HH:mm;跨年 {y}年{m}月{d}日 HH:mm
function formatClock(iso?: string): string {
  if (!iso) return ""
  const d = new Date(iso); if (isNaN(d.getTime())) return ""
  const now = new Date()
  const pad = (n: number) => String(n).padStart(2, "0")
  const hhmm = pad(d.getHours()) + ":" + pad(d.getMinutes())
  const sameDay = d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate()
  if (sameDay) return hhmm
  const sameYear = d.getFullYear() === now.getFullYear()
  return sameYear ? `${d.getMonth() + 1}月${d.getDate()}日 ${hhmm}` : `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日 ${hhmm}`
}
interface Source { idx: number; chunk_id: string; title: string; content: string }

function inline(seg: string, key: number, citIdx: Set<number>, onCite: (idx: number) => void, activeIdx: number | null): ReactNode[] {
  const out: ReactNode[] = []
  const re = /(\*\*[^*]+\*\*|\[\d+\])/g
  let last = 0, m: RegExpExecArray | null
  while ((m = re.exec(seg))) {
    const tok = m[0]
    if (tok.startsWith("[")) {
      const idx = parseInt(tok.slice(1, -1), 10)
      out.push(<span key={key + "-t" + m.index}>{seg.slice(last, m.index)}</span>)
      if (citIdx.has(idx)) out.push(<button key={key + "-b" + idx} className={"cite" + (idx === activeIdx ? " active" : "")} onClick={() => onCite(idx)}>[{idx}]</button>)
    } else {
      out.push(<span key={key + "-t" + m.index}>{seg.slice(last, m.index)}</span>)
      out.push(<strong key={key + "-b" + m.index}>{tok.slice(2, -2)}</strong>)
    }
    last = m.index + tok.length
  }
  out.push(<span key={key + "-end"}>{seg.slice(last)}</span>)
  return out
}

function renderAnswer(blocks: Block[] | undefined, citations: Citation[] | undefined, onCite: (idx: number) => void, activeIdx: number | null): ReactNode {
  const list: Block[] = blocks || []
  const citIdx = new Set((citations || []).map((c) => c.idx))
  const out: ReactNode[] = []
  let key = 0
  list.forEach((b) => {
    key += 1
    const t = b.t || "p"
    if (t === "ul" || t === "ol") { const items = b.items || []; out.push(t === "ol" ? (<ol key={"o" + key}>{items.map((it, i) => (<li key={i}>{inline(it, key * 100 + i, citIdx, onCite, activeIdx)}</li>))}</ol>) : (<ul key={"u" + key}>{items.map((it, i) => (<li key={i}>{inline(it, key * 100 + i, citIdx, onCite, activeIdx)}</li>))}</ul>)) }
    else if (t === "h") { out.push(<div key={"h" + key} className="ans-heading">{inline(b.text || "", key * 100, citIdx, onCite, activeIdx)}</div>) }
    else { out.push(<p key={"p" + key}>{inline(b.text || "", key * 100, citIdx, onCite, activeIdx)}</p>) }
  })
  return <>{out}</>
}

function CopyBtn({ text }: { text: string }) {
  const [ok, setOk] = useState(false)
  return (<button className="copy-btn" title="复制" aria-label="复制" onClick={() => { navigator.clipboard.writeText(text); setOk(true); setTimeout(() => setOk(false), 1200) }}>{ok ? <I><path d="M20 6L9 17l-5-5"/></I> : <I><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></I>}</button>)
}

function Sidebar({ sessions, activeId, onSelect, onNew, onDelete, onRename, loadErr }: { sessions: Session[]; activeId: string | null; onSelect: (id: string) => void; onNew: () => void; onDelete: (id: string) => void; onRename: (id: string, title: string) => void; loadErr: string }) {
  const [editing, setEditing] = useState<string | null>(null)
  const [editVal, setEditVal] = useState("")
  const [menu, setMenu] = useState<string | null>(null)
  return (<aside className="sidebar">
    <div className="sb-top"><div className="brand"><div className="logo-icon">力</div><span className="brand-name">保险助手</span><span className="brand-badge">AGENT</span></div><div className="sb-avatar">U</div></div>
    <button className="new-session" onClick={onNew}><I><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></I><span>新会话</span></button>
    <div className="tree">{sessions.length === 0 && <div className="tree-empty">{loadErr || "暂无会话"}</div>}{sessions.map((s2) => (editing === s2.id ? (<div key={s2.id} className="tree-item active" onKeyDown={(e) => { if (e.key === "Enter" && editVal.trim()) { onRename(s2.id, editVal.trim()); setEditing(null) } if (e.key === "Escape") setEditing(null) }}><input className="tree-edit" autoFocus value={editVal} onChange={(e) => setEditVal(e.target.value)} onBlur={() => setEditing(null)} /></div>) : (<div key={s2.id} className={"tree-item" + (s2.id === activeId ? " active" : "")} onClick={() => onSelect(s2.id)} onDoubleClick={() => { setEditing(s2.id); setEditVal(s2.title) }} title="单击切换 · 双击重命名"><span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{s2.title}</span><button className="tree-more" title="更多操作" aria-label="更多操作" onClick={(e) => { e.stopPropagation(); setMenu(menu === s2.id ? null : s2.id) }}><I><circle cx="12" cy="5" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="12" cy="19" r="1.5"/></I></button>{menu === s2.id && (<><span className="ctx-backdrop" onClick={(e) => { e.stopPropagation(); setMenu(null) }} /><div className="ctx-menu"><button className="ctx-item" onClick={(e) => { e.stopPropagation(); setMenu(null); setEditing(s2.id); setEditVal(s2.title) }}><I><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/></I><span>重命名</span></button><button className="ctx-item danger" onClick={(e) => { e.stopPropagation(); setMenu(null); if (window.confirm("确定删除该会话及其内容?")) onDelete(s2.id) }}><I><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></I><span className="danger-text">删除任务</span></button></div></>)}</div>)))}</div>
    <div className="sb-bottom"><div className="settings-btn"><I><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></I><span>设置</span></div></div>
  </aside>)
}

function Details({ open, activeSource, onClose }: { open: boolean; activeSource: Source | null; onClose: () => void }) {
  const src = activeSource
  return (<div className="details" data-open={open || undefined}>
    <div className="details-head">
      <span>溯源</span>
    </div>
    <div className="details-body">
      {!src ? <div className="details-empty">点击回答中的角标查看对应溯源</div> :
        <div className="src-card active"><div className="src-title">[{src.idx}] {src.title}</div><div className="src-content">{src.content}</div></div>}
    </div>
  </div>)
}
function Center({ messages, input, setInput, busy, send, onCite, activeCite, title, trace, activeTab, setActiveTab, ctxUsage, model, setModel }: { messages: Msg[]; input: string; setInput: (s: string) => void; busy: boolean; send: () => void; onCite: (msgId: string, idx: number) => void; activeCite: { msgId: string; idx: number } | null; title: string; trace: any[]; activeTab: "chat" | "trace"; setActiveTab: (t: "chat" | "trace") => void; ctxUsage: { used: number; window: number; system: number; tools: number; messages: number; compression: boolean } | null; model: string; setModel: (m: string) => void }) {
  const ctxPct = ctxUsage && ctxUsage.window > 0 ? Math.min(100, Math.round((ctxUsage.used / ctxUsage.window) * 100)) : 0
  const [modelMenu, setModelMenu] = useState(false)
  const [ctxPop, setCtxPop] = useState(false)
  const listRef = useRef<HTMLDivElement>(null)
  useEffect(() => { listRef.current?.scrollTo(0, listRef.current.scrollHeight) }, [messages])
  const fmtD = (ms?: number) => { if (ms == null) return "—"; if (ms < 1000) return ms + "ms"; const s = ms / 1000; return (s < 10 ? String(Math.round(s * 10) / 10) : String(Math.round(s))) + "秒" }
  const fmtT = (v?: number) => { if (v == null) return "—"; const c = Math.max(0, v); return (c >= 10 ? String(Math.round(c)) : String(Math.round(c * 10) / 10)) + " tok/s" }
  // 每行渲染一个"节点":user / think / tool / answer,按事件序交错
  const renderRow = (m: Msg) => {
    if (m.role === "user") return (<div className="ans-wrap"><div className="message-text">{m.text}</div><div className="msg-chrome user">{formatClock(m.time)}<CopyBtn text={m.text || ""} /></div></div>)
    if (m.role === "text") return (<div className="ans-wrap"><div className="message-text">{m.text}</div></div>)
    if (m.role === "tool") return (<div className="tool-card" style={{ marginLeft: 0 }}><span className="tool-name">{m.tool?.name}</span>{m.tool?.args ? <span className="tool-args">{JSON.stringify(m.tool.args)}</span> : ""}<span className={"tool-status" + (m.tool?.running ? " running" : "")}>{m.tool?.running ? "调用中…" : "✓ 完成"}</span></div>)
    if (m.role === "think") {
      const summary = m.reasoning ? (m.streaming ? m.reasoning.split("\n").pop() : m.reasoning.split("\n")[0]) : "正在思考…"
      return (<div className="ans-wrap"><details className="think-row"><summary><span className="think-label">Think</span><span className="think-summary">{summary}</span>{m.streaming && <span className="stream-caret" />}</summary><div className="think-body">{m.reasoning}</div></details></div>)
    }
    // answer
    return (<div className="ans-wrap"><div className="message-text">{renderAnswer(m.blocks, m.citations, (idx) => onCite(m.id, idx), activeCite?.msgId === m.id ? activeCite.idx : null)}</div><div className="msg-chrome"><CopyBtn text={(m.blocks || []).map((b) => b.t === "ul" ? (b.items || []).join("\n") : b.text || "").join("\n")} />{formatClock(m.time)}{(m.runMs != null || m.ttftMs != null || m.tps != null) ? (<span className="msg-metrics">{(m.runMs != null ? " · 用时 " + fmtD(m.runMs) : "") + (m.ttftMs != null ? " · 首token " + fmtD(m.ttftMs) : "") + (m.tps != null ? " · " + fmtT(m.tps) : "")}</span>) : ""}</div></div>)
  }
  return (<div className="center">
    <div className="c-head"><div className="c-title">{title}</div><div className="tabs">
      <div className={"tab" + (activeTab === "chat" ? " active" : "")} onClick={() => setActiveTab("chat")}>对话</div>
      <div className={"tab" + (activeTab === "trace" ? " active" : "")} onClick={() => setActiveTab("trace")}>轨迹</div>
    </div></div>
    {activeTab === "chat"
      ? <div className="messages" ref={listRef}>{messages.length === 0 && <div className="hint">问一个保险问题,例如:重疾险的责任免除包括哪些?</div>}{(() => { const rows: ReactNode[] = []; let i = 0; while (i < messages.length) { const m = messages[i]; if (m.role === "user" || m.role === "answer") { rows.push(<div key={m.id} className={"message " + (m.role === "answer" ? "assistant" : m.role)} data-time-hover-root>{renderRow(m)}</div>); i += 1; continue } const grp: Msg[] = []; let j = i; while (j < messages.length && (messages[j].role === "think" || messages[j].role === "text" || messages[j].role === "tool")) { grp.push(messages[j]); j += 1 } const hasAnswer = j < messages.length && messages[j].role === "answer"; const ansRun = hasAnswer ? messages[j].runMs : undefined; const label = hasAnswer ? ("任务耗时 " + fmtElapsed(ansRun)) : "任务进行中…"; rows.push(<div key={"g" + i} className="message assistant" data-time-hover-root><div className="ans-wrap"><details className="process-group" open={!hasAnswer}><summary className="process-summary"><span className="process-label">{label}</span></summary><div className="process-body">{grp.map((g) => (<div key={g.id} className="message assistant" data-time-hover-root>{renderRow(g)}</div>))}</div></details></div></div>); i = j } return rows })()}</div>
      : <div className="trace-view">
          {trace.length === 0 && <div className="hint">本轮暂无轨迹</div>}
          {trace.map((t, i) => (
            <div key={i} className={"trace-row " + t.type}>
              <span className="trace-seq">{i + 1}</span>
              <span className="trace-type">{t.type}</span>
              <span className="trace-detail">{t.type === "tool_call" ? (t.tool + " " + JSON.stringify(t.args || {})) : t.type === "turn_end" ? ("完成 · 耗时 " + fmtD(t.elapsed_ms)) : formatClock(t.ts)}</span>
            </div>
          ))}
        </div>}
    <div className="input-wrap"><div className="composer"><div className="composer-top"><textarea className="composer-input" rows={1} value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send() } }} placeholder="给保险助手发消息" /></div><div className="composer-bottom"><div className="composer-tools"><button className="tool-btn"><I><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/></I><span>Workspace Write</span></button></div><div className="composer-right"><div className="model-wrap"><div className="model-select" onClick={() => setModelMenu((m) => !m)} title="选择模型"><span className="model-dot"></span><span className="model-name">{model}</span><I><path d="M6 9l6 6 6-6"/></I></div>{modelMenu && (<><span className="ctx-backdrop" onClick={() => setModelMenu(false)} /><div className="model-menu">{["deepseek-v4-flash", "deepseek-v4-pro"].map((m) => (<div key={m} className={"model-item" + (m === model ? " active" : "")} onClick={() => { setModel(m); setModelMenu(false) }}><span>{m}</span>{m === model ? <span className="model-check">✓</span> : null}</div>))}</div></>)}</div><button className="ctx-toggle" title="上下文用量" onClick={() => setCtxPop((p) => !p)}><svg className="ctx-ring" viewBox="0 0 36 36"><circle cx="18" cy="18" r="15.5" fill="none" stroke="#edf1f7" strokeWidth="4"/><circle cx="18" cy="18" r="15.5" fill="none" stroke="#5686fe" strokeWidth="4" strokeLinecap="round" strokeDasharray={String(2 * Math.PI * 15.5)} strokeDashoffset={String(2 * Math.PI * 15.5 * (1 - ctxPct / 100))} transform="rotate(-90 18 18)"/><text x="18" y="21" textAnchor="middle" fontSize="8" fill="#0f1115">{ctxPct}%</text></svg></button>{ctxPop && (<><span className="ctx-backdrop" onClick={() => setCtxPop(false)} /><div className="ctx-pop"><div className="ctx-usage"><div className="ctx-head"><span>上下文已用 <b>{ctxPct}%</b></span><span className="ctx-total">{fmtCtx(ctxUsage?.used ?? 0)} / {fmtCtx(ctxUsage?.window ?? 0)}</span></div><div className="ctx-bar"><div className="ctx-fill" style={{ width: ctxPct + "%" }} /></div><div className="ctx-rows"><div className="ctx-row"><span className="ctx-dot sys" />系统提示词<span className="ctx-val">{fmtCtx(ctxUsage?.system)}</span></div><div className="ctx-row"><span className="ctx-dot tool" />工具<span className="ctx-val">{fmtCtx(ctxUsage?.tools)}</span></div><div className="ctx-row"><span className="ctx-dot msg" />对话消息<span className="ctx-val">{fmtCtx(ctxUsage?.messages)}</span></div></div>{ctxUsage?.compression && <div className="ctx-remind">⚠ 上下文已达上限,已压缩历史</div>}</div></div></>)}<button className="send-btn" onClick={send} disabled={busy || !input.trim()}><I><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></I></button></div></div></div></div>
    <div className="footer-stats"><span>引用:点击回答中的角标,右侧展开对应溯源片段</span></div>
  </div>)
}

export default function App() {
  const [sessions, setSessions] = useState<Session[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [messages, setMessages] = useState<Msg[]>([])
  const [input, setInput] = useState("")
  const [busy, setBusy] = useState(false)
    const [trace, setTrace] = useState<any[]>([])
  const [loadErr, setLoadErr] = useState("")
  const [activeTab, setActiveTab] = useState<"chat" | "trace">("chat")
  const [sideW, setSideW] = useState(SIDEBAR_DEFAULT)
  const [detW, setDetW] = useState(DETAILS_DEFAULT)
  const [detailsOpen, setDetailsOpen] = useState(false)
  const [narrow, setNarrow] = useState(false)
  const [activeCite, setActiveCite] = useState<{ msgId: string; idx: number } | null>(null)
  const [ctxUsage, setCtxUsage] = useState<{ used: number; window: number; system: number; tools: number; messages: number; compression: boolean } | null>(null)
  const [model, setModel] = useState("deepseek-v4-flash")
  const frameRef = useRef<HTMLDivElement>(null)
  const [vp, setVp] = useState(1280)
  const idRef = useRef(0)
  const mid = () => "m" + (idRef.current++)
  const retrievalRef = useRef<any[]>([])
  const activeIdRef = useRef<string | null>(null)   // 当前激活会话(切换会话时用于判断事件该不该应用到当前视图)

  useEffect(() => { const el = frameRef.current; if (!el) return; const ro = new ResizeObserver(() => setVp(el.getBoundingClientRect().width)); ro.observe(el); return () => ro.disconnect() }, [])
  useEffect(() => { setNarrow(vp < SIDEBAR_AUTO_COLLAPSE) }, [vp])
  const cols = solve(vp, narrow ? 0 : sideW, detailsOpen ? detW : 0, narrow)
  // 激活的引用只属于"被点击的那条回答":按其自身的 sources 解析溯源,不在其它轮编号上高亮。
  const activeMsg = messages.find((m) => m.id === activeCite?.msgId)
  const activeSource = activeMsg?.sources?.find((s) => s.idx === activeCite?.idx) || null

  const buildSources = (cites: Citation[], chunks: any[]): Source[] => {
    const byId = new Map(chunks.map((c: any) => [c.chunk_id, c]))
    return (cites || []).map((c) => { const ch = byId.get(c.chunk_id) || {}; return { idx: c.idx, chunk_id: c.chunk_id, title: (ch.doc_id || "") + " " + (ch.version || "") + " · " + (ch.section || ""), content: ch.content || "(未找到原文)" } })
  }
  const loadEvents = async (sid: string) => {
    const evs = await listEvents(sid)
    const msgs: Msg[] = []; let chunks: any[] = []; let cites: Citation[] = []; const tr: any[] = []
    // 按 step 重构:每步 reasoning→think 行、text→叙述/回答行、tool_call→工具卡,按事件序交错还原历史视图
    let step: { kind: "tool" | "answer" | null; reason: string; text: string; tools: any[] } = { kind: null, reason: "", text: "", tools: [] }
    evs.forEach((e) => {
      if (e.type === "turn_start") { chunks = [] }
      else if (e.type === "user_message") msgs.push({ id: mid(), role: "user", text: e.payload.text, time: e.ts })
      else if (e.type === "step_start") { step = { kind: null, reason: "", text: "", tools: [] } }
      else if (e.type === "assistant_chunk") { const k = e.payload?.kind; const d = e.payload?.delta || ""; if (k === "reasoning") step.reason += d; else if (k === "text") step.text += d }
      else if (e.type === "tool_call") { step.kind = "tool"; tr.push({ type: "tool_call", tool: e.payload?.tool, args: e.payload?.args, ts: e.ts }) }
      else if (e.type === "retrieval") { chunks = [...chunks, ...(e.payload.chunks || [])]; step.tools.push({ query: e.payload?.query }) }
      else if (e.type === "assistant_message") { step.kind = "answer"; cites = e.payload.citations || []; if (step.reason.trim()) msgs.push({ id: mid(), role: "think", reasoning: step.reason, time: e.ts }); msgs.push({ id: mid(), role: "answer", blocks: e.payload?.blocks || [{ t: "p", text: "" }], citations: cites, sources: buildSources(cites, chunks), time: e.ts }) }
      else if (e.type === "step_end") { if (step.kind === "tool") { if (step.reason.trim()) msgs.push({ id: mid(), role: "think", reasoning: step.reason, time: e.ts }); if (step.text.trim()) msgs.push({ id: mid(), role: "text", text: step.text, time: e.ts }); for (const t of step.tools) msgs.push({ id: mid(), role: "tool", tool: { name: "search_knowledge", ok: true, args: { query: t.query } }, time: e.ts }) } }
      else if (e.type === "request_context") { const p = e.payload || {}; setCtxUsage({ used: p.prompt_tokens ?? 0, window: p.context_window ?? 0, system: p.system_tokens ?? 0, tools: p.tools_tokens ?? 0, messages: p.messages_tokens ?? 0, compression: !!p.compression_triggered }) }
      else if (e.type === "usage") { const p = e.payload || {}; const last = [...msgs].reverse().find((x) => x.role === "answer"); if (last) { last.ttftMs = p.ttft_ms ?? last.ttftMs; last.tps = p.tokens_per_second ?? last.tps } }
      else if (e.type === "turn_end") { const p = e.payload || {}; const last = [...msgs].reverse().find((x) => x.role === "answer"); if (last) { last.runMs = p.elapsed_ms ?? last.runMs; last.ttftMs = p.ttft_ms ?? last.ttftMs; last.tps = p.tokens_per_second ?? last.tps } tr.push({ type: "turn_end", ...p, ts: e.ts }) }
    })
    setMessages(msgs); setActiveCite(null)
    if (tr.length) setTrace(tr)
  }
  const selectSession = async (sid: string) => { activeIdRef.current = sid; setActiveId(sid); setActiveCite(null); setCtxUsage(null); try { await loadEvents(sid); setLoadErr("") } catch { setMessages([]); setLoadErr("加载会话失败,请稍后重试") } }
  // 后端重启有启动窗口(~12s):失败不显示"暂无会话",自动重试并提示
  useEffect(() => {
    let alive = true; let timer: number | undefined
    const load = async () => {
      try {
        const s = await listSessions()
        if (!alive) return
        setSessions(s); setLoadErr("")
        if (s.length) await selectSession(s[0].id)
        else { const n = await createSession("u1"); if (alive) { setSessions([n]); setActiveId(n.id) } }
      } catch { if (alive) { setLoadErr("后端未就绪,正在重试…"); timer = window.setTimeout(load, 2000) } }
    }
    load()
    return () => { alive = false; if (timer) window.clearTimeout(timer) }
  }, []) // eslint-disable-line
  const newSession = async () => { try { const s = await createSession("u1"); const all = await listSessions(); setSessions(all); setActiveId(s.id); activeIdRef.current = s.id; idRef.current = 0; setMessages([]); setActiveCite(null); setCtxUsage(null); setTrace([]); setLoadErr("") } catch { setLoadErr("创建会话失败,请稍后重试") } }
  const deleteSess = async (id: string) => { try { await deleteSession(id); const all = await listSessions(); setSessions(all); if (activeIdRef.current === id) { idRef.current = 0; setMessages([]); setActiveCite(null); setCtxUsage(null); setTrace([]); if (all.length) { activeIdRef.current = all[0].id; setActiveId(all[0].id); await loadEvents(all[0].id) } else { const n = await createSession("u1"); activeIdRef.current = n.id; setActiveId(n.id); setSessions([n]) } } } catch { setLoadErr("删除会话失败,请稍后重试") } }
  const renameSess = async (id: string, title: string) => { try { await renameSession(id, title); setSessions(await listSessions()) } catch { setLoadErr("重命名失败,请稍后重试") } }
  const send = async () => {
    const text = input.trim(); if (!text || busy) return
    if (!activeId) { const n = await createSession("u1"); setActiveId(n.id); setSessions(await listSessions()) }
    const sid = activeId as string
    setInput(""); setBusy(true)
    setMessages((m) => [...m, { id: mid(), role: "user", text, time: new Date().toISOString() }])
    // 行模型:openThink=当前"思考"行;openText=当前流式的 text 行(叙述或最终回答);answerId=最终回答行
    let openThink: string | null = null
    let openText: string | null = null
    let answerId: string | null = null
    let abandoned = false   // 本 send 的 turn 进行中用户切到别的会话 → 放弃实时更新,但后端 turn 仍继续并落日志
    openThink = mid()
    setMessages((m) => [...m, { id: openThink as string, role: "think", reasoning: "", streaming: true, time: new Date().toISOString() }])
    const closeThink = () => {
      if (!openThink) return
      const id = openThink; openThink = null
      setMessages((m) => {
        const idx = m.findIndex((x) => x.id === id)
        if (idx < 0) return m
        if (!m[idx].reasoning) return m.filter((x) => x.id !== id) // 空占位(无推理则移除,不留空 Think)
        const next = [...m]; next[idx] = { ...next[idx], streaming: false }; return next
      })
    }
    try {
      await sendPrompt(sid, text, (e: PEvent) => {
        if (activeIdRef.current !== sid) abandoned = true
        if (abandoned) { if (e.type === "turn_start") setBusy(true); else if (e.type === "turn_end") { setBusy(false); } return }
        if (e.type === "turn_start") { setBusy(true); retrievalRef.current = [] }
        else if (e.type === "assistant_chunk") {
          const kind = e.payload?.kind || "text"
          const delta = e.payload?.delta || ""
          if (kind === "reasoning") {
            if (!openThink) { const id = mid(); openThink = id; setMessages((m) => [...m, { id, role: "think", reasoning: "", streaming: true, time: e.ts }]) }
            const id = openThink
            setMessages((mm) => mm.map((x) => x.id === id ? { ...x, reasoning: (x.reasoning || "") + delta, streaming: true } : x))
          } else if (kind === "text") {
            // 叙述(工具步骤)与最终回答都以流式 text 行渲染;tool_call 落成叙述、assistant_message 落成回答
            if (!openText) { const tid = mid(); openText = tid; setMessages((m) => [...m, { id: tid, role: "text", text: "", streaming: true, time: e.ts }]) }
            const tid = openText
            setMessages((mm) => mm.map((x) => x.id === tid ? { ...x, text: (x.text || "") + delta, streaming: true } : x))
          }
        }
        else if (e.type === "tool_call") { setTrace((t) => [...t, { type: "tool_call", tool: e.payload?.tool, args: e.payload?.args, ts: e.ts }]); closeThink(); if (openText) { const id = openText; openText = null; setMessages((mm) => mm.map((x) => x.id === id ? { ...x, streaming: false } : x)) } setMessages((m) => [...m, { id: mid(), role: "tool", tool: { name: e.payload?.tool || "search_knowledge", ok: true, args: e.payload?.args, running: true }, time: e.ts }]) }
        else if (e.type === "tool_result") { setMessages((m) => m.map((x) => x.role === "tool" ? { ...x, tool: { name: x.tool?.name || "search_knowledge", ok: e.payload?.ok !== false, running: false, args: x.tool?.args } } : x)) }
        else if (e.type === "retrieval") { retrievalRef.current = [...retrievalRef.current, ...(e.payload?.chunks || [])] }
        else if (e.type === "assistant_message") { const cites = e.payload?.citations || []; const srcs = buildSources(cites, retrievalRef.current); closeThink(); const bl = e.payload?.blocks || [{ t: "p", text: "（本次回答为空）" }]; if (openText) { const id = openText; openText = null; setMessages((mm) => mm.map((x) => x.id === id ? { ...x, role: "answer", blocks: bl, citations: cites, sources: srcs, streaming: false } : x)); answerId = id } else { const aId = mid(); answerId = aId; setMessages((m) => [...m, { id: aId, role: "answer", blocks: bl, citations: cites, sources: srcs, time: e.ts }]) } }
        else if (e.type === "request_context") { const p = e.payload || {}; setCtxUsage({ used: p.prompt_tokens ?? 0, window: p.context_window ?? 0, system: p.system_tokens ?? 0, tools: p.tools_tokens ?? 0, messages: p.messages_tokens ?? 0, compression: !!p.compression_triggered }) }
        else if (e.type === "usage") { const p = e.payload || {}; if (answerId) setMessages((mm) => mm.map((x) => x.id === answerId ? { ...x, ttftMs: p.ttft_ms ?? x.ttftMs, tps: p.tokens_per_second ?? x.tps } : x)) }
        else if (e.type === "turn_end") { const p = e.payload || {}; if (answerId) setMessages((mm) => mm.map((x) => x.id === answerId ? { ...x, runMs: p.elapsed_ms ?? x.runMs, ttftMs: p.ttft_ms ?? x.ttftMs, tps: p.tokens_per_second ?? x.tps } : x)); setTrace((t) => [...t, { type: "turn_end", ...p, ts: e.ts }]); setBusy(false) }
      }, model)
    } catch { } finally { setBusy(false); try { setSessions(await listSessions()) } catch { } if (!abandoned) { if (openThink) closeThink(); if (!answerId) { const aId = mid(); answerId = aId; setMessages((m) => [...m, { id: aId, role: "answer", blocks: [{ t: "p", text: "回答生成中断,请重试。" }], citations: [], time: new Date().toISOString() }]) } } }
  }
  const toggleSource = (msgId: string, idx: number) => { setActiveCite({ msgId, idx }); setDetailsOpen(true) }

  return (<div className="frame" ref={frameRef}>
    <div className="sidebarCol" style={{ width: cols.sidebar }}><Sidebar sessions={sessions} activeId={activeId} onSelect={selectSession} onNew={newSession} onDelete={deleteSess} onRename={renameSess} loadErr={loadErr} /></div>
    <div className="centerCol" style={{ width: cols.center }}><Center messages={messages} input={input} setInput={setInput} busy={busy} send={send} onCite={toggleSource} activeCite={activeCite} title={sessions.find((s2) => s2.id === activeId)?.title || "新会话"} trace={trace} activeTab={activeTab} setActiveTab={setActiveTab} ctxUsage={ctxUsage} model={model} setModel={setModel} /></div>
    <div className="detailsCol" style={{ width: cols.details }}><Details open={detailsOpen} activeSource={activeSource} onClose={() => setDetailsOpen(false)} /></div>
    {!narrow && cols.sidebar > SIDEBAR_COLLAPSED && <ColHandle pos={cols.sidebar} onDrag={(dx) => setSideW(clamp(cols.sidebar + dx, SIDEBAR_MIN, SIDEBAR_MAX))} />}
    <button className="dt-expand" onClick={() => setDetailsOpen(!detailsOpen)} title={detailsOpen ? "收起右栏" : "展开右栏"}><I><rect x="3.5" y="3.5" width="17" height="17" rx="4"/><line x1="16.5" y1="8" x2="16.5" y2="16"/></I></button>
    {cols.details > 0 && <ColHandle pos={cols.sidebar + cols.center} onDrag={(dx) => setDetW(clamp(cols.details - dx, 0, DETAILS_MAX))} />}
  </div>)
}
