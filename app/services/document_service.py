"""文档导入、列表与删除业务。"""

from __future__ import annotations

import logging
from hashlib import sha256
from pathlib import Path
from typing import Any

from app.core.exceptions import DocumentNotFoundError, DocumentReadError
from app.core.protocols import EmbeddingProvider
from app.ingestion.readers import TextDocumentReader
from app.models.domain import Document, DocumentSummary, IngestionResult
from app.rag.document_split import build_chunks
from app.repositories.qdrant_repository import QdrantRepository


LOGGER = logging.getLogger(__name__)


class DocumentService:
    """显式编排导入流水线，便于单测和后续替换为异步任务。"""

    def __init__(
        self,
        *,
        reader: TextDocumentReader,
        embedder: EmbeddingProvider,
        repository: QdrantRepository,
        max_chunk_size: int,
        chunk_overlap: int,
        embedding_model_id: str,
    ) -> None:
        self.reader = reader
        self.embedder = embedder
        self.repository = repository
        self.max_chunk_size = max_chunk_size
        self.chunk_overlap = chunk_overlap
        self.embedding_model_id = embedding_model_id

    def import_path(
        self,
        file_path: str | Path,
        *,
        tenant_id: str,
        kb_id: str,
        document_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> IngestionResult:
        document = self.reader.read_path(
            file_path,
            tenant_id=tenant_id,
            kb_id=kb_id,
            document_id=document_id,
            metadata=metadata,
        )
        return self._ingest(document)

    def import_bytes(
        self,
        *,
        filename: str,
        data: bytes,
        tenant_id: str,
        kb_id: str,
        document_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> IngestionResult:
        document = self.reader.read_bytes(
            filename=filename,
            data=data,
            tenant_id=tenant_id,
            kb_id=kb_id,
            document_id=document_id,
            metadata=metadata,
        )
        return self._ingest(document)

    def _ingest(
        self,
        document: Document,
        max_chunk_size: int | None = None,
        chunk_overlap: int | None = None,
    ) -> IngestionResult:
        effective_size = max_chunk_size or self.max_chunk_size
        effective_overlap = self.chunk_overlap if chunk_overlap is None else chunk_overlap
        if effective_size < 1 or effective_size > 10_000 or effective_overlap < 0 or effective_overlap >= effective_size:
            raise DocumentReadError("切片参数无效：chunk_size 应为 1-10000，overlap 必须小于 chunk_size")
        # 索引版本同时绑定正文、切分配置和 embedding 身份，重新切分时不会残留旧 Chunk。
        document = document.model_copy(update={"version": self.index_version(document, effective_size, effective_overlap), "metadata": {
            **document.metadata, "source_content_hash": document.content_hash,
            "chunker_version": "markdown-v1", "embedding_model_id": self.embedding_model_id,
            "chunk_size": effective_size, "chunk_overlap": effective_overlap,
        }})
        chunks = build_chunks(document, effective_size, effective_overlap)
        if not chunks:
            raise DocumentReadError("文档无法生成有效 Chunk")
        LOGGER.info(
            "开始向量化 tenant_id=%s kb_id=%s document_id=%s chunks=%s",
            document.tenant_id,
            document.kb_id,
            document.document_id,
            len(chunks),
        )
        embeddings = self.embedder.embed([chunk.content for chunk in chunks])
        upserted = self.repository.upsert_chunks(chunks, embeddings)
        removed = self.repository.delete_other_versions(
            tenant_id=document.tenant_id,
            kb_id=document.kb_id,
            document_id=document.document_id,
            keep_version=document.version,
        )
        LOGGER.info("文档入库完成 document_id=%s removed_old_chunks=%s", document.document_id, removed)
        summary = DocumentSummary(
            document_id=document.document_id,
            tenant_id=document.tenant_id,
            kb_id=document.kb_id,
            title=document.title,
            source=document.source,
            document_version=document.version,
            chunk_count=len(chunks),
            created_at=document.created_at,
        )
        return IngestionResult(document=summary, upserted_chunks=upserted)

    def index_version(
        self,
        document: Document,
        max_chunk_size: int | None = None,
        chunk_overlap: int | None = None,
    ) -> str:
        """正文、切分参数或模型身份变化都会产生新索引版本。"""

        effective_size = max_chunk_size or self.max_chunk_size
        effective_overlap = self.chunk_overlap if chunk_overlap is None else chunk_overlap
        index_fingerprint = (
            f"{document.content_hash}:markdown-v1:{effective_size}:"
            f"{effective_overlap}:{self.embedding_model_id}"
        )
        return sha256(index_fingerprint.encode("utf-8")).hexdigest()[:16]

    def list_documents(self, *, tenant_id: str, kb_id: str, offset: int, limit: int) -> list[DocumentSummary]:
        return self.repository.list_documents(tenant_id=tenant_id, kb_id=kb_id, offset=offset, limit=limit)

    def delete_document(self, *, tenant_id: str, kb_id: str, document_id: str) -> int:
        deleted = self.repository.delete_document(
            tenant_id=tenant_id,
            kb_id=kb_id,
            document_id=document_id,
        )
        if deleted == 0:
            raise DocumentNotFoundError("指定租户和知识库中不存在该文档")
        LOGGER.info("文档删除完成 document_id=%s chunks=%s", document_id, deleted)
        return deleted
