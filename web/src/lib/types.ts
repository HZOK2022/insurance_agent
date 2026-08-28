// 契约 v0 的类型(与 docs/contract.md 对齐)
export interface SessionSummary { id: string; title: string; created_at: string; }

export type EventType =
  | 'user_message' | 'retrieval' | 'assistant_chunk' | 'assistant_message'
  | 'tool_call' | 'tool_result' | 'usage' | 'turn_start' | 'turn_end'

export interface Citation { idx: number; chunk_id: string }
export interface RetrievalChunk {
  chunk_id: string; score: number; doc_id: string; version: string;
  section: string; source: string; content: string
}
export interface SessionEvent {
  seq: number; type: EventType; ts: string; payload: Record<string, unknown>
}

// 前端渲染用的消息视图
export interface Message {
  id: string
  role: 'user' | 'assistant' | 'tool'
  text?: string
  citations?: Citation[]
  tool?: { name: string; args?: unknown; ok?: boolean; error?: string }
}
