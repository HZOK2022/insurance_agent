// chunk.section 与目录节点 path 的"分支归属"匹配(段级前缀,不含反向挂祖先)。
// section 与 path 都是"层级标题链,用 > 分隔,从文档根开始"。
// 一个 chunk 属于某节点分支 ⟺ 该节点 path 的段序列是 chunk.section 段序列的前缀(含相等)。
// 这样"比节点更浅的祖先块"(如文档根标题那块)不会挂进每一个深层节点。
export function normPath(s: string | null | undefined): string[] {
  return (s || "").replace(/[\u3000\s]/g, "").split(">").map((x) => x.trim()).filter(Boolean)
}
export function inBranch(section: string | null | undefined, path: string | null | undefined): boolean {
  const a = normPath(section)
  const b = normPath(path)
  if (!a.length || !b.length) return false
  if (b.length > a.length) return false
  for (let i = 0; i < b.length; i++) if (a[i] !== b[i]) return false
  return true
}
