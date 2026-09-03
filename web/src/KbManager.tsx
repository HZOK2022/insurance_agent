import { useEffect, useState } from "react"
import type { ReactNode } from "react"
import { listKbDocuments, listKbChunks, deleteKbDocument, ingestKbText, reindexKb, type KbDocument, type KbChunk } from "./lib/api"

type KbView = "list" | "chunks" | "upload"

export default function KbManager({ onBack }: { onBack?: () => void }) {
  const [docs, setDocs] = useState<KbDocument[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")
  const [view, setView] = useState<KbView>("list")
  const [selectedDoc, setSelectedDoc] = useState<string>("")
  const [chunks, setChunks] = useState<KbChunk[]>([])
  const [chunkTotal, setChunkTotal] = useState(0)
  const [chunkPage, setChunkPage] = useState(1)
  const [msg, setMsg] = useState("")
  const [msgOk, setMsgOk] = useState(true)
  const PAGE_SIZE = 50

  // Upload form
  const [uText, setUText] = useState("")
  const [uDocId, setUDocId] = useState("")
  const [uVersion, setUVersion] = useState("v1")
  const [uCategory, setUCategory] = useState("")
  const [uTitle, setUTitle] = useState("")
  const [uBusy, setUBusy] = useState(false)
  const [reindexBusy, setReindexBusy] = useState(false)

  const flash = (m: string, ok: boolean = true) => { setMsg(m); setMsgOk(ok); setTimeout(() => setMsg(""), 5000) }

  const loadDocs = async (p: number = 1) => {
    setLoading(true); setError("")
    try {
      const r = await listKbDocuments(p, PAGE_SIZE)
      setDocs(r.items); setTotal(r.total); setPage(p)
    } catch (e: any) {
      setError(e?.message || "加载失败"); setDocs([])
    } finally { setLoading(false) }
  }

  useEffect(() => { loadDocs() }, []) // eslint-disable-line

  const loadChunks = async (docId: string, p: number = 1) => {
    setLoading(true)
    try {
      const r = await listKbChunks(docId, p, 100)
      setChunks(r.items); setChunkTotal(r.total); setChunkPage(p)
    } catch (e: any) {
      flash("加载chunks失败: " + (e?.message || ""), false)
    } finally { setLoading(false) }
  }

  const openChunks = async (docId: string) => {
    setSelectedDoc(docId); setView("chunks"); setChunkPage(1)
    await loadChunks(docId, 1)
  }

  const handleDelete = async (docId: string) => {
    if (!window.confirm(`确定删除文档「${docId}」？删除后不可恢复。`)) return
    try {
      const r = await deleteKbDocument(docId)
      if (r.ok) flash(`已删除: ${docId} (${r.chunks_deleted} chunks)`)
      else flash(r.message, false)
      loadDocs(page)
    } catch (e: any) {
      flash("删除失败: " + (e?.message || ""), false)
    }
  }

  const handleUpload = async () => {
    if (!uText.trim() || !uDocId.trim()) { flash("文档内容和文档ID不能为空", false); return }
    setUBusy(true)
    try {
      const r = await ingestKbText({
        text: uText,
        doc_id: uDocId.trim(),
        version: uVersion || "v1",
        product_category: uCategory || undefined,
        title: uTitle || undefined,
        doc_type: "text",
      })
      if (r.ok) {
        flash(`成功摄取: ${r.doc_id} (${r.chunks_written} chunks)`)
        setUText(""); setUDocId(""); setUVersion("v1"); setUCategory(""); setUTitle("")
        setView("list"); loadDocs(1)
      } else flash(r.message, false)
    } catch (e: any) {
      flash("上传失败: " + (e?.message || ""), false)
    } finally { setUBusy(false) }
  }

  const handleReindex = async () => {
    if (!window.confirm("确定全量重建索引？此操作可能耗时较长(取决于知识库大小)，重建期间检索不会中断。")) return
    setReindexBusy(true)
    try {
      const r = await reindexKb()
      flash(r.message)
    } catch (e: any) {
      flash("重建失败: " + (e?.message || ""), false)
    } finally { setReindexBusy(false) }
  }

  const totalPages = Math.ceil(total / PAGE_SIZE)
  const chunkTotalPages = Math.ceil(chunkTotal / 100)

  return (
    <div className="kb-manager">
      <div className="kb-head">
        <div className="kb-head-left">
          {onBack && <button className="kb-back-btn" onClick={onBack}><svg className="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="15 18 9 12 15 6"/></svg> 返回对话</button>}
          <span className="kb-title">知识管理</span>
          {view === "list" && <span className="kb-subtitle">共 {total} 篇文档</span>}
          {view === "chunks" && <span className="kb-subtitle"><a className="kb-back" onClick={() => { setView("list"); loadDocs(page) }}>← 返回文档列表</a></span>}
          {view === "upload" && <span className="kb-subtitle"><a className="kb-back" onClick={() => setView("list")}>← 返回文档列表</a></span>}
        </div>
        <div className="kb-head-right">
          <button className="kb-btn" onClick={() => setView("upload")} disabled={view === "upload"}>上传文档</button>
          <button className="kb-btn kb-btn-danger" onClick={handleReindex} disabled={reindexBusy}>{reindexBusy ? "重建中…" : "重建索引"}</button>
          <button className="kb-btn" onClick={() => loadDocs(1)}>刷新</button>
        </div>
      </div>

      {msg && <div className={"kb-msg" + (msgOk ? " ok" : " err")}>{msg}</div>}

      {view === "list" && (
        <div className="kb-body">
          {loading && <div className="kb-loading">加载中…</div>}
          {error && <div className="kb-err">{error}</div>}
          {!loading && !error && docs.length === 0 && <div className="kb-empty">知识库为空，请上传文档</div>}
          {docs.map((d) => (
            <div key={d.doc_id} className="kb-doc-row">
              <div className="kb-doc-info" onClick={() => openChunks(d.doc_id)}>
                <span className="kb-doc-name">{d.doc_id}</span>
                <span className="kb-doc-meta">
                  <span className="kb-tag">{d.doc_type || "未知类型"}</span>
                  {d.product_category && <span className="kb-tag kb-tag-cat">{d.product_category}</span>}
                  <span className="kb-chunk-count">{d.chunk_count} 块</span>
                  {d.last_updated && <span className="kb-time">更新于 {new Date(d.last_updated).toLocaleDateString()}</span>}
                </span>
              </div>
              <div className="kb-doc-actions">
                <button className="kb-btn kb-btn-sm" onClick={() => openChunks(d.doc_id)}>查看</button>
                <button className="kb-btn kb-btn-sm kb-btn-danger" onClick={() => handleDelete(d.doc_id)}>删除</button>
              </div>
            </div>
          ))}
          {totalPages > 1 && (
            <div className="kb-pages">
              <button className="kb-btn kb-btn-sm" disabled={page <= 1} onClick={() => loadDocs(page - 1)}>上一页</button>
              <span className="kb-page-info">第 {page} / {totalPages} 页</span>
              <button className="kb-btn kb-btn-sm" disabled={page >= totalPages} onClick={() => loadDocs(page + 1)}>下一页</button>
            </div>
          )}
        </div>
      )}

      {view === "chunks" && (
        <div className="kb-body">
          <div className="kb-chunk-head">
            <span className="kb-chunk-title">文档: {selectedDoc}</span>
            <span className="kb-chunk-total">共 {chunkTotal} 个chunks</span>
          </div>
          {loading && <div className="kb-loading">加载中…</div>}
          {chunks.map((c) => (
            <div key={c.chunk_id} className="kb-chunk-row">
              <div className="kb-chunk-id">{c.chunk_id}</div>
              <div className="kb-chunk-meta">
                {c.version && <span className="kb-tag">v{c.version}</span>}
                {c.section && <span className="kb-tag">{c.section}</span>}
                {c.title && <span className="kb-tag">{c.title}</span>}
              </div>
              <div className="kb-chunk-preview">{c.content_preview}</div>
            </div>
          ))}
          {chunkTotalPages > 1 && (
            <div className="kb-pages">
              <button className="kb-btn kb-btn-sm" disabled={chunkPage <= 1} onClick={() => { setChunkPage(chunkPage - 1); loadChunks(selectedDoc, chunkPage - 1) }}>上一页</button>
              <span className="kb-page-info">第 {chunkPage} / {chunkTotalPages} 页</span>
              <button className="kb-btn kb-btn-sm" disabled={chunkPage >= chunkTotalPages} onClick={() => { setChunkPage(chunkPage + 1); loadChunks(selectedDoc, chunkPage + 1) }}>下一页</button>
            </div>
          )}
        </div>
      )}

      {view === "upload" && (
        <div className="kb-body">
          <div className="kb-form">
            <div className="kb-form-field">
              <label className="kb-form-label">文档ID *</label>
              <input className="kb-form-input" value={uDocId} onChange={(e) => setUDocId(e.target.value)} placeholder="例: 尊享e生2025条款" />
            </div>
            <div className="kb-form-row">
              <div className="kb-form-field">
                <label className="kb-form-label">版本</label>
                <input className="kb-form-input" value={uVersion} onChange={(e) => setUVersion(e.target.value)} placeholder="v1" />
              </div>
              <div className="kb-form-field">
                <label className="kb-form-label">保险类别</label>
                <input className="kb-form-input" value={uCategory} onChange={(e) => setUCategory(e.target.value)} placeholder="医疗险/重疾险/意外险" />
              </div>
            </div>
            <div className="kb-form-field">
              <label className="kb-form-label">标题</label>
              <input className="kb-form-input" value={uTitle} onChange={(e) => setUTitle(e.target.value)} placeholder="文档显示标题(可选)" />
            </div>
            <div className="kb-form-field">
              <label className="kb-form-label">文档内容 *</label>
              <textarea className="kb-form-textarea" value={uText} onChange={(e) => setUText(e.target.value)} placeholder="粘贴文档内容(文本)…" rows={12} />
            </div>
            <div className="kb-form-actions">
              <button className="kb-btn kb-btn-primary" onClick={handleUpload} disabled={uBusy}>{uBusy ? "上传中…" : "上传并索引"}</button>
              <button className="kb-btn" onClick={() => setView("list")}>取消</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}