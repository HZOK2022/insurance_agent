# -*- coding: utf-8 -*-
"""文件读取工具，支持 .txt/.md/.pdf/.docx/.xlsx。
供 CLI(ingest_kb.py) 和 API 摄取共用。
"""
from __future__ import annotations
import os
from app.retrieval.categories import classify_product_category

# 扩展名 -> 入库 doc_type
_DOC_TYPE = {
    ".txt": "text",
    ".md": "markdown",
    ".pdf": "policy_pdf",
    ".docx": "policy_docx",
    ".xlsx": "rate_table",
}
_SUPPORTED_EXTS = (".txt", ".md", ".pdf", ".docx", ".xlsx")


def is_supported(path: str) -> bool:
    """判断文件是否支持摄取"""
    ext = os.path.splitext(path)[1].lower()
    return ext in _SUPPORTED_EXTS


def read_text(path: str) -> str | None:
    """读取文件提取文本，返回 None 表示不支持该格式"""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".txt", ".md"):
        with open(path, encoding="utf-8") as f:
            return f.read()
    if ext == ".pdf":
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            return "\n".join((p.extract_text() or "") for p in pdf.pages)
    if ext == ".docx":
        return _read_docx(path)
    if ext == ".xlsx":
        return _read_xlsx(path)
    return None


def _read_docx(path: str) -> str:
    """python-docx:正文段落 + 表格(逐行用 | 拼接)。"""
    from docx import Document
    doc = Document(path)
    parts = []
    for p in doc.paragraphs:
        if p.text.strip():
            parts.append(p.text)
    for ti, table in enumerate(doc.tables, 1):
        parts.append(f"[表格{ti}]")
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            parts.append(" | ".join(cells))
    return "\n".join(parts)


def _read_xlsx(path: str) -> str:
    """openpyxl:逐工作表,表头行 + 数据行(逐行用 | 拼接)。"""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    parts = []
    for ws in wb.worksheets:
        parts.append(f"[{ws.title}]")
        for row in ws.iter_rows(values_only=True):
            cells = ["" if v is None else str(v) for v in row]
            if any(c.strip() for c in cells):
                parts.append(" | ".join(cells))
    wb.close()
    return "\n".join(parts)


def build_docs(path: str, category: str = "") -> list[dict]:
    """从文件路径构建文档元数据。
    返回: [{text: str, meta: dict}]
    """
    text = read_text(path)
    if not text:
        return []
    base = os.path.splitext(os.path.basename(path))[0]
    ext = os.path.splitext(path)[1].lower()
    # 保险类别:优先显式 category,否则按 doc_id 关键词判定
    cat = category or classify_product_category(base, base)
    doc_type = _DOC_TYPE.get(ext, "policy_document")
    return [{
        "text": text,
        "meta": {
            "chunk_id": base,
            "doc_id": base,
            "version": "v1",
            "section": "",
            "doc_type": doc_type,
            "source": path,
            "title": base,
            "product_category": cat,
        }
    }]


def supported_extensions() -> tuple[str, ...]:
    """返回支持的扩展名列表"""
    return _SUPPORTED_EXTS
