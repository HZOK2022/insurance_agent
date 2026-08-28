import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import type { Message, Citation } from './lib/types'
import { streamMockReply, mockChunk } from './lib/mock'
import './App.css'

const SIDEBAR_MIN = 264, SIDEBAR_MAX = 420, SIDEBAR_DEFAULT = 280
const SIDEBAR_COLLAPSED = 56, SIDEBAR_AUTO_COLLAPSE = 1024
const DETAILS_MIN = 300, DETAILS_MAX = 520, DETAILS_DEFAULT = 360
const CENTER_MIN = 640

function I({ children }: { children: ReactNode }) { return <svg className="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">{children}</svg> }

// 只有会话窗口,没有文件夹
const SESSIONS = ['重疾险责任免除咨询', '百万医疗免赔额', '优先理赔案例', '销售话术合规', '年金险对比']

function clamp(v: number, min: number, max: number) { return Math.min(max, Math.max(min, Math.round(v))) }
function solve(vp: number, side: number, det: number, narrow: boolean) {
  let s = side === 0 ? SIDEBAR_COLLAPSED : side
  let d = det
  while (vp - s - d < CENTER_MIN) { if (d > 0) { d = Math.max(0, d - 20) } else if (!narrow && s > SIDEBAR_COLLAPSED) { s = SIDEBAR_COLLAPSED } else { break } }
  return { sidebar: s, center: Math.max(0, vp - s - d), details: d }
}

function ColHandle({ pos, onDrag, side }: { pos: number; onDrag: (dx: number) => void; side: 'sidebar' | 'details' }) {
  const start = useRef(0)
  return (
    <div className={'col-handle' + (side === 'details' ? ' detail' : '')} style={{ left: pos - 3 }}
      onPointerDown={(e) => { e.preventDefault(); e.currentTarget.setPointerCapture(e.pointerId); start.current = e.clientX }}
      onPointerMove={(e) => { if (e.currentTarget.hasPointerCapture(e.pointerId)) onDrag(e.clientX - start.current) }}
      onPointerUp={(e) => { if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId) }} />
  )
}

function Sidebar({ active, onSelect, onNew, busyBy }: { active: number; onSelect: (i: number) => void; onNew: () => void; busyBy: Record<number, boolean> }) {
  return (
    <aside className="sidebar">
      <div className="sb-top"><div className="brand"><div className="logo-icon">力</div><span className="brand-name">保险助手</span><span className="brand-badge">AGENT</span></div><div className="sb-avatar">U</div></div>
      <button className="new-session" onClick={onNew}><I><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></I><span>新会话</span></button>
      <div className="tree">
        {SESSIONS.map((it, idx) => (
          <div key={idx} className={'tree-item' + (idx === active ? ' active' : '')} onClick={() => onSelect(idx)}>
            <I><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></I>
            <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{it}</span>
            
          </div>
        ))}
      </div>
      <div className="sb-bottom"><div className="settings-btn"><I><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></I><span>设置</span></div></div>
    </aside>
  )
}

function renderText(text: string, citations: Citation[] | undefined, onCite: (c: Citation) => void) {
  if (!citations || citations.length === 0) return <>{text}</>
  const parts: ReactNode[] = []
  let last = 0
  citations.forEach((c) => { const at = text.indexOf('[' + c.idx + ']', last); if (at < 0) return; parts.push(<span key={'t' + c.idx}>{text.slice(last, at)}</span>); parts.push(<button key={'b' + c.idx} className="cite" onClick={() => onCite(c)}>[{c.idx}]</button>); last = at + 3 })
  parts.push(<span key="tail">{text.slice(last)}</span>)
  return <>{parts}</>
}

function Details({ open, onToggle, activeChunk, sources, onActiveChunk }: { open: boolean; onToggle: () => void; activeChunk: string | null; sources: { chunk_id: string; title: string; content: string }[]; onActiveChunk: (id: string) => void }) {
  return (
    <div className="details" data-open={open || undefined}>
      <div className="details-head"><span>溯源 · 引用来源</span></div>
      <div className="details-body">
        {sources.length === 0 && <div className="details-empty">暂无引用来源</div>}
        {sources.map((s) => (<div key={s.chunk_id} className={'src-card' + (s.chunk_id === activeChunk ? ' active' : '')} onClick={() => onActiveChunk(s.chunk_id)}><div className="src-title">{s.title}</div><div className="src-content">{s.content}</div></div>))}
      </div>
    </div>
  )
}

function Center({ messages, input, setInput, busy, send, onCite, title, onToggleDetails, detailsOpen }: { messages: Message[]; input: string; setInput: (s: string) => void; busy: boolean; send: () => void; onCite: (c: Citation) => void; title: string; onToggleDetails: () => void; detailsOpen: boolean }) {
  const listRef = useRef<HTMLDivElement>(null)
  useEffect(() => { listRef.current?.scrollTo(0, listRef.current.scrollHeight) }, [messages])
  return (
    <div className="center">
      <div className="c-head"><div className="c-title">{title}</div><div className="tabs"><div className="tab active">对话</div><div className="tab">轨迹</div></div></div>
      <div className="messages" ref={listRef}>
        {messages.length === 0 && <div className="hint">问一个保险问题,例如:重疾险的责任免除包括哪些?</div>}
        {messages.map((m) => (<div key={m.id} className={'message ' + m.role}>{m.role === 'tool' ? (<div className="tool-card"><span className="tool-name">{m.tool?.name}</span><span className="tool-status">{m.tool?.ok ? '✓ 完成' : '✗ ' + (m.tool?.error || '失败')}</span></div>) : (<div className="message-text">{renderText(m.text || '', m.citations, onCite)}</div>)}</div>))}
      </div>
      <div className="input-wrap"><div className="composer"><div className="composer-top"><textarea className="composer-input" rows={1} value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }} placeholder="给保险助手发消息" /></div><div className="composer-bottom"><div className="composer-tools"><button className="tool-btn"><I><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/></I><span>Workspace Write</span></button></div><div className="composer-right"><div className="model-select"><span className="model-dot"></span><span>deepseek · 高</span></div><button className="send-btn" onClick={send} disabled={busy || !input.trim()}><I><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></I></button></div></div></div></div>
      <div className="footer-stats">0 轮 · 0 步 · 引用:点击角标,右侧溯源面板查看原文</div>
    </div>
  )
}

export default function App() {
  const [active, setActive] = useState(0)
  const [msgsBy, setMsgsBy] = useState<Record<number, Message[]>>({})
  const [busyBy, setBusyBy] = useState<Record<number, boolean>>({})
  const [input, setInput] = useState('')
  const [sideW, setSideW] = useState(SIDEBAR_DEFAULT)
  const [detW, setDetW] = useState(DETAILS_DEFAULT)
  const [detailsOpen, setDetailsOpen] = useState(true)
  const [narrow, setNarrow] = useState(false)
  const [activeChunk, setActiveChunk] = useState<string | null>(null)
  const frameRef = useRef<HTMLDivElement>(null)
  const [vp, setVp] = useState(1280)

  useEffect(() => { const el = frameRef.current; if (!el) return; const ro = new ResizeObserver(() => setVp(el.getBoundingClientRect().width)); ro.observe(el); return () => ro.disconnect() }, [])
  useEffect(() => { setNarrow(vp < SIDEBAR_AUTO_COLLAPSE) }, [vp])
  const messages = msgsBy[active] || []
  const busy = !!busyBy[active]
  const cols = solve(vp, narrow ? 0 : sideW, detailsOpen ? detW : 0, narrow)
  const sources = messages.flatMap((m) => (m.citations || []).map((c) => { const ch = mockChunk(c.chunk_id); return { chunk_id: c.chunk_id, title: ch ? ch.doc_id + ' ' + ch.version + ' · ' + ch.section : c.chunk_id, content: ch ? ch.content : 'chunk 未命中' } }))

  const setMsg = (idx: number, upd: (prev: Message[]) => Message[]) => setMsgsBy((prev) => ({ ...prev, [idx]: upd(prev[idx] || []) }))

  const send = () => {
    const text = input.trim(); if (!text || busy) return
    setInput('')
    setMsg(active, (m) => [...m, { id: 'u' + Date.now(), role: 'user', text }])
    setBusyBy((b) => ({ ...b, [active]: true }))
    streamMockReply(text,
      (mm) => setMsg(active, (prev) => { const i = prev.findIndex((x) => x.id === mm.id); if (i < 0) return [...prev, mm]; const copy = [...prev]; copy[i] = mm; return copy }),
      () => setBusyBy((b) => ({ ...b, [active]: false })))
  }

  return (
    <div className="frame" ref={frameRef}>
      <div className="sidebarCol" style={{ width: cols.sidebar }}><Sidebar active={active} onSelect={setActive} onNew={() => { setMsgsBy((m) => ({ ...m, [active]: [] })); setActiveChunk(null) }} busyBy={busyBy} /></div>
      <div className="centerCol" style={{ width: cols.center }}><Center messages={messages} input={input} setInput={setInput} busy={busy} send={send} onCite={(c) => { setActiveChunk(c.chunk_id); setDetailsOpen(true) }} title={SESSIONS[active]} onToggleDetails={() => setDetailsOpen(!detailsOpen)} detailsOpen={detailsOpen} /></div>
      <div className="detailsCol" style={{ width: cols.details }}><Details open={detailsOpen} onToggle={() => setDetailsOpen(!detailsOpen)} activeChunk={activeChunk} sources={sources} onActiveChunk={setActiveChunk} /></div>
      <button className="dt-expand" style={{ left: cols.sidebar + cols.center }} onClick={() => setDetailsOpen(!detailsOpen)} title={detailsOpen ? '收起溯源' : '展开溯源'}><I><path d={detailsOpen ? 'M15 18l-6-6 6-6' : 'M9 18l6-6-6-6'}/></I></button>      {!narrow && cols.sidebar > SIDEBAR_COLLAPSED && <ColHandle pos={cols.sidebar} onDrag={(dx) => setSideW(clamp(cols.sidebar + dx, SIDEBAR_MIN, SIDEBAR_MAX))} side="sidebar" />}
      {cols.details > 0 && <ColHandle pos={cols.sidebar + cols.center} onDrag={(dx) => setDetW(clamp(cols.details - dx, 0, DETAILS_MAX))} side="details" />}
    </div>
  )
}
