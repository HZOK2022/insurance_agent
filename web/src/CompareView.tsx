import { useMemo, useState } from "react"
import { previewKbParse, type ParsePreviewItem, type OutlineNode } from "./lib/api"
import { inBranch } from "./lib/branch"

const BACKENDS: { key: string; label: string; note: string }[] = [
  { key: "mineru", label: "MinerU", note: "在线 / 耗额度" },
  { key: "markitdown", label: "MarkItDown", note: "本地" },
  { key: "pdfplumber", label: "pdfplumber", note: "本地" },
  { key: "native", label: "原库 native", note: "docx/xlsx" },
]

function fmt(n: number): string { return n == null ? "—" : String(n) }
function fmtDurMs(ms: number): string { return ms >= 1000 ? (ms / 1000).toFixed(1) + "s" : ms + "ms" }
const labelOf = (b: string) => BACKENDS.find((x) => x.key === b)?.label || b
const emptyRow = (b: string) => ({ backend: b, ok: false, err: "解析中…", chars: 0, lines: 0, md_heads: 0, part: 0, article: 0, subitem: 0, numbered: 0, chunks: 0, section_fill_pct: 0, avg_path_len: 0, overlong: 0, excerpt: "", outline: [], text: "", chunks_view: [], elapsed_ms: -1 })

// 给 outline 节点补"完整路径"(按 level 弹栈),供树→chunk 过滤匹配 section 前缀
function withPaths(nodes: OutlineNode[]): { node: OutlineNode; path: string }[] {
  const stack: string[] = []
  return nodes.map((n) => {
    while (stack.length >= n.level) stack.pop()
    stack.push(n.title)
    return { node: n, path: stack.join(" > ") }
  })
}

export default function CompareView({ onBack, onOpenKb }: { onBack?: () => void; onOpenKb?: () => void }) {
  const [file, setFile] = useState<File | null>(null)
  const [checks, setChecks] = useState<Record<string, boolean>>({ mineru: false, markitdown: true, pdfplumber: true, native: false })
  const [busy, setBusy] = useState(false)
  const [busyBackends, setBusyBackends] = useState<string[]>([])
  const [err, setErr] = useState("")
  const [rows, setRows] = useState<ParsePreviewItem[] | null>(null)
  const [active, setActive] = useState<string>("")
  const [expanded, setExpanded] = useState<Record<number, boolean>>({})
  const [filterPath, setFilterPath] = useState("")

  const run = async () => {
    if (!file) { setErr("请先选择要对比的文件(pdf/docx/xlsx)"); return }
    const sel = BACKENDS.filter((b) => checks[b.key]).map((b) => b.key)
    if (sel.length === 0) { setErr("请至少勾选一个解析后端"); return }
    setBusy(true); setErr(""); setFilterPath(""); setExpanded({})
    setRows(sel.map((b) => emptyRow(b)))
    setBusyBackends(sel)
    // 各后端并发跑:本地腿先出结果即时上屏,MinerU 慢不拖累其它
    let remaining = sel.length
    sel.forEach((b) => {
      ;(async () => {
        try {
          const fd = new FormData()
          fd.append("file", file)
          fd.append("backends", b)
          const r = await previewKbParse(fd)
          const item = r.items[0]
          setRows((prev) => (prev || []).map((x) => (x.backend === b ? item : x)))
          if (item.ok) setActive((a) => (a || item.backend))
        } catch (e: any) {
          setRows((prev) => (prev || []).map((x) => (x.backend === b ? { ...x, err: e?.message || "请求失败" } : x)))
        } finally {
          setBusyBackends((prev) => prev.filter((x) => x !== b))
          remaining -= 1
          if (remaining <= 0) setBusy(false)
        }
      })()
    })
  }

  const okRow = rows?.find((r) => r.backend === active)
  // 表格下方只列"已结束的真失败";解析中的占位行由表格行/页签的"解析中…"体现,不在下方重复
  const failRows = (rows || []).filter((r) => !r.ok && !busyBackends.includes(r.backend))

  // 目录树两级(方案A):(部分>条);更深（一）/1./(1) 收敛进 chunk.section;对比页同口径
  const tree = useMemo(() => (okRow ? withPaths(okRow.outline).filter(({ node }) => node.level <= 2) : []), [okRow])
  const chunks = okRow?.chunks_view || []
  // 每节点 → 该分支切块编号(section 以节点路径为前缀,同下方过滤口径),供目录标记
  const nodeChunks = useMemo(() => {
    const m: Record<string, number[]> = {}
    for (const { path } of tree) {
      const ids: number[] = []
      for (const c of chunks) {
        // 段级前缀:块属于该节点分支(祖先块不反向挂入,避免每个节点都关联到根标题块)
        if (inBranch(c.section, path)) ids.push(c.i)
      }
      m[path] = ids
    }
    return m
  }, [tree, chunks])
  // 子树关联:点某节点 → section 以该节点路径为前缀的块(容器(条)下含更深（一)/1. 的块)
const shownChunks = filterPath ? chunks.filter((c) => inBranch(c.section, filterPath)) : chunks

  return (
    <div className="cmp-page">
      <div className="cmp-head">
        <div className="cmp-head-left">
          {onBack && <button className="kb-back-btn" onClick={onBack}><svg className="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="15 18 9 12 15 6"/></svg> 返回对话</button>}
          {onOpenKb && <button className="kb-back-btn" onClick={onOpenKb}><svg className="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg> 知识管理</button>}
          <span className="cmp-title">解析对比</span>
          <span className="cmp-sub">左:结构树 · 右:整篇切块预览 · 点树节点过滤该分支切块</span>
        </div>
        <button className="kb-btn kb-btn-primary" onClick={run} disabled={busy || !file}>{busy ? "对比中…" : "开始对比"}</button>
      </div>

      <div className="cmp-body">
        <div className="cmp-pick">
          <div className="kb-form-field" style={{ flex: 1, marginBottom: 0 }}>
            <label className="kb-form-label">选择文件 *</label>
            <input className="kb-form-input" type="file" accept=".pdf,.docx,.xlsx"
                   onChange={(e) => { setFile(e.target.files?.[0] || null); setRows(null); setErr(""); setFilterPath("") }} />
          </div>
          <div className="cmp-checks">
            {BACKENDS.map((b) => (
              <label key={b.key} className="cmp-check">
                <input type="checkbox" checked={!!checks[b.key]}
                       onChange={(e) => { setChecks({ ...checks, [b.key]: e.target.checked }); setRows(null) }} />
                {b.label} <span className="cmp-note">({b.note})</span>
              </label>
            ))}
          </div>
        </div>

        {err && <div className="kb-err" style={{ padding: 12, textAlign: "left" }}>{err}</div>}

        {rows && rows.length > 0 && (
          <div className="cmp-res">
            <div className="cmp-tabs">
              {rows.map((r) => {
                const pend = busyBackends.includes(r.backend)
                return (
                  <button key={r.backend}
                          className={"cmp-tab" + (active === r.backend ? " active" : "") + (pend ? " pending" : (r.ok ? "" : " fail"))}
                          onClick={() => { setActive(r.backend); setFilterPath(""); setExpanded({}) }}>
                    {labelOf(r.backend)}{pend ? " …" : (r.ok ? " ✓" : " ✗")}
                  </button>
                )
              })}
            </div>

            <div className="cmp-summary">
              <table className="kb-cmp-table">
                <thead><tr><th>后端</th><th>结果</th><th>耗时</th><th>字符</th><th>行</th><th>md#</th><th>部分</th><th>条</th><th>(一)</th><th>N.</th><th>切块</th><th>带section</th><th>深度</th><th>超长</th></tr></thead>
                <tbody>
                  {rows.map((r) => {
                    const pend = busyBackends.includes(r.backend)
                    return (
                    <tr key={r.backend} className={pend ? "kb-cmp-pending" : (r.ok ? "" : "kb-cmp-fail")}>
                      <td>{labelOf(r.backend)}</td>
                      <td>{r.ok ? "OK" : (pend ? "解析中…" : "✗ " + r.err)}</td>
                      <td>{pend ? "—" : (r.elapsed_ms >= 0 ? fmtDurMs(r.elapsed_ms) : "—")}</td>
                      <td>{fmt(r.chars)}</td><td>{fmt(r.lines)}</td><td>{fmt(r.md_heads)}</td>
                      <td>{fmt(r.part)}</td><td>{fmt(r.article)}</td><td>{fmt(r.subitem)}</td><td>{fmt(r.numbered)}</td>
                      <td>{fmt(r.chunks)}</td><td>{r.section_fill_pct}%</td><td>{r.avg_path_len}</td><td>{fmt(r.overlong)}</td>
                    </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>

            {okRow && okRow.ok && (
              <div className="cmp-split">
                {/* 左:结构树 */}
                <div className="cmp-tree-pane">
                  <div className="cmp-pane-head">
                    <span>结构树 · {labelOf(okRow.backend)}</span>
                    <span className="cmp-pane-sub">{tree.length} 节点</span>
                  </div>
                  <div className="cmp-tree">
                    {tree.length > 0 ? tree.map(({ node, path }, i) => (
                      <div key={i}
                           className={"cmp-node" + (filterPath === path ? " sel" : "")}
                           style={{ paddingLeft: (node.level - 1) * 16 }}
                           onClick={() => setFilterPath(filterPath === path ? "" : path)}
                           title={path}>
                        <span className="cmp-node-glyph">{node.level <= 1 ? "▣" : (node.level <= 3 ? "▢" : "·")}</span>
                        <span className="cmp-node-title">{node.title}</span>
                        {(() => {
                          const ids = nodeChunks[path] || []
                          return ids.length > 0
                            ? <span className="cmp-chunk-range-tag">§{ids[0]}{ids.length > 1 ? "–" + ids[ids.length - 1] : ""}({ids.length})</span>
                            : null
                        })()}
                      </div>
                    )) : <div className="cmp-hint">该后端未识别到结构标题</div>}
                  </div>
                </div>
                {/* 右:整篇切块预览 */}
                <div className="cmp-chunk-pane">
                  <div className="cmp-pane-head">
                    <span>整篇切块预览 · {labelOf(okRow.backend)}</span>
                    <span className="cmp-pane-sub">
                      {filterPath ? "已筛选: " + filterPath : ""}
                      {shownChunks.length}/{chunks.length} 块
                    </span>
                  </div>
                  <div className="cmp-chunks">
                    {shownChunks.length > 0 ? shownChunks.map((c) => {
                      const open = !!expanded[c.i]
                      return (
                      <div key={c.i} className={"cmp-chunk" + (open ? " open" : "")}>
                        <div className="cmp-chunk-meta">
                          <span className="cmp-chunk-idx">#{c.i}</span>
                          {c.section ? <span className="kb-tag">{c.section}</span> : <span className="kb-tag">(无结构)</span>}
                        </div>
                        <div className={"cmp-chunk-content" + (open ? " open" : " clamp")}
                             onClick={() => setExpanded((e) => ({ ...e, [c.i]: !open }))}
                             title="点击展开/收起">
                          {c.content}
                          {open && <span className="cmp-fold" title="点击收起"
                                         onClick={(ev) => { ev.stopPropagation(); setExpanded((e) => ({ ...e, [c.i]: false })) }}>&lt;</span>}
                        </div>
                      </div>
                      )
                    }) : <div className="cmp-hint">该标题没有对应切块(空壳或内容并入别块)</div>}
                  </div>
                </div>
              </div>
            )}

            {failRows.length > 0 && (
              <div className="cmp-fails">
                {failRows.map((r) => (
                  <div key={r.backend} className="cmp-fail-line">
                    <span className="kb-tag">{labelOf(r.backend)}</span>
                    <span>{r.err || "解析失败"}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
