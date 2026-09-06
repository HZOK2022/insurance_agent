# -*- coding: utf-8 -*-
"""Knowledge Base management API schemas (admin-only)."""
from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Optional


class PaginationQuery(BaseModel):
    """分页查询参数"""
    page: int = Field(default=1, ge=1, description="页码 (1-indexed)")
    page_size: int = Field(default=50, ge=10, le=200, description="每页条数")


class DocumentItem(BaseModel):
    """单个文档列表项"""
    doc_id: str
    doc_type: Optional[str]
    product_category: Optional[str]
    chunk_count: int
    last_updated: Optional[str]


class DocumentListResponse(BaseModel):
    """文档列表响应"""
    total: int
    page: int
    page_size: int
    items: list[DocumentItem]


class ChunkItem(BaseModel):
    """单个chunk列表项"""
    chunk_id: str
    doc_id: str
    version: str
    section: Optional[str]
    title: Optional[str]
    product_category: Optional[str]
    content_preview: str


class StructNode(BaseModel):
    """文档结构树节点"""
    level: int
    title: str
    page: Optional[int] = None
    parent: str = ""
    chunk_ids: list[int] = []


class DocStructureResponse(BaseModel):
    """文档目录(结构树)响应"""
    doc_id: str
    nodes: list[StructNode]


class ChunkListResponse(BaseModel):
    """文档chunks列表响应"""
    doc_id: str
    total: int
    page: int
    page_size: int
    items: list[ChunkItem]


class IngestTextRequest(BaseModel):
    """摄取文本文档请求"""
    text: str = Field(..., min_length=1, description="文档内容")
    product_name: str = Field(..., min_length=1, description="产品名称(唯一;doc_id=它)")
    doc_id: Optional[str] = Field(None, description="文档ID(旧字段;缺省=product_name)")
    version: str = Field(default="v1", description="文档版本")
    doc_type: Optional[str] = Field(None, description="文档类型")
    product_category: Optional[str] = Field(None, description="保险类别")
    title: Optional[str] = Field(None, description="文档标题")
    source: Optional[str] = Field(None, description="来源路径或描述")
    text_splitter: Optional[str] = Field(None, description="切块方式: structured|character|paragraph")
    chunk_size: Optional[int] = Field(None, description="切块字符数上限(覆盖 config)")
    overlap: Optional[int] = Field(None, description="切块重叠字符数(覆盖 config)")
    chunk_max_tokens: Optional[int] = Field(None, description="结构层级 token 预算(覆盖 config)")
    force: bool = Field(False, description="同名产品内容不同时强制覆盖(需确认)")


class DeleteDocumentResponse(BaseModel):
    """删除文档响应"""
    ok: bool
    doc_id: str
    chunks_deleted: int
    points_deleted: int
    message: str


class ReindexResponse(BaseModel):
    """全量重建索引响应"""
    ok: bool
    total_chunks: int
    embedded: int
    message: str


class IngestTextResponse(BaseModel):
    """摄取文本响应"""
    ok: bool
    doc_id: str
    chunks_written: int
    chunks_embedded: int
    message: str
    conflict: bool = False

class OutlineNode(BaseModel):
    """大纲节点(层级树)"""
    level: int = 1
    title: str = ""


class ChunkPreviewNode(BaseModel):
    """切块预览(单块)"""
    i: int = 0
    section: str = ""
    title: str = ""
    content: str = ""


class ParsePreviewItem(BaseModel):
    """单路解析预览结果(不落库)"""
    backend: str
    ok: bool
    err: str = ""
    chars: int = 0
    lines: int = 0
    md_heads: int = 0
    part: int = 0
    article: int = 0
    subitem: int = 0
    numbered: int = 0
    chunks: int = 0
    section_fill_pct: float = 0.0
    avg_path_len: float = 0.0
    overlong: int = 0
    excerpt: str = ""
    outline: list[OutlineNode] = []
    text: str = ""
    chunks_view: list[ChunkPreviewNode] = []
    elapsed_ms: int = 0


class ParsePreviewResponse(BaseModel):
    """解析预览响应:同一文件多后端并列结果"""
    file_name: str
    items: list[ParsePreviewItem]


class FileIngestResponse(IngestTextResponse):
    """文件摄取响应(带所用解析后端)"""
    parser: str = "auto"


class UploadChunkNode(BaseModel):
    """上传预览的单块(section/title/content,供切块预览与 commit 复用)"""
    section: str = ""
    title: str = ""
    content: str = ""


class UploadPreviewResponse(BaseModel):
    """上传前"预览切块"结果(不写库、不发嵌入)"""
    ok: bool
    err: str = ""
    doc_type: str = ""
    parser: str = ""
    text_splitter: str = "structured"
    chunk_count: int = 0
    chunk_size: int = 0
    overlap: int = 0
    outline: list[StructNode] = []
    chunks: list[UploadChunkNode] = []


class IngestionCommitRequest(BaseModel):
    """确认索引:提交预览得到的 chunks/outline(不再解析),服务端写库+嵌入+Qdrant"""
    product_name: str = Field(..., min_length=1, description="产品名称(唯一;doc_id=它)")
    doc_id: Optional[str] = Field(None, description="文档ID(旧字段;缺省=product_name)")
    title: Optional[str] = None
    version: str = "v1"
    product_category: Optional[str] = None
    doc_type: str = "policy_pdf"
    source: Optional[str] = None
    parser: str = "auto"
    text_splitter: str = "structured"
    outline: list[StructNode] = []
    chunks: list[UploadChunkNode] = Field(..., min_length=1)
    force: bool = Field(False, description="同名产品内容不同时强制覆盖(需确认)")

