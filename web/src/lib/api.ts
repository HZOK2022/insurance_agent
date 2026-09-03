export interface Session { id: string; title: string; user_id: string; created_at: string; last_ts?: string }
export interface PEvent { type: string; payload: any; ts?: string }
export interface Citation { idx: number; chunk_id: string }
export interface Source { chunk_id: string; title: string; content: string }

const BASE = '' // 同源;Vite 已代理 /api
import { getToken } from './auth'
// 接口鉴权:Bearer token(localStorage 或 sessionStorage 的 'api_token';空则未启用鉴权)
const authHeaders = (): Record<string, string> => {
  const t = getToken()
  return t ? { Authorization: 'Bearer ' + t } : {}
}

// ---- 鉴权:登录 / 登出 ----
export interface LoginBody { username: string; password: string; remember: boolean }
export interface LoginResp { token: string; expires_at: string; username: string; display_name: string }
export async function login(body: LoginBody): Promise<LoginResp> {
  const r = await fetch(BASE + '/api/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (r.status === 401) throw new Error('账号或密码错误')
  if (!r.ok) throw new Error('/api/login -> ' + r.status)
  return r.json() as Promise<LoginResp>
}
export async function logout(): Promise<void> {
  const r = await fetch(BASE + '/api/logout', { method: 'POST', headers: authHeaders() })
  if (!r.ok && r.status !== 401) throw new Error('/api/logout -> ' + r.status)
}
export interface MeResp { username: string; display_name: string }
export async function getMe(): Promise<MeResp> {
  return json<MeResp>('/api/me')
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(BASE + path, { headers: { 'Content-Type': 'application/json', ...authHeaders() }, ...init })
  if (r.status === 401) throw new Error(path + ' -> 401 未授权')
  if (!r.ok) throw new Error(path + ' -> ' + r.status)
  return r.json() as Promise<T>
}

export const listSessions = () => json<Session[]>('/api/sessions')
export const createSession = (user_id: string) => json<Session>('/api/sessions', { method: 'POST', body: JSON.stringify({ user_id }) })
export const listEvents = (sid: string) => json<PEvent[]>('/api/sessions/' + sid + '/events')
export const deleteSession = async (sid: string): Promise<void> => { const r = await fetch(BASE + '/api/sessions/' + sid, { method: 'DELETE' }); if (!r.ok) throw new Error('del ' + sid + ' -> ' + r.status) }
export const renameSession = (sid: string, title: string) => json<Session>('/api/sessions/' + sid, { method: 'PATCH', body: JSON.stringify({ title }) })
// 清理"从没发过消息"的会话(切走即弃);keep=当前激活会话 id,豁免不删
export const pruneEmptySessions = (keep: string = '') =>
  json<{ pruned: number }>('/api/sessions/prune-empty?keep=' + encodeURIComponent(keep), { method: 'POST' })
export interface ApiConfig { context_window: number; model: string; compaction_threshold_ratio: number; compaction_retain_ratio: number; compaction_max_tokens: number; max_tool_result_chars: number }
export const getConfig = () => json<ApiConfig>('/api/config')
export const getCitation = (sid: string, cid: string) =>
  json<{ content: string; source: string; doc_id: string; version: string; section: string }>(
    '/api/sessions/' + sid + '/citation/' + encodeURIComponent(cid))
export type ApprovalStatus = 'approve' | 'reject' | 'defer'
export interface ApprovalDecisionIn { request_id: string; status: ApprovalStatus; edited_args?: any | null; reason?: string; decided_by?: string }
export const submitApproval = (sid: string, d: ApprovalDecisionIn) =>
  json<{ ok: boolean; request_id: string; status: string }>('/api/sessions/' + sid + '/approval', { method: 'POST', body: JSON.stringify(d) })

// 阶段6:审计查询视图 + 可观测(读 events,只读)
export interface AuditItem { session_id: string; title: string; user_id: string; ts: string; question: string; answer: any[]; citations: any[]; model: string | null; prompt_tokens: number; completion_tokens: number; cost: number | null; elapsed_ms: number | null; reason: string | null; retrievals: number; approvals: number; retries: number; error: boolean }
export interface Metrics { turns: number; prompt_tokens: number; completion_tokens: number; total_tokens: number; cost: number | null; errors: number; retries: number; approvals: number; avg_ttft_ms: number; avg_tps: number }
export const getAudit = (sid: string) => json<{ count: number; items: AuditItem[] }>('/api/audit?session_id=' + encodeURIComponent(sid))
export const getSessionMetrics = (sid: string) => json<Metrics>('/api/observability/' + encodeURIComponent(sid))
export const getObservability = () => json<{ totals: Metrics & { sessions: number }; per_session: any[] }>('/api/observability')


// 显式"停止":置后端中止位(不是直接断流——断流后 Starlette 不保证 close 底层生成器,后端会白跑完这一轮)。
// 置位后后端在下一个 step/chunk 边界收尾并照常推 turn_end,前端因此能拿到完整终结事件。
export const abortPrompt = (sid: string) =>
  json<{ ok: boolean; session_id: string }>('/api/sessions/' + sid + '/abort', { method: 'POST' })

// POST prompt 响应为 SSE 帧流:逐条 data: {...} 回调 onEvent
// signal:仅作"兜底断流"用(后端迟迟不收尾时强制断开),正常停止走 abortPrompt。
export function sendPrompt(sid: string, text: string, onEvent: (e: PEvent) => void, model?: string, signal?: AbortSignal): Promise<void> {
  return fetch(BASE + '/api/sessions/' + sid + '/prompt', {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(model ? { text, model } : { text }),
    signal,
  }).then(async (r) => {
    if (!r.ok) throw new Error('/prompt -> ' + r.status)
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
