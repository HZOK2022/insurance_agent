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
export interface LoginResp { token: string; expires_at: string; username: string; display_name: string; role: string }
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
export interface MeResp { username: string; display_name: string; role: string }
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

// ---- 记忆管理面板(P2.3)----
export interface MemoryEntry { id?: string; user_id?: string; bucket?: string; scope?: string; type: string; key: string; content: string; confidence?: string; status?: string; updated_at?: string }
export interface MemoryTargetMeta { label: string; cat: string[] }
export interface MemoryListResp { enabled: boolean; user_id: string; limit: number; buckets: Record<string, MemoryEntry[]>; frames: Record<string, string>; counts: Record<string, number>; targets?: Record<string, MemoryTargetMeta> }
export interface MemorySaveResp { ok: boolean; user_id: string; bucket: string; message: string; archived: number; entry?: any }
export const getMemory = (sessionId?: string) =>
  json<MemoryListResp>('/api/memory' + (sessionId ? '?session_id=' + encodeURIComponent(sessionId) : ''))
export const saveMemory = (body: { target: string; category: string; key: string; content: string; session_id?: string }) =>
  json<MemorySaveResp>('/api/memory', { method: 'POST', body: JSON.stringify(body) })
export const forgetMemory = (target: string, key: string, sessionId?: string) =>
  json<{ ok: boolean; message: string }>('/api/memory?target=' + encodeURIComponent(target) + '&key=' + encodeURIComponent(key) + (sessionId ? '&session_id=' + encodeURIComponent(sessionId) : ''), { method: 'DELETE' })
export const compactMemory = (target: string, sessionId?: string) =>
  json<{ ok: boolean; bucket: string; archived: number }>('/api/memory/compact?target=' + encodeURIComponent(target) + (sessionId ? '&session_id=' + encodeURIComponent(sessionId) : ''), { method: 'POST' })

// ---- 知识库管理 API (admin-only) ----
export interface KbDocument {
  doc_id: string; doc_type: string | null; product_category: string | null;
  chunk_count: number; last_updated: string | null
}
export interface KbDocumentListResp { total: number; page: number; page_size: number; items: KbDocument[] }
export const listKbDocuments = (page: number = 1, pageSize: number = 50) =>
  json<KbDocumentListResp>('/api/kb/documents?page=' + page + '&page_size=' + pageSize)

export interface KbChunk {
  chunk_id: string; doc_id: string; version: string; section: string | null;
  title: string | null; product_category: string | null; content: string; content_preview: string
}
export interface KbChunkListResp { doc_id: string; total: number; page: number; page_size: number; items: KbChunk[] }
export const listKbChunks = (docId: string, page: number = 1, pageSize: number = 100) =>
  json<KbChunkListResp>('/api/kb/documents/' + encodeURIComponent(docId) + '/chunks?page=' + page + '&page_size=' + pageSize)

export interface KbStructNode { level: number; title: string; page: number | null; parent: string; chunk_ids?: number[] }
export interface KbStructureResp { doc_id: string; nodes: KbStructNode[] }
export const getKbStructure = (docId: string) =>
  json<KbStructureResp>('/api/kb/documents/' + encodeURIComponent(docId) + '/structure')

export interface KbIngestTextReq { text: string; product_name: string; doc_id?: string; version?: string; doc_type?: string; product_category?: string; title?: string; source?: string; text_splitter?: string; chunk_size?: number; overlap?: number; chunk_max_tokens?: number; force?: boolean }
export interface KbIngestResp { ok: boolean; doc_id: string; chunks_written: number; chunks_embedded: number; message: string; conflict?: boolean }
export const ingestKbText = (body: KbIngestTextReq) =>
  json<KbIngestResp>('/api/kb/ingest/text', { method: 'POST', body: JSON.stringify(body) })

export interface KbDeleteResp { ok: boolean; doc_id: string; chunks_deleted: number; points_deleted: number; message: string }
export const deleteKbDocument = (docId: string) =>
  json<KbDeleteResp>('/api/kb/documents/' + encodeURIComponent(docId), { method: 'DELETE' })

export interface KbReindexResp { ok: boolean; total_chunks: number; embedded: number; message: string }
export const reindexKb = () =>
  json<KbReindexResp>('/api/kb/reindex', { method: 'POST' })

// ---- KB 文件上传 + 三路解析对比(不落库)----
export interface OutlineNode { level: number; title: string }
export interface ChunkViewNode { i: number; section: string; title: string; content: string }
export interface ParsePreviewItem {
  backend: string; ok: boolean; err: string;
  chars: number; lines: number; md_heads: number;
  part: number; article: number; subitem: number; numbered: number;
  chunks: number; section_fill_pct: number; avg_path_len: number; overlong: number;
  excerpt: string; outline: OutlineNode[]; text: string; chunks_view: ChunkViewNode[]; elapsed_ms: number
}
export interface ParsePreviewResp { file_name: string; items: ParsePreviewItem[] }
export interface KbIngestFileResp extends KbIngestResp { parser: string }

async function formPost<T>(path: string, fd: FormData): Promise<T> {
  const r = await fetch(BASE + path, { method: 'POST', headers: authHeaders(), body: fd })
  if (r.status === 400) {
    let detail: string | undefined
    try { const d = await r.json(); detail = d?.detail } catch { /* noop */ }
    throw new Error(detail || path + ' -> 400')
  }
  if (!r.ok) throw new Error(path + ' -> ' + r.status)
  return r.json() as Promise<T>
}

export interface IngestProgress { stage: string; done: number; total: number }
// 上传摄取 = SSE:逐帧推进度(chunked/embed/qdrant) → done|error
export async function ingestKbFile(fd: FormData, onProgress?: (p: IngestProgress) => void): Promise<KbIngestFileResp> {
  const r = await fetch(BASE + '/api/kb/ingest/file', { method: 'POST', headers: authHeaders(), body: fd })
  if (!r.ok) {
    let detail: string | undefined
    try { const d = await r.json(); detail = d?.detail } catch { /* noop */ }
    throw new Error(detail || '/api/kb/ingest/file -> ' + r.status)
  }
  if (!r.body) throw new Error('摄取失败: 无响应流')
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
      if (!line) continue
      const ev = JSON.parse(line.slice(5))
      if (ev.type === 'progress' && onProgress) onProgress(ev as IngestProgress)
      else if (ev.type === 'done') return ev as KbIngestFileResp
      else if (ev.type === 'error') throw new Error(ev.message || '摄取失败')
    }
  }
  throw new Error('摄取中断: 连接结束未收到完成事件')
}
export const previewKbParse = (fd: FormData) => formPost<ParsePreviewResp>('/api/kb/parse/preview', fd)

export const PARSER_OPTIONS: { value: string; label: string }[] = [
  { value: 'auto', label: 'auto · 自动回退链(MinerU→MarkItDown→pdfplumber)' },
  { value: 'mineru', label: 'mineru · MinerU 在线(需 MINERU_API_KEY,耗每日额度)' },
  { value: 'markitdown', label: 'markitdown · MarkItDown(本地)' },
  { value: 'pdfplumber', label: 'pdfplumber · PDF 直抽(本地,仅 pdf)' },
  { value: 'native', label: 'native · 原库直读(仅 docx/xlsx)' },
]

export const CHUNK_METHOD_OPTIONS: { value: string; label: string }[] = [
  { value: 'structured', label: '结构层级(默认)' },
  { value: 'character', label: '按字符数量' },
  { value: 'paragraph', label: '按段落' },
]

// ---- 上传前"预览切块"→"确认索引"(D70):预览复用,避免二次解析 ----
export interface UploadChunkNode { section: string; title: string; content: string }
export interface UploadPreviewResp {
  ok: boolean; err: string; doc_type: string; parser: string; text_splitter: string;
  chunk_count: number; chunk_size: number; overlap: number;
  outline: KbStructNode[]; chunks: UploadChunkNode[]
}
export interface IngestCommitReq {
  product_name: string; doc_id?: string; title?: string; version?: string; product_category?: string;
  doc_type?: string; source?: string; parser?: string; text_splitter?: string;
  outline: KbStructNode[]; chunks: UploadChunkNode[]; force?: boolean
}
export const previewKbUpload = (fd: FormData) => formPost<UploadPreviewResp>('/api/kb/ingest/preview', fd)
// commit = SSE:写库+嵌入进度逐帧
export async function commitKbUpload(body: IngestCommitReq, onProgress?: (p: IngestProgress) => void): Promise<KbIngestResp> {
  const r = await fetch(BASE + '/api/kb/ingest/commit', { method: 'POST', headers: { ...authHeaders(), 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  if (!r.ok) {
    let detail: string | undefined
    try { const d = await r.json(); detail = d?.detail } catch { /* noop */ }
    throw new Error(detail || '/api/kb/ingest/commit -> ' + r.status)
  }
  if (!r.body) throw new Error('索引失败: 无响应流')
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
      if (!line) continue
      const ev = JSON.parse(line.slice(5))
      if (ev.type === 'progress' && onProgress) onProgress(ev as IngestProgress)
      else if (ev.type === 'done') return ev as KbIngestResp
      else if (ev.type === 'error') throw new Error(ev.message || '索引失败')
    }
  }
  throw new Error('索引中断: 连接结束未收到完成事件')
}
