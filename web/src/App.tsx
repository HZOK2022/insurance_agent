import { useEffect, useRef, useState } from 'react'
import type { Message, Citation } from './lib/types'
import { MOCK_SESSIONS, streamMockReply, mockChunk } from './lib/mock'

function Sidebar({ sessions, activeId, onSelect, onNew }: {
  sessions: string[]; activeId: string; onSelect: (i: number) => void; onNew: () => void
}) {
  return (
    <aside className="sidebar">
      <div className="brand">保险助手</div>
      <button className="new" onClick={onNew}>+ 新会话</button>
      <ul>
        {sessions.map((t, i) => (
          <li key={i} className={i === activeId ? 'active' : ''} onClick={() => onSelect(i)}>
            {t}
          </li>
        ))}
      </ul>
    </aside>
  )
}

function MessageRow({ m, onCite }: { m: Message; onCite: (c: Citation) => void }) {
  if (m.role === 'tool') {
    return (
      <div className="msg tool-card">
        <span className="tool-name">{m.tool?.name}</span>
        <span className="tool-status">{m.tool?.ok ? '✓ 完成' : '✗ ' + (m.tool?.error || '失败')}</span>
      </div>
    )
  }
  const cls = m.role === 'user' ? 'msg user' : 'msg assistant'
  return (
    <div className={cls}>
      {m.text != null && renderTextWithCitations(m.text, m.citations, onCite)}
    </div>
  )
}

function renderTextWithCitations(text: string, citations: Citation[] | undefined, onCite: (c: Citation) => void) {
  if (!citations || citations.length === 0) return <span>{text}</span>
  const parts: React.ReactNode[] = []
  let last = 0
  citations.forEach((c) => {
    parts.push(<span key={'t' + c.idx}>{text.slice(last, text.indexOf('[' + c.idx + ']', last))}</span>)
    const at = text.indexOf('[' + c.idx + ']', last)
    if (at >= 0) {
      parts.push(<button key={'b' + c.idx} className="cite" onClick={() => onCite(c)}>[{c.idx}]</button>)
      last = at + 3
    }
  })
  parts.push(<span key="tail">{text.slice(last)}</span>)
  return <>{parts}</>
}

export default function App() {
  const [sessions] = useState(MOCK_SESSIONS.map((s) => s.title))
  const [active, setActive] = useState(0)
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [popup, setPopup] = useState<{ content: string; source: string } | null>(null)
  const listRef = useRef<HTMLDivElement>(null)

  useEffect(() => { listRef.current?.scrollTo(0, listRef.current.scrollHeight) }, [messages])

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
    <div className="shell">
      <Sidebar sessions={sessions} activeId={active} onSelect={setActive} onNew={() => { setMessages([]); setPopup(null) }} />
      <main className="chat">
        <header className="chat-head">{sessions[active] || '新会话'}</header>
        <div className="list" ref={listRef}>
          {messages.length === 0 && <div className="hint">问一个保险问题,例如:重疾险的责任免除包括哪些?</div>}
          {messages.map((m) => <MessageRow key={m.id} m={m} onCite={(c) => {
            const ch = mockChunk(c.chunk_id)
            ch ? setPopup({ content: ch.content, source: ch.source + ' · ' + ch.doc_id + ' ' + ch.version }) : setPopup({ content: 'chunk 未命中', source: c.chunk_id })
          }} />)}
        </div>
        <form className="input" onSubmit={(e) => { e.preventDefault(); send() }}>
          <input value={input} onChange={(e) => setInput(e.target.value)} placeholder="输入问题…" disabled={busy} />
          <button disabled={busy || !input.trim()}>发送</button>
        </form>
      </main>
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
