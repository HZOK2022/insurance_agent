export interface Session { id: string; title: string; user_id: string; created_at: string }
export interface PEvent { type: string; payload: any; ts?: string }
export interface Citation { idx: number; chunk_id: string }
export interface Source { chunk_id: string; title: string; content: string }

const BASE = '' // 同源;Vite 已代理 /api

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(BASE + path, { headers: { 'Content-Type': 'application/json' }, ...init })
  if (!r.ok) throw new Error(path + ' -> ' + r.status)
  return r.json() as Promise<T>
}

export const listSessions = () => json<Session[]>('/api/sessions')
export const createSession = (user_id: string) => json<Session>('/api/sessions', { method: 'POST', body: JSON.stringify({ user_id }) })
export const listEvents = (sid: string) => json<PEvent[]>('/api/sessions/' + sid + '/events')
export const deleteSession = async (sid: string): Promise<void> => { const r = await fetch(BASE + '/api/sessions/' + sid, { method: 'DELETE' }); if (!r.ok) throw new Error('del ' + sid + ' -> ' + r.status) }
export const renameSession = (sid: string, title: string) => json<Session>('/api/sessions/' + sid, { method: 'PATCH', body: JSON.stringify({ title }) })
export interface ApiConfig { context_window: number; model: string; compaction_threshold_ratio: number; compaction_retain_ratio: number; compaction_max_tokens: number; max_tool_result_chars: number }
export const getConfig = () => json<ApiConfig>('/api/config')
export const getCitation = (sid: string, cid: string) =>
  json<{ content: string; source: string; doc_id: string; version: string; section: string }>(
    '/api/sessions/' + sid + '/citation/' + encodeURIComponent(cid))
export type ApprovalStatus = 'approve' | 'reject' | 'defer'
export interface ApprovalDecisionIn { request_id: string; status: ApprovalStatus; edited_args?: any | null; reason?: string; decided_by?: string }
export const submitApproval = (sid: string, d: ApprovalDecisionIn) =>
  json<{ ok: boolean; request_id: string; status: string }>('/api/sessions/' + sid + '/approval', { method: 'POST', body: JSON.stringify(d) })

// POST prompt 响应为 SSE 帧流:逐条 data: {...} 回调 onEvent
export function sendPrompt(sid: string, text: string, onEvent: (e: PEvent) => void, model?: string): Promise<void> {
  return fetch(BASE + '/api/sessions/' + sid + '/prompt', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(model ? { text, model } : { text }),
  }).then(async (r) => {
    if (!r.body) return
    const reader = r.body.getReader()
    const dec = new TextDecoder()
    let buf = ''
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buf += dec.decode(value, { stream: true })
      let i
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const frame = buf.slice(0, i); buf = buf.slice(i + 2)
        const line = frame.split('\n').find((l) => l.startsWith('data:'))
        if (line) onEvent(JSON.parse(line.slice(5)))
      }
    }
  })
}
