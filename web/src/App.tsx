import { useEffect, useRef, useState } from "react"
import type { ReactNode } from "react"
import { listSessions, createSession, listEvents, sendPrompt, type Session, type PEvent, type Citation } from "./lib/api"
import "./App.css"

const SIDEBAR_MIN = 264, SIDEBAR_MAX = 420, SIDEBAR_DEFAULT = 280
const SIDEBAR_COLLAPSED = 56, SIDEBAR_AUTO_COLLAPSE = 1024
const DETAILS_MAX = 520, DETAILS_DEFAULT = 360, CENTER_MIN = 640

function I({ children }: { children: ReactNode }) { return <svg className="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">{children}</svg> }
function clamp(v: number, min: number, max: number) { return Math.min(max, Math.max(min, Math.round(v))) }
function solve(vp: number, side: number, det: number, narrow: boolean) { let s = side === 0 ? SIDEBAR_COLLAPSED : side; let d = det; while (vp - s - d < CENTER_MIN) { if (d > 0) { d = Math.max(0, d - 20) } else if (!narrow && s > SIDEBAR_COLLAPSED) { s = SIDEBAR_COLLAPSED } else { break } } return { sidebar: s, center: Math.max(0, vp - s - d), details: d } }
function ColHandle({ pos, onDrag }: { pos: number; onDrag: (dx: number) => void }) { const start = useRef(0); return (<div className="col-handle" style={{ left: pos - 3 }} onPointerDown={(e) => { e.preventDefault(); e.currentTarget.setPointerCapture(e.pointerId); start.current = e.clientX }} onPointerMove={(e) => { if (e.currentTarget.hasPointerCapture(e.pointerId)) onDrag(e.clientX - start.current) }} onPointerUp={(e) => { if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId) }} />) }

interface Msg { id: string; role: "user" | "assistant" | "tool"; text?: string; citations?: Citation[]; tool?: { name: string; ok: boolean } }
interface Source { idx: number; chunk_id: string; title: string; content: string }

// 渲染文本:仅在回答引用了 [idx] 时显示角标;未引用的 [数字] 一律剥掉
function renderText(text: string, citations: Citation[] | undefined, onCite: (idx: number) => void) {
  const raw = text || ""
  const cit = (citations || []).filter((c) => raw.indexOf("[" + c.idx + "]") >= 0)
  if (cit.length === 0) return <>{raw.replace(/\[\d+\]/g, "")}</>
  const idxs = cit.map((c) => c.idx)
  const p: ReactNode[] = []; let cursor = 0
  const re = /\[(\d+)\]/g; let m: RegExpExecArray | null
  while ((m = re.exec(raw))) {
    const n = parseInt(m[1], 10)
    if (idxs.includes(n)) {
      p.push(<span key={"t" + n}>{raw.slice(cursor, m.index)}</span>)
      p.push(<button key={"b" + n} className="cite" onClick={() => onCite(n)}>[{n}]</button>)
      cursor = m.index + m[0].length
    }
  }
  p.push(<span key="tail">{raw.slice(cursor)}</span>)
  return <>{p}</>
}

function Sidebar({ sessions, activeId, onSelect, onNew }: { sessions: Session[]; activeId: string | null; onSelect: (id: string) => void; onNew: () => void }) {
  return (<aside className="sidebar">
    <div className="sb-top"><div className="brand"><div className="logo-icon">力</div><span className="brand-name">保险助手</span><span className="brand-badge">AGENT</span></div><div className="sb-avatar">U</div></div>
    <button className="new-session" onClick={onNew}><I><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></I><span>新会话</span></button>
    <div className="tree">{sessions.length === 0 && <div className="tree-empty">暂无会话</div>}{sessions.map((s2) => (<div key={s2.id} className={"tree-item" + (s2.id === activeId ? " active" : "")} onClick={() => onSelect(s2.id)}><span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{s2.title}</span></div>))}</div>
    <div className="sb-bottom"><div className="settings-btn"><I><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></I><span>设置</span></div></div>
  </aside>)
}

// 溯源面板:只显示被引用的片段;点 [n] 展开/收起
function Details({ open, activeIdx, sources, onToggle }: { open: boolean; activeIdx: number | null; sources: Source[]; onToggle: (idx: number) => void }) {
  return (<div className="details" data-open={open || undefined}>
    <div className="details-head"><span>溯源 · 引用来源</span></div>
    <div className="details-body">
      {sources.length === 0 && <div className="details-empty">回答未引用资料,无溯源片段</div>}
      {sources.map((s) => {
        const on = s.idx === activeIdx
        return (<div key={s.idx} className={"src-card" + (on ? " active" : "")} onClick={() => onToggle(s.idx)}>
          <div className="src-title">[{s.idx}] {s.title}</div>
          {on && <div className="src-content">{s.content}</div>}
        </div>)
      })}
    </div>
  </div>)
}

function Center({ messages, input, setInput, busy, send, onCite, title }: { messages: Msg[]; input: string; setInput: (s: string) => void; busy: boolean; send: () => void; onCite: (idx: number) => void; title: string }) {
  const listRef = useRef<HTMLDivElement>(null)
  useEffect(() => { listRef.current?.scrollTo(0, listRef.current.scrollHeight) }, [messages])
  return (<div className="center">
    <div className="c-head"><div className="c-title">{title}</div><div className="tabs"><div className="tab active">对话</div><div className="tab">轨迹</div></div></div>
    <div className="messages" ref={listRef}>{messages.length === 0 && <div className="hint">问一个保险问题,例如:重疾险的责任免除包括哪些?</div>}{messages.map((m) => (<div key={m.id} className={"message " + m.role}>{m.role === "tool" ? (<div className="tool-card" style={{ marginLeft: 0 }}><span className="tool-name">{m.tool?.name}</span><span className="tool-status">✓ 完成</span></div>) : (<div className="message-text">{renderText(m.text || "", m.citations, onCite)}</div>)}</div>))}{busy && <div className="hint">检索中…</div>}</div>
    <div className="input-wrap"><div className="composer"><div className="composer-top"><textarea className="composer-input" rows={1} value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send() } }} placeholder="给保险助手发消息" /></div><div className="composer-bottom"><div className="composer-tools"><button className="tool-btn"><I><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/></I><span>Workspace Write</span></button></div><div className="composer-right"><div className="model-select"><span className="model-dot"></span><span>deepseek · 高</span></div><button className="send-btn" onClick={send} disabled={busy || !input.trim()}><I><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></I></button></div></div></div></div>
    <div className="footer-stats">引用:点击回答中的角标,右侧展开对应溯源片段</div>
  </div>)
}

export default function App() {
  const [sessions, setSessions] = useState<Session[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [messages, setMessages] = useState<Msg[]>([])
  const [sources, setSources] = useState<Source[]>([])
  const [input, setInput] = useState("")
  const [busy, setBusy] = useState(false)
  const [sideW, setSideW] = useState(SIDEBAR_DEFAULT)
  const [detW, setDetW] = useState(DETAILS_DEFAULT)
  const [detailsOpen, setDetailsOpen] = useState(true)
  const [narrow, setNarrow] = useState(false)
  const [activeIdx, setActiveIdx] = useState<number | null>(null)
  const frameRef = useRef<HTMLDivElement>(null)
  const [vp, setVp] = useState(1280)
  const idRef = useRef(0)
  const mid = () => "m" + (idRef.current++)
  const retrievalRef = useRef<any[]>([])

  useEffect(() => { const el = frameRef.current; if (!el) return; const ro = new ResizeObserver(() => setVp(el.getBoundingClientRect().width)); ro.observe(el); return () => ro.disconnect() }, [])
  useEffect(() => { setNarrow(vp < SIDEBAR_AUTO_COLLAPSE) }, [vp])
  const cols = solve(vp, narrow ? 0 : sideW, detailsOpen ? detW : 0, narrow)

  const buildSources = (cites: Citation[], chunks: any[]): Source[] => {
    const byId = new Map(chunks.map((c: any) => [c.chunk_id, c]))
    return (cites || []).map((c) => { const ch = byId.get(c.chunk_id) || {}; return { idx: c.idx, chunk_id: c.chunk_id, title: (ch.doc_id || "") + " " + (ch.version || "") + " · " + (ch.section || ""), content: ch.content || "(未找到原文)" } })
  }

  const loadEvents = async (sid: string) => {
    const evs = await listEvents(sid)
    const msgs: Msg[] = []; let chunks: any[] = []; let cites: Citation[] = []
    evs.forEach((e) => {
      if (e.type === "user_message") msgs.push({ id: mid(), role: "user", text: e.payload.text })
      else if (e.type === "retrieval") { chunks = e.payload.chunks || []; msgs.push({ id: mid(), role: "tool", tool: { name: "search_knowledge", ok: true } }) }
      else if (e.type === "assistant_message") { cites = e.payload.citations || []; msgs.push({ id: mid(), role: "assistant", text: e.payload.text, citations: cites }) }
    })
    setMessages(msgs); setSources(buildSources(cites, chunks)); setActiveIdx(null)
  }

  const selectSession = async (sid: string) => { setActiveId(sid); setActiveIdx(null); try { await loadEvents(sid) } catch { setMessages([]); setSources([]) } }
  useEffect(() => { listSessions().then(async (s) => { setSessions(s); if (s.length) await selectSession(s[0].id) }).catch(() => {}) }, []) // eslint-disable-line
  const newSession = async () => { const s = await createSession("u1"); const all = await listSessions(); setSessions(all); setActiveId(s.id); idRef.current = 0; setMessages([]); setSources([]); setActiveIdx(null) }

  const send = async () => {
    const text = input.trim(); if (!text || busy || !activeId) return
    setInput(""); setBusy(true)
    setMessages((m) => [...m, { id: mid(), role: "user", text }])
    try {
      await sendPrompt(activeId, text, (e: PEvent) => {
        if (e.type === "retrieval") { retrievalRef.current = e.payload.chunks || []; setMessages((m) => [...m, { id: mid(), role: "tool", tool: { name: "search_knowledge", ok: true } }]) }
        else if (e.type === "assistant_message") { const cites = e.payload.citations || []; setSources(buildSources(cites, retrievalRef.current)); setActiveIdx(null); setMessages((m) => [...m, { id: mid(), role: "assistant", text: e.payload.text, citations: cites }]) }
      })
    } catch { } finally { setBusy(false) }
  }

  const toggleSource = (idx: number) => { setActiveIdx((cur) => (cur === idx ? null : idx)); setDetailsOpen(true) }

  return (<div className="frame" ref={frameRef}>
    <div className="sidebarCol" style={{ width: cols.sidebar }}><Sidebar sessions={sessions} activeId={activeId} onSelect={selectSession} onNew={newSession} /></div>
    <div className="centerCol" style={{ width: cols.center }}><Center messages={messages} input={input} setInput={setInput} busy={busy} send={send} onCite={toggleSource} title={sessions.find((s2) => s2.id === activeId)?.title || "新会话"} /></div>
    <div className="detailsCol" style={{ width: cols.details }}><Details open={detailsOpen} activeIdx={activeIdx} sources={sources} onToggle={toggleSource} /></div>
    {!narrow && cols.sidebar > SIDEBAR_COLLAPSED && <ColHandle pos={cols.sidebar} onDrag={(dx) => setSideW(clamp(cols.sidebar + dx, SIDEBAR_MIN, SIDEBAR_MAX))} />}
    {cols.details > 0 && <ColHandle pos={cols.sidebar + cols.center} onDrag={(dx) => setDetW(clamp(cols.details - dx, 0, DETAILS_MAX))} />}
  </div>)
}