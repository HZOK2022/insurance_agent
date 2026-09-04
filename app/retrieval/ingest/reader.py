# -*- coding: utf-8 -*-
"""文件读取工具，支持 .txt/.md/.pdf/.docx/.xlsx。

三个解析后端"都要、可并存、可对比"(互不覆盖):
- mineru     : MinerU 在线精准解析(v4;需 MINERU_API_KEY)。输出结构化 markdown(标题为 # 平铺,
                真实层级需靠编号 regex 重建——见 docs/mineru-api-docs.md 备注)。zip 内另有
                *_content_list.json(块级结构,含 type/bbox),当前仅取 markdown,content_list
                由 mineru_client 一并返回、留作后续层级重建接口。
- markitdown : MarkItDown 统一转 pdf/docx/xlsx → markdown(标题按文档样式识别,纯文本层条款
                输出无 # 标题,与 pdfplumber 同属"无层级文本")。
- pdfplumber : pdf 直抽纯文本层(native;仅 pdf)。
- native      : docx(python-docx)/xlsx(openpyxl) 直读(仅 docx/xlsx)。

read_text(path, backend=...) 按扩展名路由:
- backend="auto"(默认)= 全回退链:pdf → mineru→markitdown→pdfplumber;docx/xlsx → mineru→markitdown→native
- backend 显式 = 只用该后端,失败返回 None(供对比工具逐路独立评测;auto 语义不变,不破坏既有管线)。
供 CLI(ingest_kb.py --parser)、API 摄取、scripts/compare_parsers.py 共用。
"""
from __future__ import annotations
import logging
import os

from app.retrieval.categories import classify_product_category

logger = logging.getLogger(__name__)

# 扩展名 -> 入库 doc_type
_DOC_TYPE = {
    ".txt": "text",
    ".md": "markdown",
    ".pdf": "policy_pdf",
    ".docx": "policy_docx",
    ".xlsx": "rate_table",
}
_SUPPORTED_EXTS = (".txt", ".md", ".pdf", ".docx", ".xlsx")

# 后端名(顺序无关,auto 回退链见 _CHAINS)
BACKEND_NAMES = ("auto", "mineru", "markitdown", "pdfplumber", "native")

# 各扩展名的 auto 回退链
_CHAINS = {
    ".pdf": ("mineru", "markitdown", "pdfplumber"),
    ".docx": ("mineru", "markitdown", "native"),
    ".xlsx": ("mineru", "markitdown", "native"),
}


def is_supported(path: str) -> bool:
    """判断文件是否支持摄取"""
    ext = os.path.splitext(path)[1].lower()
    return ext in _SUPPORTED_EXTS


# ---------------- 三个独立后端(每个失败返回 None,不抛) ----------------

def _markitdown_text(path: str) -> str | None:
    """MarkItDown:pdf/docx/xlsx → 结构化 markdown(标题/表格尽量保留)。
    失败(未装/异常)返回 None,由调用方降级。"""
    try:
        from markitdown import MarkItDown
        return MarkItDown().convert(path).markdown
    except Exception as e:  # noqa: BLE001
        logger.debug("markitdown failed %s: %s", path, e)
        return None


def _mineru_text(path: str) -> str | None:
    """MinerU 在线精准解析(需 MINERU_API_KEY,默认空)。未配置/失败返回 None。
    注意:full.md 的标题被 MinerU 拍平(顶层 # 给主标题,其余全 ##),层级信息在其
    content_list/middle.json;此处只取 markdown 文本(与 MarkItDown/pdfplumber 对齐口径)。"""
    from app.config import load
    if not (getattr(load(), "mineru_api_key", "") or ""):
        return None
    try:
        from app.retrieval.ingest.mineru_client import parse_file
        return parse_file(path).get("markdown")
    except Exception as e:  # noqa: BLE001
        logger.warning("mineru failed %s: %s", path, e)
        return None


def parse_pdf_pdfplumber(path: str) -> str | None:
    """pdfplumber 直抽纯文本层(逐页 extract_text)。"""
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            return "\n".join((p.extract_text() or "") for p in pdf.pages)
    except Exception as e:  # noqa: BLE001
        logger.debug("pdfplumber failed %s: %s", path, e)
        return None


def _native_docx(path: str) -> str:
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


def _native_xlsx(path: str) -> str:
    """openpyxl:逐工作表,表头行 + 数据行(逐行用 | 拼接)。"""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    parts = []
    try:
        for ws in wb.worksheets:
            parts.append(f"[{ws.title}]")
            for row in ws.iter_rows(values_only=True):
                cells = ["" if v is None else str(v) for v in row]
                if any(c.strip() for c in cells):
                    parts.append(" | ".join(cells))
    finally:
        wb.close()
    return "\n".join(parts)


def _native_text(path: str) -> str | None:
    """docx/xlsx 原库直读(native 后端)。"""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".docx":
            return _native_docx(path)
        if ext == ".xlsx":
            return _native_xlsx(path)
    except Exception as e:  # noqa: BLE001
        logger.debug("native failed %s: %s", path, e)
    return None


# 后端名 -> (该后端适用的扩展名, 执行函数)
_BACKEND_RUN = {
    "mineru": ((".pdf", ".docx", ".xlsx"), _mineru_text),
    "markitdown": ((".pdf", ".docx", ".xlsx"), _markitdown_text),
    "pdfplumber": ((".pdf",), parse_pdf_pdfplumber),
    "native": ((".docx", ".xlsx"), _native_text),
}


def try_backend(path: str, backend: str) -> tuple[bool, str | None, str]:
    """跑单个后端,返回 (ok, text, err)。err 为空串表示成功。后端不适用该扩展名 → (False,None,原因)。"""
    ext = os.path.splitext(path)[1].lower()
    if backend not in _BACKEND_RUN:
        return False, None, f"未知后端: {backend}"
    exts, fn = _BACKEND_RUN[backend]
    if ext not in exts:
        return False, None, f"后端 {backend} 不适用 .{ext.lstrip('.')}(适用: {', '.join(e.lstrip('.') for e in exts)})"
    if backend == "mineru":
        from app.config import load
        if not (getattr(load(), "mineru_api_key", "") or ""):
            return False, None, "MINERU_API_KEY 未配置"
    try:
        text = fn(path)
        if text:
            return True, text, ""
        return False, None, "解析无输出(空文本)"
    except Exception as e:  # noqa: BLE001
        return False, None, f"{type(e).__name__}: {e}"


def _resolve_backend(backend: str | None) -> str:
    """backend=None → 取 config 的 parser_backend(默认 auto);非法值回退 auto。"""
    if backend is None:
        try:
            from app.config import load
            backend = getattr(load(), "parser_backend", "auto") or "auto"
        except Exception:  # noqa: BLE001
            backend = "auto"
    if backend not in BACKEND_NAMES:
        logger.warning("未知解析后端 %r,回退 auto", backend)
        backend = "auto"
    return backend


def read_text(path: str, backend: str | None = None) -> str | None:
    """读取文件提取文本，返回 None 表示不支持该格式。
    backend: None=取 config PARSER_BACKEND | auto(回退链)| mineru | markitdown | pdfplumber | native(仅 docx/xlsx)。"""
    backend = _resolve_backend(backend)
    ext = os.path.splitext(path)[1].lower()
    if ext in (".txt", ".md"):
        try:
            with open(path, encoding="utf-8") as f:
                return f.read()
        except OSError:
            return None
    candidates = _CHAINS[ext] if backend == "auto" else (backend,)
    for b in candidates:
        ok, text, _ = try_backend(path, b)
        if ok:
            return text
    return None


def backend_supported_for(path: str, backend: str) -> bool:
    """后端是否适用该文件(不保证成功)。"""
    ext = os.path.splitext(path)[1].lower()
    exts, _ = _BACKEND_RUN.get(backend, ((), None))
    return ext in exts


def build_docs(path: str, category: str = "", backend: str | None = None) -> list[dict]:
    """从文件路径构建文档元数据(backend=None → config PARSER_BACKEND)。
    返回: [{text: str, meta: dict}]
    """
    text = read_text(path, backend)
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
