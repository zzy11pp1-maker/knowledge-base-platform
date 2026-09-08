"""Qdrant dense+sparse 存取实现。"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from threading import Lock
from typing import Any

from qdrant_client import QdrantClient, models

from app.core.exceptions import VectorStoreError
from app.models.domain import Chunk, DocumentSummary, EmbeddingBatch, SparseEmbedding, VectorHit


LOGGER = logging.getLogger(__name__)
FILTER_KEY_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")


class QdrantRepository:
    """所有读写强制携带 tenant/kb 过滤，防止跨空间串数据。"""

    dense_name = "dense"
    sparse_name = "sparse"

    def __init__(
        self,
        client: QdrantClient,
        collection_name: str,
        dense_vector_size: int,
        upsert_batch_size: int = 64,
    ) -> None:
        if upsert_batch_size <= 0:
            raise ValueError("upsert_batch_size 必须大于 0")
        self.client = client
        self.collection_name = collection_name
        self.dense_vector_size = dense_vector_size
        self.upsert_batch_size = upsert_batch_size
        self._ensure_lock = Lock()
        self._ensured = False

    def ensure_collection(self) -> None:
        """只创建或校验 collection，绝不隐式重建/删除已有数据。"""

        if self._ensured:
            return
        with self._ensure_lock:
            if self._ensured:
                return
            try:
                if not self.client.collection_exists(self.collection_name):
                    try:
                        self.client.create_collection(
                            collection_name=self.collection_name,
                            vectors_config={
                                self.dense_name: models.VectorParams(
                                    size=self.dense_vector_size,
                                    distance=models.Distance.COSINE,
                                )
                            },
                            sparse_vectors_config={
                                self.sparse_name: models.SparseVectorParams(
                                    index=models.SparseIndexParams(on_disk=False)
                                )
                            },
                        )
                    except Exception:
                        # 多 worker 首次请求可能同时创建；若另一方已经成功则继续校验。
                        if not self.client.collection_exists(self.collection_name):
                            raise
                self._validate_collection()
                self._ensure_payload_indexes()
                self._ensured = True
            except VectorStoreError:
                raise
            except Exception as exc:
                LOGGER.exception("Qdrant collection 初始化失败 collection=%s", self.collection_name)
                raise VectorStoreError(f"Qdrant collection 初始化失败：{type(exc).__name__}") from exc

    def _validate_collection(self) -> None:
        info = self.client.get_collection(self.collection_name)
        params = info.config.params
        vectors = params.vectors
        dense = vectors.get(self.dense_name) if isinstance(vectors, dict) else None
        if dense is None or dense.size != self.dense_vector_size:
            raise VectorStoreError(
                f"已有 collection 的 dense 配置不兼容，需要命名向量 {self.dense_name}:{self.dense_vector_size}"
            )
        sparse_vectors = getattr(params, "sparse_vectors", None) or {}
        if self.sparse_name not in sparse_vectors:
            raise VectorStoreError(f"已有 collection 缺少 named sparse vector：{self.sparse_name}")

    def _ensure_payload_indexes(self) -> None:
        # tenant_id 使用 tenant 索引提示 Qdrant 按租户优化存储；其余字段用于精确过滤。
        keyword_schema: Any = models.PayloadSchemaType.KEYWORD
        try:
            keyword_schema = models.KeywordIndexParams(
                type=models.KeywordIndexType.KEYWORD,
                is_tenant=True,
            )
        except (AttributeError, TypeError):
            keyword_schema = models.PayloadSchemaType.KEYWORD
        for field_name, schema in (
            ("tenant_id", keyword_schema),
            ("kb_id", models.PayloadSchemaType.KEYWORD),
            ("document_id", models.PayloadSchemaType.KEYWORD),
            ("chunk_id", models.PayloadSchemaType.KEYWORD),
        ):
            self.client.create_payload_index(
                collection_name=self.collection_name,
                field_name=field_name,
                field_schema=schema,
                wait=True,
            )

    def upsert_chunks(self, chunks: list[Chunk], embeddings: EmbeddingBatch) -> int:
        self.ensure_collection()
        if len(chunks) != len(embeddings.dense) or len(chunks) != len(embeddings.sparse):
            raise VectorStoreError("Chunk 数量与向量数量不一致")
        points: list[models.PointStruct] = []
        for chunk, dense, sparse in zip(chunks, embeddings.dense, embeddings.sparse):
            if len(dense) != self.dense_vector_size:
                raise VectorStoreError(f"dense 向量维度必须为 {self.dense_vector_size}")
            points.append(
                models.PointStruct(
                    id=chunk.chunk_id,
                    vector={
                        self.dense_name: dense,
                        self.sparse_name: models.SparseVector(indices=sparse.indices, values=sparse.values),
                    },
                    payload=self._chunk_to_payload(chunk),
                )
            )
        try:
            for start in range(0, len(points), self.upsert_batch_size):
                self.client.upsert(
                    self.collection_name,
                    points=points[start : start + self.upsert_batch_size],
                    wait=True,
                )
            LOGGER.info("Qdrant upsert 完成 collection=%s chunks=%s", self.collection_name, len(points))
            return len(points)
        except Exception as exc:
            LOGGER.exception("Qdrant upsert 失败 chunk_count=%s", len(points))
            raise VectorStoreError(f"Qdrant upsert 失败：{type(exc).__name__}") from exc

    def search_dense(
        self,
        vector: list[float],
        *,
        tenant_id: str,
        kb_id: str,
        limit: int,
        metadata_filters: dict[str, str | int | bool] | None = None,
    ) -> list[VectorHit]:
        return self._search(
            query=vector,
            using=self.dense_name,
            tenant_id=tenant_id,
            kb_id=kb_id,
            limit=limit,
            metadata_filters=metadata_filters,
        )

    def search_sparse(
        self,
        vector: SparseEmbedding,
        *,
        tenant_id: str,
        kb_id: str,
        limit: int,
        metadata_filters: dict[str, str | int | bool] | None = None,
    ) -> list[VectorHit]:
        # Qdrant 不接受完全为空的 SparseVector；没有稀疏权重时直接返回空结果。
        if not vector.indices:
            return []
        return self._search(
            query=models.SparseVector(indices=vector.indices, values=vector.values),
            using=self.sparse_name,
            tenant_id=tenant_id,
            kb_id=kb_id,
            limit=limit,
            metadata_filters=metadata_filters,
        )

    def _search(
        self,
        *,
        query: list[float] | models.SparseVector,
        using: str,
        tenant_id: str,
        kb_id: str,
        limit: int,
        metadata_filters: dict[str, str | int | bool] | None,
    ) -> list[VectorHit]:
        self.ensure_collection()
        try:
            response = self.client.query_points(
                collection_name=self.collection_name,
                query=query,
                using=using,
                query_filter=self._build_filter(tenant_id, kb_id, metadata_filters),
                limit=limit,
                with_payload=True,
                with_vectors=False,
            )
            return [VectorHit(chunk=self._payload_to_chunk(point.payload or {}), score=point.score) for point in response.points]
        except VectorStoreError:
            raise
        except Exception as exc:
            LOGGER.exception("Qdrant %s 检索失败", using)
            raise VectorStoreError(f"Qdrant {using} 检索失败：{type(exc).__name__}") from exc

    def delete_document(self, *, tenant_id: str, kb_id: str, document_id: str) -> int:
        self.ensure_collection()
        query_filter = self._document_filter(tenant_id, kb_id, document_id)
        try:
            count = self.client.count(self.collection_name, count_filter=query_filter, exact=True).count
            if count:
                self.client.delete(
                    collection_name=self.collection_name,
                    points_selector=models.FilterSelector(filter=query_filter),
                    wait=True,
                )
            return count
        except Exception as exc:
            LOGGER.exception("删除 Qdrant 文档失败 document_id=%s", document_id)
            raise VectorStoreError(f"删除 Qdrant 文档失败：{type(exc).__name__}") from exc

    def delete_other_versions(
        self,
        *,
        tenant_id: str,
        kb_id: str,
        document_id: str,
        keep_version: str,
    ) -> int:
        """新版本写入成功后清理旧版本，避免导入失败导致原文档先消失。"""

        self.ensure_collection()
        query_filter = models.Filter(
            must=self._document_filter(tenant_id, kb_id, document_id).must,
            must_not=[
                models.FieldCondition(
                    key="document_version",
                    match=models.MatchValue(value=keep_version),
                )
            ],
        )
        try:
            count = self.client.count(self.collection_name, count_filter=query_filter, exact=True).count
            if count:
                self.client.delete(
                    collection_name=self.collection_name,
                    points_selector=models.FilterSelector(filter=query_filter),
                    wait=True,
                )
            return count
        except Exception as exc:
            LOGGER.exception("清理文档旧版本失败 document_id=%s", document_id)
            raise VectorStoreError(f"清理文档旧版本失败：{type(exc).__name__}") from exc

    def count_document(self, *, tenant_id: str, kb_id: str, document_id: str) -> int:
        self.ensure_collection()
        try:
            return self.client.count(
                self.collection_name,
                count_filter=self._document_filter(tenant_id, kb_id, document_id),
                exact=True,
            ).count
        except Exception as exc:
            LOGGER.exception("Qdrant 文档计数失败 document_id=%s", document_id)
            raise VectorStoreError(f"Qdrant 文档计数失败：{type(exc).__name__}") from exc

    def get_chunk(self, tenant_id: str, kb_id: str, document_id: str, chunk_id: str):
        """按四个作用域字段精确取回引用正文；调用方仍须执行关系库 ACL 复核。"""

        self.ensure_collection()
        query_filter = self._document_filter(tenant_id, kb_id, document_id)
        query_filter.must.append(
            models.FieldCondition(key="chunk_id", match=models.MatchValue(value=chunk_id))
        )
        try:
            points, _ = self.client.scroll(
                self.collection_name, scroll_filter=query_filter, limit=1,
                with_payload=True, with_vectors=False,
            )
            return self._payload_to_chunk(points[0].payload or {}) if points else None
        except Exception as exc:
            raise VectorStoreError("读取引用 Chunk 失败") from exc

    def list_documents(self, *, tenant_id: str, kb_id: str, offset: int, limit: int) -> list[DocumentSummary]:
        self.ensure_collection()
        query_filter = self._build_filter(tenant_id, kb_id, None)
        groups: dict[str, list[Chunk]] = defaultdict(list)
        page_offset: Any = None
        try:
            while True:
                points, page_offset = self.client.scroll(
                    self.collection_name,
                    scroll_filter=query_filter,
                    limit=256,
                    offset=page_offset,
                    with_payload=True,
                    with_vectors=False,
                )
                for point in points:
                    chunk = self._payload_to_chunk(point.payload or {})
                    groups[chunk.document_id].append(chunk)
                if page_offset is None:
                    break
        except Exception as exc:
            LOGGER.exception("Qdrant 文档列表查询失败")
            raise VectorStoreError(f"Qdrant 文档列表查询失败：{type(exc).__name__}") from exc
        summaries = []
        for document_id, chunks in groups.items():
            first = min(chunks, key=lambda chunk: chunk.chunk_index)
            summaries.append(
                DocumentSummary(
                    document_id=document_id,
                    tenant_id=first.tenant_id,
                    kb_id=first.kb_id,
                    title=str(first.metadata.get("document_title", first.title)),
                    source=first.source,
                    document_version=first.document_version,
                    chunk_count=len(chunks),
                    created_at=first.created_at,
                )
            )
        summaries.sort(key=lambda item: (item.created_at, item.document_id), reverse=True)
        return summaries[offset : offset + limit]

    @staticmethod
    def _chunk_to_payload(chunk: Chunk) -> dict[str, Any]:
        return {
            "chunk_id": chunk.chunk_id,
            "tenant_id": chunk.tenant_id,
            "kb_id": chunk.kb_id,
            "document_id": chunk.document_id,
            "chunk_index": chunk.chunk_index,
            "content": chunk.content,
            "title": chunk.title,
            "source": chunk.source,
            "document_version": chunk.document_version,
            "content_hash": chunk.content_hash,
            "created_at": chunk.created_at.isoformat(),
            "metadata": chunk.metadata,
        }

    @staticmethod
    def _payload_to_chunk(payload: dict[str, Any]) -> Chunk:
        try:
            return Chunk.model_validate(payload)
        except Exception as exc:
            raise VectorStoreError("Qdrant payload 与当前 Chunk schema 不兼容") from exc

    @staticmethod
    def _document_filter(tenant_id: str, kb_id: str, document_id: str) -> models.Filter:
        return models.Filter(
            must=[
                models.FieldCondition(key="tenant_id", match=models.MatchValue(value=tenant_id)),
                models.FieldCondition(key="kb_id", match=models.MatchValue(value=kb_id)),
                models.FieldCondition(key="document_id", match=models.MatchValue(value=document_id)),
            ]
        )

    @staticmethod
    def _build_filter(
        tenant_id: str,
        kb_id: str,
        metadata_filters: dict[str, str | int | bool] | None,
    ) -> models.Filter:
        must = [
            models.FieldCondition(key="tenant_id", match=models.MatchValue(value=tenant_id)),
            models.FieldCondition(key="kb_id", match=models.MatchValue(value=kb_id)),
        ]
        for key, value in (metadata_filters or {}).items():
            if not FILTER_KEY_RE.fullmatch(key):
                raise VectorStoreError(f"metadata 过滤字段名不合法：{key}")
            must.append(models.FieldCondition(key=f"metadata.{key}", match=models.MatchValue(value=value)))
        return models.Filter(must=must)
