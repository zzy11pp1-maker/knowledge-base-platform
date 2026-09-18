"""受保护的导入与检索，复用既有模型客户端及算法。"""

from datetime import datetime, timezone
from time import perf_counter

from fastapi import HTTPException
from sqlalchemy import select, update

from app.core.auth import Principal
from app.core.config import get_settings
from app.db.models import DocumentPermission, DocumentRecord
from app.db.session import session_factory
from app.rag.rrf import reciprocal_rank_fusion
from app.services.access_control import acl_payload, can_read_document, fresh_user, kb_access, scoped
from app.services.metrics import record_metric


class AuthorizedKnowledgeService:
    def __init__(self, document_service, repository, embedder, reranker):
        self.documents = document_service
        self.repository = repository
        self.embedder = embedder
        self.reranker = reranker

    def import_bytes(
        self,
        user: Principal,
        kb_id: str,
        filename: str,
        data: bytes,
        document_id=None,
        operation: str = "processing",
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
        category: str = "未分类",
    ):
        user = fresh_user(user, "knowledge:create")
        source = self.documents.reader.read_bytes(
            filename=filename, data=data, tenant_id=user.tenant_id, kb_id=kb_id, document_id=document_id
        )
        with session_factory()() as db:
            kb_access(db, user, kb_id, "editor")
            doc = db.get(DocumentRecord, source.document_id)
            if doc is not None:
                if doc.tenant_id != user.tenant_id or doc.kb_id != kb_id:
                    raise HTTPException(404, "文档不可访问")
                if doc.status in ("indexing", "processing", "reindex", "acl_syncing"):
                    raise HTTPException(409, "文档正在处理")
                # 内容哈希未变化时直接返回现有 ready 版本，避免重复向量化和重复写入。
                effective_size = chunk_size or doc.chunk_size or self.documents.max_chunk_size
                effective_overlap = doc.chunk_overlap if chunk_overlap is None else chunk_overlap
                if doc.status == "ready" and doc.version == self.documents.index_version(
                    source, effective_size, effective_overlap
                ):
                    return doc
                acquired = db.execute(
                    update(DocumentRecord)
                    .where(
                        DocumentRecord.id == doc.id,
                        DocumentRecord.status == doc.status,
                        DocumentRecord.acl_version == doc.acl_version,
                    )
                    .values(status=operation, error_message=None, updated_at=datetime.now(timezone.utc))
                )
                if acquired.rowcount != 1:
                    raise HTTPException(409, "文档状态已变化，请重试")
            else:
                doc = DocumentRecord(
                    id=source.document_id,
                    tenant_id=user.tenant_id,
                    kb_id=kb_id,
                    creator_id=user.id,
                    source=source.source,
                    title=source.title,
                    status="processing",
                    file_type=source.metadata.get("suffix", ".txt").lstrip("."),
                    category=category,
                    chunk_size=chunk_size or self.documents.max_chunk_size,
                    chunk_overlap=self.documents.chunk_overlap if chunk_overlap is None else chunk_overlap,
                )
                db.add(doc)
                db.flush()
                # 新文档默认仅上传者可读；通过权限 API 显式扩大授权。
                db.add(DocumentPermission(document_id=doc.id, permission_type="user", target_id=user.id))
            db.commit()
        try:
            effective_size = chunk_size or doc.chunk_size or self.documents.max_chunk_size
            effective_overlap = doc.chunk_overlap if chunk_overlap is None else chunk_overlap
            result = self.documents._ingest(source, effective_size, effective_overlap)
            with session_factory()() as db:
                doc = db.get(DocumentRecord, source.document_id)
                doc.title = result.document.title
                doc.source = result.document.source
                doc.version = result.document.document_version
                doc.chunk_count = result.upserted_chunks
                doc.file_type = source.metadata.get("suffix", ".txt").lstrip(".")
                doc.category = category or doc.category
                doc.chunk_size = effective_size
                doc.chunk_overlap = effective_overlap
                doc.error_message = None
                doc.updated_at = datetime.now(timezone.utc)
                self.repository.set_document_acl(user.tenant_id, kb_id, doc.id, acl_payload(db, doc))
                self.repository.set_document_metadata(
                    user.tenant_id, kb_id, doc.id,
                    {"title": doc.title, "category": doc.category, "is_enabled": doc.is_enabled},
                )
                doc.status = "ready"
                db.commit()
                return doc
        except Exception as exc:
            with session_factory()() as db:
                doc = db.get(DocumentRecord, source.document_id)
                doc.status = "failed"
                doc.error_message = f"{type(exc).__name__}: 文档处理失败"
                doc.updated_at = datetime.now(timezone.utc)
                db.commit()
            raise

    def filter_current(self, user, kb_id, candidates):
        user = fresh_user(user)
        with session_factory()() as db:
            kb_access(db, user, kb_id)
            result = []
            for item in candidates:
                chunk = item.chunk
                doc = db.get(DocumentRecord, chunk.document_id)
                if (
                    doc is not None
                    and doc.kb_id == kb_id
                    and chunk.kb_id == kb_id
                    and chunk.tenant_id == user.tenant_id
                    and doc.version == chunk.document_version
                    and can_read_document(db, user, doc)
                ):
                    result.append(item)
            return result

    def search_detailed(self, user, kb_id, query, candidate_limit=30, top_k=5, metadata_filters=None):
        user = fresh_user(user)
        with session_factory()() as db:
            kb_access(db, user, kb_id)
            docs = list(
                db.scalars(
                    select(DocumentRecord).where(
                        DocumentRecord.tenant_id == user.tenant_id,
                        DocumentRecord.kb_id == kb_id,
                        DocumentRecord.status == "ready",
                    )
                )
            )
            allowed = {
                doc.id: (doc.version, doc.acl_version) for doc in docs if can_read_document(db, user, doc)
            }
            denied = [doc.id for doc in docs if doc.id not in allowed]
        search_started = perf_counter()
        embedding = self.embedder.embed([query])
        dense, sparse = self.repository.query_authorized(
            embedding, user, kb_id, allowed, candidate_limit, metadata_filters or {}
        )
        # 在融合及精排之前进行第二层数据库核验。
        dense = self.filter_current(user, kb_id, dense)
        sparse = self.filter_current(user, kb_id, sparse)
        candidates = reciprocal_rank_fusion(dense, sparse, k=get_settings().rrf_k)[:candidate_limit]
        candidates = self.filter_current(user, kb_id, candidates)
        retrieval_ms = (perf_counter() - search_started) * 1000
        rerank_ms = 0.0
        if candidates:
            rerank_started = perf_counter()
            scores = self.reranker.rerank(query, [item.chunk.content for item in candidates])
            rerank_ms = (perf_counter() - rerank_started) * 1000
            if len(scores) != len(candidates):
                raise HTTPException(502, "精排响应数量错误")
            for item, score in zip(candidates, scores, strict=True):
                item.rerank_score = score
            candidates.sort(key=lambda item: (-item.rerank_score, -item.rrf_score, item.chunk.chunk_id))
        restricted_hits = self.repository.restricted_hybrid_document_hits(
            embedding, user.tenant_id, kb_id, denied, get_settings().restricted_score_threshold
        )
        result = self.filter_current(user, kb_id, candidates[:top_k])
        record_metric("retrieval", tenant_id=user.tenant_id, kb_id=kb_id, user_id=user.id,
                      duration_ms=retrieval_ms, metadata={"result_count": len(result)})
        if candidates:
            record_metric("rerank", tenant_id=user.tenant_id, kb_id=kb_id, user_id=user.id,
                          duration_ms=rerank_ms, metadata={"result_count": len(result)})
        allowed_recalled = list(dict.fromkeys(
            item.chunk.document_id for item in [*dense, *sparse] if item.chunk.document_id in allowed
        ))
        denied_recalled = list(dict.fromkeys(document_id for document_id, _ in restricted_hits))
        diagnostics = {
            "recalled_document_ids": list(dict.fromkeys([*allowed_recalled, *denied_recalled])),
            "allowed_document_ids": allowed_recalled,
            "denied_document_ids": denied_recalled,
            "highest_similarity_score": max(
                [item.score for item in dense] + [score for _, score in restricted_hits], default=None
            ),
        }
        return result, bool(restricted_hits), diagnostics

    def search(self, user, kb_id, query, candidate_limit=30, top_k=5, metadata_filters=None):
        """兼容既有调用方；详细审计信息由问答链路使用。"""

        hits, restricted, _ = self.search_detailed(
            user, kb_id, query, candidate_limit, top_k, metadata_filters
        )
        return hits, restricted

    def delete(self, user, document_id):
        user = fresh_user(user, "knowledge:delete")
        with session_factory()() as db:
            doc = scoped(db, DocumentRecord, document_id, user.tenant_id)
            kb_access(db, user, doc.kb_id, "editor")
            if doc.status in ("indexing", "processing", "reindex", "acl_syncing"):
                raise HTTPException(409, "文档正在处理，暂不可删除")
            doc.status = "deleted"
            db.commit()
            try:
                count = self.repository.delete_document(
                    tenant_id=user.tenant_id, kb_id=doc.kb_id, document_id=doc.id
                )
            except Exception as exc:
                doc.error_message = f"{type(exc).__name__}: 向量清理失败，可重试删除"
                db.commit()
                raise
            doc.chunk_count = 0
            doc.error_message = None
            doc.updated_at = datetime.now(timezone.utc)
            db.commit()
            return count
