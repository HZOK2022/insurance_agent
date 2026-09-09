# Contract v0 —— 前端依赖的 HTTP + SSE 契约

> 阶段0 契约先行:前端只依赖本契约。后端在阶段1起按此实现,契约稳定前不改前端。
> 事件类型与 docs/sqlite-schema.md 的注册表一致(模型可见 ⟺ 已记录)。

## 传输与鉴权
- 同源;dev 时 Vite 把 /api 代理到 http://127.0.0.1:8000(见 web/vite.config.ts)
- 鉴权(起步):Header `X-Internal-Token`(单机内网;阶段7 再加固)

## REST
- `GET /api/health` → `{ok:true}`
- `POST /api/sessions` body `{user_id}` → `{id, title, created_at}`
- `GET /api/sessions` → `[{id, title, created_at, updated_at}]`
- `POST /api/sessions/{id}/prompt` body `{text, client_time?}` → 202 `{accepted:true}`
- `GET /api/sessions/{id}/events`(SSE,text/event-stream)→ 逐条事件帧(见下)
- `GET /api/sessions/{id}/citation/{chunk_id}` → `{content, source, doc_id, version, section}`(点角标取原文)

## SSE 事件帧
每条 data 为 JSON:`{seq, type, ts, payload}`。type 与 payload 要点:

| type | payload |
|---|---|
| `user_message` | {text, client_time} |
| `retrieval` | {query, chunks:[{chunk_id,score,doc_id,version,section,source,content}]} |
| `assistant_chunk` | {delta} |
| `assistant_message` | {text, citations:[{idx, chunk_id}]} |
| `tool_call` | {tool, args} |
| `tool_result` | {tool, ok, result_truncated, error?} |
| `approval_request` | {tool, args, reason} |
| `approval_decision` | {status, decided_by?} |
| `usage` | {model, prompt_tokens, completion_tokens, cost_estimate} |
| `turn_start` / `turn_end` | {} |

## 引用角标(验收核心)
`assistant_message.citations[].chunk_id` → `GET .../citation/{chunk_id}` → 原文与来源。
前端:回答文本中的 `[n]` → 点击 → 取原文 → 弹层展示。

## 阶段0 mock
`web/src/lib/mock.ts` 模拟上述事件的时序流(默认 VITE_MOCK=1),前端无需后端也能"演一次假对话"。后端 mock 在 `app/main.py`(MODE=mock)。
