import { useEffect, useState } from "react"
import type { ReactNode } from "react"
import {
  listKbDocuments, listKbChunks, deleteKbDocument, ingestKbText, reindexKb,
  getKbStructure, PARSER_OPTIONS, CHUNK_METHOD_OPTIONS,
  previewKbUpload, commitKbUpload,
  type KbDocument, type KbChunk, type KbStructNode, type UploadPreviewResp,
} from "./lib/api"

type KbView = "list" | "chunks" | "upload"

function progLabel(p: { stage: string; done: number; total: number }): string {
  if (p.stage === "upload") return "上传文件中…"
  if (p.stage === "chunked") return "解析完成,切块入库(" + p.total + " 块)…"
  if (p.stage === "embed") return "嵌入向量 " + p.done + " / " + p.total + " …"
  if (p.stage === "qdrant") return "写入向量索引…"
  return p.stage
}
function progPct(p: { stage: string; done: number; total: number }): number {
  if (p.stage === "upload") return 5
  if (p.stage === "chunked") return 10
  if (p.stage === "embed") return p.total > 0 ? Math.round(10 + (p.done / p.total) * 80) : 10
  return 95
}

export default function KbManager({ onBack, onOpenCompare }: { onBack?: () => void; onOpenCompare?: () => void }) {
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
  const [uMode, setUMode] = useState<"text" | "file">("text")
  const [uText, setUText] = useState("")
  const [uProductName, setUProductName] = useState("")   // 产品名称(必填,doc_id=它)
  const [uVersion, setUVersion] = useState("v1")
  const [uCategory, setUCategory] = useState("")
  const [uTitle, setUTitle] = useState("")
  const [uFile, setUFile] = useState<File | null>(null)
  const [uParser, setUParser] = useState("mineru")   // D70:上传默认解析=MinerU
  const [uMethod, setUMethod] = useState("structured")   // D70:切块方式,默认结构层级
  const [uChunkSize, setUChunkSize] = useState(1000)
  const [uOverlap, setUOverlap] = useState(200)
  const [uPreview, setUPreview] = useState<UploadPreviewResp | null>(null)
  const [uPreviewBusy, setUPreviewBusy] = useState(false)
  const [uBusy, setUBusy] = useState(false)
  const [uProg, setUProg] = useState<{ stage: string; done: number; total: number } | null>(null)
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

  // 目录(结构树)+ 当前目录筛选(按归一化标题匹配 chunk.section)
  const [structNodes, setStructNodes] = useState<KbStructNode[]>([])
  const [stFilter, setStFilter] = useState("")

  const loadStructure = async (docId: string) => {
    setStructNodes([]); setStFilter("")
    try {
      const r = await getKbStructure(docId)
      setStructNodes(r.nodes || [])
    } catch { /* 无结构(纯文本/未抽取)不报错 */ }
  }

  const openChunks = async (docId: string) => {
    setSelectedDoc(docId); setView("chunks"); setChunkPage(1)
    await Promise.all([loadChunks(docId, 1), loadStructure(docId)])
  }

  const norm = (s: string) => (s || "").replace(/\s+/g, "")
  const shownChunks = stFilter ? chunks.filter((c) => norm(c.section || "").includes(stFilter)) : chunks
  // 目录树只显示两级(部分>条,方案A);更深（一）/1./(1) 收敛进 chunk.section(检索/引用不受影响)
  const treeNodes = structNodes.filter((n) => n.level <= 2)

  // 上传预览:目录树(两级) + 节点→chunk 范围(同分支),与解析对比页同口径
  const normS = (s: string) => (s || "").replace(/\u3000/g, "").replace(/\s+/g, "")
  const withPaths = (nodes: KbStructNode[]): { node: KbStructNode; path: string }[] => {
    const stack: string[] = []
    return nodes.map((n) => {
      while (stack.length >= n.level) stack.pop()
      stack.push(n.title)
      return { node: n, path: stack.join(" > ") }
    })
  }
  const prevTree = uPreview ? withPaths(uPreview.outline).filter(({ node }) => node.level <= 2) : []
  const prevNodeChunks: Record<string, number[]> = {}
  for (const { path } of prevTree) {
    const ids: number[] = []
    uPreview?.chunks.forEach((c, idx) => {
      const s = normS(c.section), p = normS(path)
      if (s && (s.startsWith(p) || p.startsWith(s))) ids.push(idx + 1)
    })
    prevNodeChunks[path] = ids
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

  // D70:预览切块(不写库、不发嵌入)——用所选解析+切块方式,先看目录/切块内容
  const handlePreview = async () => {
    if (!uFile) { flash("请选择文件", false); return }
    setUPreviewBusy(true); setUPreview(null)
    try {
      const fd = new FormData()
      fd.append("file", uFile)
      fd.append("parser", uParser)
      fd.append("text_splitter", uMethod)
      if (uChunkSize > 0) fd.append("chunk_size", String(uChunkSize))
      if (uOverlap >= 0) fd.append("overlap", String(uOverlap))
      const r = await previewKbUpload(fd)
      if (r.ok) {
        setUPreview(r)
        flash(`预览完成: ${r.chunk_count} 块 · ${r.text_splitter} · ${r.parser}`)
      } else flash("预览失败: " + (r.err || "解析失败"), false)
    } catch (e: any) {
      flash("预览失败: " + (e?.message || ""), false)
    } finally { setUPreviewBusy(false) }
  }

  // D70:确认索引——把预览得到的 chunks/outline 提交写库(不再解析);force:同名产品内容不同时强制覆盖
  const handleCommit = async (opts?: { force?: boolean }) => {
    if (!uPreview) { flash("请先预览切块", false); return }
    if (!uPreview.chunks.length) { flash("预览为空,无法索引", false); return }
    if (!uProductName.trim()) { flash("请先填产品名称", false); return }
    setUBusy(true)
    try {
      setUProg({ stage: "chunked", done: 0, total: 1 })
      const r = await commitKbUpload({
        product_name: uProductName.trim(),
        title: uTitle || uProductName.trim(),
        version: uVersion || "v1",
        product_category: uCategory || undefined,
        doc_type: uPreview.doc_type || "policy_pdf",
        source: "upload-preview/" + uProductName.trim(),
        parser: uParser,
        text_splitter: uPreview.text_splitter,
        outline: uPreview.outline,
        chunks: uPreview.chunks,
        force: opts?.force,
      }, (p) => setUProg(p))
      setUProg(null)
      if (r.ok) {
        flash(`成功摄取: ${r.doc_id} (${r.chunks_written} chunks)`)
        setUFile(null); setUPreview(null); setUTitle(""); setUCategory("")
        setView("list"); loadDocs(1)
      } else if (r.conflict && !opts?.force) {
        const go = window.confirm(r.message + "\n\n确定覆盖该产品的旧文档？")
        if (go) { await handleCommit({ force: true }); return }
        flash("已取消")
      } else flash(r.message, false)
    } catch (e: any) {
      setUProg(null)
      flash("索引失败: " + (e?.message || ""), false)
    } finally { setUBusy(false) }
  }

  const handleUpload = async (opts?: { force?: boolean }) => {
    // 文本模式:直接上传(带切块方式/参数);force:同名产品内容不同时强制覆盖
    if (!uText.trim() || !uProductName.trim()) { flash("文档内容和产品名称不能为空", false); return }
    setUBusy(true)
    try {
      const r = await ingestKbText({
        text: uText,
        product_name: uProductName.trim(),
        version: uVersion || "v1",
        product_category: uCategory || undefined,
        title: uTitle || uProductName.trim(),
        doc_type: "text",
        text_splitter: uMethod,
        chunk_size: uChunkSize > 0 ? uChunkSize : undefined,
        overlap: uOverlap >= 0 ? uOverlap : undefined,
        force: opts?.force,
      })
      if (r.ok) {
        flash(`成功摄取: ${r.doc_id} (${r.chunks_written} chunks)`)
        setUText(""); setUProductName(""); setUVersion("v1"); setUCategory(""); setUTitle("")
        setView("list"); loadDocs(1)
      } else if (r.conflict && !opts?.force) {
        // 同名产品内容不同:让用户确认覆盖(force 后再提交)
        const go = window.confirm(r.message + "\n\n确定覆盖该产品的旧文档？")
        if (go) { await handleUpload({ force: true }); return }
        flash("已取消")
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

  const resetUpload = () => { setView("list"); setUFile(null); setUProg(null); setUPreview(null); setUProductName("") }

  return (
    <div className="kb-manager">
      <div className="kb-head">
        <div className="kb-head-left">
          {onBack && <button className="kb-back-btn" onClick={onBack}><svg className="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="15 18 9 12 15 6"/></svg> 返回对话</button>}
          <span className="kb-title">知识管理</span>
          {view === "list" && <span className="kb-subtitle">共 {total} 篇文档</span>}
          {view === "chunks" && <span className="kb-subtitle"><a className="kb-back" onClick={() => { setView("list"); loadDocs(page) }}>← 返回文档列表</a></span>}
          {view === "upload" && <span className="kb-subtitle"><a className="kb-back" onClick={resetUpload}>← 返回文档列表</a></span>}
        </div>
        <div className="kb-head-right">
          <button className="kb-btn" onClick={() => setView("upload")} disabled={view === "upload"}>上传文档</button>
          {onOpenCompare && <button className="kb-btn" onClick={onOpenCompare} title="同一文件多后端解析对比(不落库)">解析对比</button>}
          <button className="kb-btn kb-btn-danger" onClick={handleReindex} disabled={reindexBusy}>{reindexBusy ? "重建中…" : "重建索引"}</button>
          <button className="kb-btn" onClick={() => loadDocs(1)}>刷新</button>
        </div>
      </div>

      {msg && <div className={"kb-msg" + (msgOk ? " ok" : " err")}>{msg}</div>}

      {view === "list" && (
        <div className="kb-body">
          {loading && <div className="kb-loading">加载中…</div>}
          {error && <div className="kb-err">{error}</div>}
          {!loading && !error && docs.length === 0 && <div className="kb-empty">知识库为空,请上传文档</div>}
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
            {stFilter && <span className="kb-tag kb-tag-cat">目录筛选: {stFilter}<a className="kb-back" onClick={() => setStFilter("")} style={{ marginLeft: 6 }}>清除</a></span>}
          </div>
          {loading && <div className="kb-loading">加载中…</div>}
          <div className="doc-split">
            <div className="doc-structure">
              <div className="pdf-pane-head"><span>文档目录</span><span className="cmp-pane-sub">{treeNodes.length} 节点(两级)</span></div>
              <div className="cmp-tree">
                {treeNodes.length > 0 ? treeNodes.map((n, i) => (
                  <div key={i}
                       className={"cmp-node" + (stFilter === norm(n.title) ? " sel" : "")}
                       style={{ paddingLeft: (n.level - 1) * 16 }}
                       onClick={() => setStFilter(stFilter === norm(n.title) ? "" : norm(n.title))}
                       title={n.parent ? n.parent + " > " + n.title : n.title}>
                    <span className="cmp-node-glyph">{n.level <= 1 ? "▣" : "▢"}</span>
                    <span className="cmp-node-title">{n.title}</span>
                    {n.chunk_ids && n.chunk_ids.length > 0 && (
                      <span className="cmp-chunk-range-tag">§{n.chunk_ids[0]}{n.chunk_ids.length > 1 ? "–" + n.chunk_ids[n.chunk_ids.length - 1] : ""}({n.chunk_ids.length})</span>
                    )}
                    {n.page != null && <span className="cmp-page-tag">P{n.page}</span>}
                  </div>
                )) : <div className="cmp-hint">该文档无目录(未抽到结构)</div>}
              </div>
            </div>
            <div className="doc-chunks">
              {shownChunks.map((c) => (
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
              {shownChunks.length === 0 && <div className="kb-empty">该目录项下暂无切块</div>}
              {chunkTotalPages > 1 && (
                <div className="kb-pages">
                  <button className="kb-btn kb-btn-sm" disabled={chunkPage <= 1} onClick={() => { setChunkPage(chunkPage - 1); loadChunks(selectedDoc, chunkPage - 1) }}>上一页</button>
                  <span className="kb-page-info">第 {chunkPage} / {chunkTotalPages} 页</span>
                  <button className="kb-btn kb-btn-sm" disabled={chunkPage >= chunkTotalPages} onClick={() => { setChunkPage(chunkPage + 1); loadChunks(selectedDoc, chunkPage + 1) }}>下一页</button>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {view === "upload" && (
        <div className="kb-body">
          <div className="kb-form">
            <div className="kb-mode-row">
              <button className={"kb-btn" + (uMode === "text" ? " kb-btn-primary" : "")} onClick={() => { setUMode("text"); setUPreview(null) }}>粘贴文本</button>
              <button className={"kb-btn" + (uMode === "file" ? " kb-btn-primary" : "")} onClick={() => { setUMode("file"); setUPreview(null) }}>上传文件(PDF/DOCX/XLSX/MD/TXT)</button>
            </div>

            {uMode === "text" && (
              <>
                <div className="kb-form-field">
                  <label className="kb-form-label">产品名称 *</label>
                  <input className="kb-form-input" value={uProductName} onChange={(e) => setUProductName(e.target.value)} placeholder="例: 尊享e生2025" />
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
              </>
            )}

            {uMode === "file" && (
              <>
                <div className="kb-form-field">
                  <label className="kb-form-label">产品名称 *</label>
                  <input className="kb-form-input" value={uProductName} onChange={(e) => { setUProductName(e.target.value); setUPreview(null) }} placeholder="例: 尊享e生2025" />
                  {uFile && <div className="kb-hint">doc_id = 产品名称(唯一);同名产品内容不同会提示"是否覆盖",需确认</div>}
                </div>
                <div className="kb-form-field">
                  <label className="kb-form-label">选择文件 *</label>
                  <input className="kb-form-input" type="file" accept=".pdf,.docx,.xlsx,.md,.txt"
                         onChange={(e) => { setUFile(e.target.files?.[0] || null); setUPreview(null) }} />
                </div>
                <div className="kb-form-row">
                  <div className="kb-form-field">
                    <label className="kb-form-label">解析后端</label>
                    <select className="kb-form-input" value={uParser} onChange={(e) => { setUParser(e.target.value); setUPreview(null) }}>
                      {PARSER_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  </div>
                  <div className="kb-form-field">
                    <label className="kb-form-label">保险类别</label>
                    <input className="kb-form-input" value={uCategory} onChange={(e) => setUCategory(e.target.value)} placeholder="医疗险/重疾险/意外险" />
                  </div>
                </div>
                <div className="kb-note">提示:MinerU 需在 .env 配 MINERU_API_KEY 且消耗每日额度;纯文本条款一般选 pdfplumber/markitdown 即可。想看同一文件三路解析对比,点右上「解析对比」或用户菜单里的「解析对比」。</div>
              </>
            )}

            <div className="kb-form-row">
              <div className="kb-form-field">
                <label className="kb-form-label">切分方式</label>
                <select className="kb-form-input" value={uMethod}
                        onChange={(e) => { setUMethod(e.target.value); setUPreview(null) }}>
                  {CHUNK_METHOD_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </div>
              <div className="kb-form-field">
                <label className="kb-form-label">chunk_size(字符)</label>
                <input className="kb-form-input" type="number" value={uChunkSize} min={1}
                       onChange={(e) => { setUChunkSize(Number(e.target.value) || 0); setUPreview(null) }} />
              </div>
              <div className="kb-form-field">
                <label className="kb-form-label">overlap(字符)</label>
                <input className="kb-form-input" type="number" value={uOverlap} min={0}
                       disabled={uMethod === "structured"}
                       title={uMethod === "structured" ? "结构层级不使用 overlap(字符/段落才用)" : undefined}
                       onChange={(e) => { setUOverlap(Number(e.target.value) || 0); setUPreview(null) }} />
              </div>
            </div>
            <div className="kb-note">{uMethod === "structured"
              ? "结构层级按语义单元切(节/条/一、/1./(1)),上下文经 section 路径保留,不使用 overlap(已置灰);token 预算约460控制块大小。"
              : "字符/段落模式按 chunk_size 字符数分块,overlap 生效(相邻块重叠);选非结构(字符/段落)时无目录树关联。"} bge 嵌入上限约 512 token(≈400字),过大嵌入会截断。</div>

            <div className="kb-form-actions">
              {uMode === "file" ? (
                <>
                  <button className="kb-btn" onClick={handlePreview} disabled={uPreviewBusy || uBusy}>{uPreviewBusy ? "预览中…" : "预览切块"}</button>
                  <button className="kb-btn kb-btn-primary" onClick={() => handleCommit()} disabled={uBusy || !uPreview || !uProductName.trim()}>{uBusy ? "索引中…" : "确认并索引"}</button>
                  <button className="kb-btn" onClick={resetUpload}>取消</button>
                </>
              ) : (
                <>
                  <button className="kb-btn kb-btn-primary" onClick={() => handleUpload()} disabled={uBusy || !uProductName.trim()}>{uBusy ? "处理中…" : "上传并索引"}</button>
                  <button className="kb-btn" onClick={resetUpload}>取消</button>
                </>
              )}
            </div>
            {uBusy && uProg && (
              <div className="kb-progress">
                <div className="kb-progress-label">{progLabel(uProg)}</div>
                <div className="kb-progress-track"><div className="kb-progress-fill" style={{ width: progPct(uProg) + "%" }} /></div>
              </div>
            )}

          </div>
          {uMode === "file" && uPreview && (
            <div className="cmp-upload-split">
              <div className="cmp-tree-pane">
                <div className="cmp-pane-head">
                  <span>结构树 · {uPreview.chunk_count} 块 ({uPreview.text_splitter})</span>
                  <span className="cmp-pane-sub">{prevTree.length} 节点</span>
                </div>
                <div className="cmp-tree">
                  {uPreview.text_splitter !== "structured"
                    ? <div className="cmp-hint">非结构切分(字符/段落),无结构树</div>
                    : prevTree.length > 0 ? prevTree.map(({ node, path }, i) => {
                        const ids = prevNodeChunks[path] || []
                        return (
                          <div key={i} className="cmp-node" style={{ paddingLeft: (node.level - 1) * 16 }} title={path}>
                            <span className="cmp-node-glyph">{node.level <= 1 ? "▣" : "▢"}</span>
                            <span className="cmp-node-title">{node.title}</span>
                            {ids.length > 0 && <span className="cmp-chunk-range-tag">§{ids[0]}{ids.length > 1 ? "–" + ids[ids.length - 1] : ""}({ids.length})</span>}
                          </div>
                        )
                      }) : <div className="cmp-hint">该文件未识别到结构标题</div>}
                </div>
              </div>
              <div className="cmp-chunk-pane">
                <div className="cmp-pane-head">
                  <span>切块内容(预览)</span>
                  <span className="cmp-pane-sub">{uPreview.chunk_count} 块</span>
                </div>
                <div className="cmp-chunks">
                  {uPreview.chunks.length > 0 ? uPreview.chunks.map((c, i) => (
                    <div key={i} className="cmp-chunk" style={{ marginBottom: 8 }}>
                      <div className="cmp-chunk-meta">
                        <span className="cmp-chunk-idx">#{i + 1}</span>
                        {c.section ? <span className="kb-tag">{c.section}</span> : <span className="kb-tag">(无结构)</span>}
                        {c.title && c.title !== c.section && <span className="kb-tag">{c.title}</span>}
                      </div>
                      <div className="cmp-chunk-content clamp">{c.content}</div>
                    </div>
                  )) : <div className="cmp-hint">预览为空(解析失败或未切出块)</div>}
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
