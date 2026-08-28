import type { SessionSummary } from './types'

// 契约 v0 客户端(默认 VITE_MOCK=1 时不走这里;置 0 后连真实/mock 后端)
const BASE = '' // 同源;Vite 已代理 /api

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(BASE + path, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!r.ok) throw new Error(path + ' -> ' + r.status)
  return r.json() as Promise<T>
}

export const listSessions = () => json<SessionSummary[]>('/api/sessions')
export const createSession = (userId: string) => json<SessionSummary>('/api/sessions', { method: 'POST', body: JSON.stringify({ user_id: userId }) })
export const sendPrompt = (id: string, text: string) => json<{ accepted: boolean }>('/api/sessions/' + id + '/prompt', { method: 'POST', body: JSON.stringify({ text }) })

// SSE 事件订阅(契约核心):把事件帧推给回调
export function subscribeEvents(sessionId: string, onEvent: (ev: any) => void) {
  const ctrl = new AbortController()
  fetch(BASE + '/api/sessions/' + sessionId + '/events', { signal: ctrl.signal })
    .then(async (resp) => {
      if (!resp.body) return
      const reader = resp.body.getReader()
      const dec = new TextDecoder()
      let buf = ''
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        let idx: number
        while ((idx = buf.indexOf('\n\n')) >= 0) {
          const frame = buf.slice(0, idx); buf = buf.slice(idx + 2)
          const line = frame.split('\n').find((l) => l.startsWith('data:'))
          if (line) onEvent(JSON.parse(line.slice(5)))
        }
      }
    })
    .catch(() => {})
  return () => ctrl.abort()
}
