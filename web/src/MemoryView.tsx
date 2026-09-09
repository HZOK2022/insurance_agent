// -*- coding: utf-8 -*-
// 记忆管理面板(P2.3):三桶(用户/跨会话/会话)的查看/新增/编辑/遗忘/压实 + 注入帧预览。
// 数据来自 /api/memory(会话 token 鉴权,只操作自己);编辑=同 key 覆盖(留历史)。
import { useEffect, useState } from "react"
import { getMemory, saveMemory, forgetMemory, compactMemory, type MemoryEntry, type MemoryListResp } from "./lib/api"

// 桶展示顺序与标签
const BUCKET_ORDER: { key: string; label: string; hint: string }[] = [
  { key: "user", label: "用户记忆", hint: "当前用户的画像/偏好/使用习惯(按用户持久,每次会话注入)" },
  { key: "cross_session", label: "跨会话记忆", hint: "可复用经验/口径/知识结论/踩坑/缺口(按用户持久,每次会话注入)" },
  { key: "session", label: "会话记忆", hint: "仅当前会话有效(本会话限定,会话结束即失效)" },
]
const TYPE_COLOR: Record<string, string> = {
  profile: "#7c5cff", preference: "#3b82f6", habit: "#06b6d4",
  fact: "#22c55e", policy: "#f59e0b", lesson: "#ef4444", pending: "#94a3b8", redline: "#dc2626",
  instruction: "#8b5cf6", context: "#64748b",
}
const TARGET_TO_CATS: Record<string, string[]> = {
  user: ["profile", "preference", "habit"],
  cross_session: ["fact", "policy", "lesson", "pending"],
  session: ["instruction", "context"],
}

function TypeTag({ t }: { t: string }) {
  return <span className="mem-type" style={{ background: TYPE_COLOR[t] || "#64748b" }}>{t}</span>
}

interface EditState { target: string; category: string; key: string; content: string }

// 新增/编辑记忆表单(编辑时 target 锁定为该条目的桶,保存=同 key 覆盖)
function SaveForm({ sessionId, onDone, editing, onCancelEdit }: { sessionId: string | null; onDone: () => void; editing: EditState | null; onCancelEdit: () => void }) {
  const [target, setTarget] = useState("cross_session")
  const [category, setCategory] = useState("fact")
  const [key, setKey] = useState("")
  const [content, setContent] = useState("")
  const [msg, setMsg] = useState("")
  const [err, setErr] = useState("")
  const [busy, setBusy] = useState(false)
  const isEdit = !!editing

  // 点击某条"编辑"→ 预填表单(target 锁定为该桶)
  useEffect(() => {
    if (editing) {
      setTarget(editing.target); setCategory(editing.category)
      setKey(editing.key); setContent(editing.content)
    }
  }, [editing])

  useEffect(() => { if (!isEdit) setCategory(TARGET_TO_CATS[target][0] || "fact") }, [target, isEdit])

  const submit = async () => {
    if (!key.trim() || !content.trim()) { setErr("key 与 content 必填"); return }
    setBusy(true); setErr("")
    try {
      const r = await saveMemory({ target, category, key: key.trim(), content: content.trim(), session_id: target === "session" ? (sessionId || undefined) : undefined })
      setMsg(r.message); setKey(""); setContent(""); onDone(); onCancelEdit()
    } catch (e: any) { setErr(String(e.message || e)) }
    finally { setBusy(false) }
  }

  return (
    <div className="mem-form">
      <div className="mem-form-row">
        <label className="mem-label">存到</label>
        <select className="mem-input" value={target} disabled={isEdit} onChange={(e) => setTarget(e.target.value)}>
          {BUCKET_ORDER.filter((b) => b.key !== "session" || !!sessionId).map((b) => <option key={b.key} value={b.key}>{b.label}</option>)}
        </select>
        <label className="mem-label">类型</label>
        <select className="mem-input" value={category} onChange={(e) => setCategory(e.target.value)}>
          {(TARGET_TO_CATS[target] || []).map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
      </div>
      <div className="mem-form-row">
        <input className="mem-input" placeholder="key,如 称呼 / product:尊享e生:免赔额" value={key} onChange={(e) => setKey(e.target.value)} />
      </div>
      <div className="mem-form-row">
        <textarea className="mem-textarea" placeholder="content,如 回答前叫我大哥 / 尊享e生免赔额1万" value={content} onChange={(e) => setContent(e.target.value)} rows={3} />
      </div>
      <div className="mem-form-row mem-form-actions">
        {msg && <span className="mem-ok">{msg}</span>}{err && <span className="mem-err">{err}</span>}
        {isEdit && <button className="mem-btn" onClick={() => { onCancelEdit(); setMsg(""); setErr("") }}>取消</button>}
        <button className="mem-btn mem-primary" disabled={busy} onClick={submit}>{isEdit ? "更新" : "保存"}</button>
      </div>
    </div>
  )
}

export default function MemoryView({ sessionId, onBack }: { sessionId: string | null; onBack?: () => void }) {
  const [data, setData] = useState<MemoryListResp | null>(null)
  const [err, setErr] = useState("")
  const [loadErr, setLoadErr] = useState("")
  const [editing, setEditing] = useState<EditState | null>(null)

  const load = async () => {
    setLoadErr("")
    try { setData(await getMemory(sessionId || undefined)) }
    catch (e: any) { setLoadErr(String(e.message || e)); setData(null) }
  }
  useEffect(() => { load(); setEditing(null) }, [sessionId]) // eslint-disable-line

  const del = async (target: string, key: string) => {
    if (!window.confirm("确定遗忘「" + key + "」?(历史保留,标记已遗忘)")) return
    const removed = (data?.buckets?.[target] || []).find((e) => e.key === key)
    // 乐观更新:点击确认立即从列表移除,不等服务器;失败回滚
    setData((prev) => {
      if (!prev) return prev
      const b = prev.buckets[target] || []
      const r = b.find((e) => e.key === key)
      return { ...prev, buckets: { ...prev.buckets, [target]: b.filter((e) => e.key !== key) }, counts: { ...prev.counts, [target]: Math.max(0, (prev.counts[target] || 0) - (r?.content?.length || 0)) } }
    })
    try { await forgetMemory(target, key, target === "session" ? (sessionId || undefined) : undefined); await load() }
    catch (e: any) {
      // 回滚:恢复被遗忘的条目与计数
      setData((prev) => {
        if (!prev || !removed) return prev
        const b = prev.buckets[target] || []
        if (b.some((x) => x.key === key)) return prev
        return { ...prev, buckets: { ...prev.buckets, [target]: [...b, removed] }, counts: { ...prev.counts, [target]: (prev.counts[target] || 0) + (removed.content?.length || 0) } }
      })
      setErr(String(e.message || e))
    }
  }
  const compact = async (target: string) => {
    try { const r = await compactMemory(target, target === "session" ? (sessionId || undefined) : undefined); await load(); setErr(r.archived ? "已压实归档 " + r.archived + " 条" : "该桶已在阈值内,无需压缩") }
    catch (e: any) { setErr(String(e.message || e)) }
  }

  if (!data) {
    return (
      <div className="kb-manager">
        <div className="kb-head"><div className="kb-head-left">{onBack && <button className="kb-back-btn" onClick={onBack}><svg className="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="15 18 9 12 15 6"/></svg> 返回对话</button>}<span className="kb-title">我的记忆</span></div></div>
        <div className="kb-body"><div className="kb-empty">{loadErr || "加载记忆中…"}</div></div>
      </div>
    )
  }

  if (!data.enabled) {
    return (
      <div className="kb-manager">
        <div className="kb-head"><div className="kb-head-left">{onBack && <button className="kb-back-btn" onClick={onBack}><svg className="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="15 18 9 12 15 6"/></svg> 返回对话</button>}<span className="kb-title">我的记忆</span></div></div>
        <div className="kb-body"><div className="kb-empty">记忆系统未启用(需在 .env 设 MEMORY_ENABLED=true 并重启后端)。当前为只读提示,不作任何写入。</div></div>
      </div>
    )
  }

  const frames = data.frames || {}
  const counts = data.counts || {}
  const startEdit = (bucket: string, e: MemoryEntry) => {
    if (e.scope === "global") return
    setEditing({ target: bucket, category: e.type, key: e.key, content: e.content })
  }
  return (
    <div className="kb-manager">
      <div className="kb-head">
        <div className="kb-head-left">
          {onBack && <button className="kb-back-btn" onClick={onBack}><svg className="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="15 18 9 12 15 6"/></svg> 返回对话</button>}
          <span className="kb-title">我的记忆</span>
          <span className="kb-subtitle">三桶记忆(用户/跨会话/会话)· 每桶 ≤{data.limit} 字,超则压缩到 30%</span>
        </div>
        <div className="kb-head-right">
          <button className="kb-back-btn" onClick={load}>刷新</button>
          {err && <span className="mem-err" style={{ marginLeft: 8 }}>{err}</span>}
        </div>
      </div>
      <div className="kb-body">
        <div className="mem-save-wrap">
          <div className="kb-head"><span className="kb-subtitle">{editing ? "编辑记忆(同 key 覆盖,留历史)" : "新增一条记忆"}</span></div>
          <SaveForm sessionId={sessionId} onDone={load} editing={editing} onCancelEdit={() => setEditing(null)} />
        </div>
        <div className="mem-buckets">
          {BUCKET_ORDER.filter((b) => b.key !== "session" || sessionId).map((b) => {
            const entries = (data.buckets || {})[b.key] || []
            const count = counts[b.key] || 0
            return (
              <div key={b.key} className="mem-bucket">
                <div className="mem-bucket-head">
                  <span className="mem-bucket-title">{b.label}</span>
                  <span className="mem-bucket-meta">{count}/{data.limit} 字 · {entries.length} 条</span>
                  <span className="mem-bucket-hint">{b.hint}</span>
                  <span className="mem-bucket-actions">
                    <button className="mem-btn" onClick={() => compact(b.key)}>压实</button>
                  </span>
                </div>
                <div className="mem-entries">
                  {entries.length === 0 && <div className="mem-empty">暂无记忆</div>}
                  {entries.map((e: MemoryEntry, i: number) => (
                    <div key={e.key + "-" + i} className="mem-entry">
                      <TypeTag t={e.type} />
                      <span className="mem-entry-key">{e.key}</span>
                      {e.scope === "global" && <span className="kb-tag">global</span>}
                      {e.status === "archived" && <span className="mem-entry-archived">已归档</span>}
                      <span className="mem-entry-content">{e.content}</span>
                      <span className="mem-entry-meta">{e.confidence || ""}{e.updated_at ? " · " + e.updated_at.slice(0, 16).replace("T", " ") : ""}</span>
                      {e.scope !== "global" && (
                        <span className="mem-entry-actions">
                          <button className="mem-btn" onClick={() => startEdit(b.key, e)}>编辑</button>
                          <button className="mem-btn mem-danger" onClick={() => del(b.key, e.key)}>遗忘</button>
                        </span>
                      )}
                    </div>
                  ))}
                </div>
                {frames[b.key] && (
                  <details className="mem-frame">
                    <summary>注入帧预览(直接给模型读,标签见下)</summary>
                    <pre className="mem-frame-pre">{frames[b.key]}</pre>
                  </details>
                )}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
