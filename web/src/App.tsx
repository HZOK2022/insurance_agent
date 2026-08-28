import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import type { Message, Citation } from './lib/types'
import { streamMockReply, mockChunk } from './lib/mock'
import './App.css'

function I({ children }: { children: ReactNode }) {
  return <svg className="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">{children}</svg>
}

const TREE = [
  { name: '知识库', items: ['重疾险责任免除咨询', '百万医疗免赔额'] },
  { name: '会话历史', items: ['优先理赔案例', '销售话术合规', '年金险对比'] },
]
const ALL = TREE.flatMap((g) => g.items)

function Sidebar({ active, onSelect, onNew }: { active: number; onSelect: (i: number) => void; onNew: () => void }) {
  let base = 0
  return (
    <aside className="sidebar">
      <div className="sidebar-top">
        <button className="new-session" onClick={onNew}>
          <I><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></I>
          <span>新会话</span>
        </button>
      </div>
      <div className="workspace-bar">
        <span>工作区</span>
        <div className="workspace-actions">
          <I><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></I>
          <I><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></I>
          <I><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></I>
        </div>
      </div>
      <div className="tree">
        {TREE.map((g) => {
          const group = (
            <div key={g.name}>
              <div className="tree-folder">
                <I><path d="M9 18l6-6-6-6"/></I>
                <I><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></I>
                <span>{g.name}</span>
              </div>
              {g.items.map((it, j) => {
                const idx = base + j
                return (
                  <div key={j} className={'tree-item' + (idx === active ? ' active' : '')} onClick={() => onSelect(idx)}>
                    <I><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></I>
                    <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{it}</span>
                  </div>
                )
              })}
            </div>
          )
          base += g.items.length
          return group
        })}
      </div>
      <div className="sidebar-bottom">
        <div className="settings-btn">
          <I><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></I>
          <span>设置</span>
        </div>
      </div>
    </aside>
  )
}

function renderText(text: string, citations: Citation[] | undefined, onCite: (c: Citation) => void) {
  if (!citations || citations.length === 0) return <>{text}</>
  const parts: ReactNode[] = []
  let last = 0
  citations.forEach((c) => {
    const at = text.indexOf('[' + c.idx + ']', last)
    if (at < 0) return
    parts.push(<span key={'t' + c.idx}>{text.slice(last, at)}</span>)
    parts.push(<button key={'b' + c.idx} className="cite" onClick={() => onCite(c)}>[{c.idx}]</button>)
    last = at + 3
  })
  parts.push(<span key="tail">{text.slice(last)}</span>)
  return <>{parts}</>
}

function Chat({ messages, input, setInput, busy, send, onCite }: {
  messages: Message[]; input: string; setInput: (s: string) => void; busy: boolean; send: () => void; onCite: (c: Citation) => void
}) {
  const listRef = useRef<HTMLDivElement>(null)
  useEffect(() => { listRef.current?.scrollTo(0, listRef.current.scrollHeight) }, [messages])
  return (
    <main className="chat">
      <div className="tabs">
        <div className="tab active">对话</div>
        <div className="tab">轨迹</div>
      </div>
      <div className="messages" ref={listRef}>
        {messages.length === 0 && <div className="hint">问一个保险问题,例如:重疾险的责任免除包括哪些?</div>}
        {messages.map((m) => (
          <div key={m.id} className={'message ' + m.role}>
            {m.role === 'tool' ? (
              <div className="tool-card">
                <span className="tool-name">{m.tool?.name}</span>
                <span className="tool-status">{m.tool?.ok ? '✓ 完成' : '✗ ' + (m.tool?.error || '失败')}</span>
              </div>
            ) : (
              <div className="message-text">{renderText(m.text || '', m.citations, onCite)}</div>
            )}
          </div>
        ))}
      </div>
      <div className="input-wrap">
        <div className="composer">
          <div className="composer-top">
            <div className="composer-actions-left">
              <button className="composer-btn"><I><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></I></button>
            </div>
            <textarea className="composer-input" rows={1} value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
              placeholder="给保险助手发消息" />
          </div>
          <div className="composer-bottom">
            <div className="composer-tools">
              <button className="tool-btn">
                <I><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/></I>
                <span>Workspace Write</span>
                <I><path d="M6 9l6 6 6-6"/></I>
              </button>
              <button className="composer-btn"><I><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></I></button>
            </div>
            <div className="composer-right">
              <div className="model-select"><span className="model-dot"></span><span>deepseek · 高</span></div>
              <button className="send-btn" onClick={send} disabled={busy || !input.trim()}>
                <I><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></I>
              </button>
            </div>
          </div>
        </div>
      </div>
      <div className="footer-stats">0 轮 · 0 步 · 首 token 平均 - · 引用:回答带出处,点击角标查看原文</div>
    </main>
  )
}

export default function App() {
  const [active, setActive] = useState(0)
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [popup, setPopup] = useState<{ content: string; source: string } | null>(null)

  const send = () => {
    const text = input.trim()
    if (!text || busy) return
    setInput('')
    setBusy(true)
    setMessages((m) => [...m, { id: 'u' + Date.now(), role: 'user', text }])
    streamMockReply(text,
      (mm) => setMessages((prev) => {
        const i = prev.findIndex((x) => x.id === mm.id)
        if (i < 0) return [...prev, mm]
        const copy = [...prev]; copy[i] = mm; return copy
      }),
      () => setBusy(false))
  }

  return (
    <div className="app">
      <header className="top-header">
        <div className="header-left">
          <div className="logo-icon">力</div>
          <div className="brand"><span className="brand-name">保险助手</span><span className="brand-badge">AGENT</span></div>
        </div>
        <div className="session-title"><span>{ALL[active] || '新会话'}</span><I><path d="M6 9l6 6 6-6"/></I></div>
        <div className="header-right">
          <div className="header-btn"><span>1 个后台任务</span><I><path d="M6 9l6 6 6-6"/></I></div>
          <div className="avatar">U</div>
        </div>
      </header>

      <div className="main">
        <Sidebar active={active} onSelect={setActive} onNew={() => { setMessages([]); setPopup(null) }} />
        <Chat messages={messages} input={input} setInput={setInput} busy={busy} send={send} onCite={(c) => {
          const ch = mockChunk(c.chunk_id)
          ch ? setPopup({ content: ch.content, source: ch.source + ' · ' + ch.doc_id + ' ' + ch.version }) : setPopup({ content: 'chunk 未命中', source: c.chunk_id })
        }} />
      </div>

      {popup && (
        <div className="popup" onClick={() => setPopup(null)}>
          <div className="popup-card" onClick={(e) => e.stopPropagation()}>
            <div className="popup-src">{popup.source}</div>
            <div className="popup-body">{popup.content}</div>
            <button onClick={() => setPopup(null)}>关闭</button>
          </div>
        </div>
      )}
    </div>
  )
}
