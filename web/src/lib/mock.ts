import type { Message } from './types'

// 阶段0 mock:模拟一次假对话(检索 + 工具卡片 + 流式回答 + 引用角标)
// 结构模仿契约事件;后续由后端 SSE 替换,前端逻辑不变。
export const MOCK_SESSIONS = [
  { id: 's1', title: '重疾险责任免除咨询', created_at: '2026-03-01' },
  { id: 's2', title: '百万医疗免赔额', created_at: '2026-02-28' },
]

const CHUNKS = [
  { chunk_id: 'P10086:v3.2:section4:12', score: 0.93, doc_id: 'P10086', version: 'v3.2', section: 'section4', source: 'product/P10086/条款v3.2.pdf', content: '【责任免除】因下列情形之一导致被保险人发生保险事故的,本公司不承担给付保险金责任:……(原文节选)' },
]

const REPLY = '根据《XX重疾险条款 v3.2》(责任免除,第 4.1 条),您描述的情形属于责任免除范围,建议向客户明确说明该项不赔付。如投保前已存在相关既往症,投保时需如实告知。[1]'

// 以微秒间隔把一条回答"流式"推送出去(模拟 assistant_chunk)
export function streamMockReply(prompt: string, emit: (m: Message) => void, done: () => void) {
  // 1) 工具调用卡片:检索知识库
  emit({ id: 't1', role: 'tool', tool: { name: 'search_knowledge', ok: true, error: undefined } })
  // 2) 流式回答
  let i = 0
  const timer = setInterval(() => {
    i += 3
    if (i >= REPLY.length) {
      clearInterval(timer)
      emit({ id: 'a1', role: 'assistant', text: REPLY, citations: [{ idx: 1, chunk_id: CHUNKS[0].chunk_id }] })
      done()
    } else {
      emit({ id: 'a1', role: 'assistant', text: REPLY.slice(0, i) })
    }
  }, 40)
}

export function mockChunk(chunkId: string) {
  return CHUNKS.find((c) => c.chunk_id === chunkId) || null
}
