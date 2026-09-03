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
    doc_id: str = Field(..., min_length=1, description="文档ID(唯一标识)")
    version: str = Field(default="v1", description="文档版本")
    doc_type: Optional[str] = Field(None, description="文档类型")
    product_category: Optional[str] = Field(None, description="保险类别")
    title: Optional[str] = Field(None, description="文档标题")
    source: Optional[str] = Field(None, description="来源路径或描述")


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
