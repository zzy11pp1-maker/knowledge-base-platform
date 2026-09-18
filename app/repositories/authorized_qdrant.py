"""在基础 Repository 上增加 ACL 过滤和安全存在性探测。"""

from qdrant_client import models

from app.core.exceptions import VectorStoreError
from app.models.domain import Chunk, VectorHit
from app.repositories.qdrant_repository import QdrantRepository

ACL_FIELDS = {
    "acl_schema",
    "acl_version",
    "is_global",
    "allowed_departments",
    "allowed_roles",
    "allowed_users",
}
PLATFORM_FIELDS = {"category", "is_enabled"}


class AuthorizedQdrantRepository(QdrantRepository):
    @staticmethod
    def _payload_to_chunk(payload: dict) -> Chunk:
        # 扩展字段留在向量 payload，与既有 Chunk 模型及历史数据保持兼容。
        return QdrantRepository._payload_to_chunk(
            {k: v for k, v in payload.items() if k not in ACL_FIELDS | PLATFORM_FIELDS}
        )

    def set_document_acl(self, tenant_id: str, kb_id: str, document_id: str, payload: dict) -> None:
        self.ensure_collection()
        try:
            self.client.set_payload(
                self.collection_name,
                payload=payload,
                points=self._document_filter(tenant_id, kb_id, document_id),
                wait=True,
            )
        except Exception as exc:
            raise VectorStoreError("权限索引同步失败，文档保持不可检索状态") from exc

    def set_document_metadata(self, tenant_id: str, kb_id: str, document_id: str, payload: dict) -> None:
        """同步台账字段；拒绝 ACL 字段，避免误覆盖权限索引。"""

        if set(payload) & ACL_FIELDS:
            raise VectorStoreError("文档元数据不得覆盖权限字段")
        self.ensure_collection()
        try:
            self.client.set_payload(
                self.collection_name,
                payload=payload,
                points=self._document_filter(tenant_id, kb_id, document_id),
                wait=True,
            )
        except Exception as exc:
            raise VectorStoreError("文档元数据同步失败") from exc

    def _ensure_payload_indexes(self) -> None:
        super()._ensure_payload_indexes()
        for name in ("allowed_departments", "allowed_roles", "allowed_users"):
            self.client.create_payload_index(
                self.collection_name, name, models.PayloadSchemaType.KEYWORD, wait=True
            )
        for name in ("acl_schema", "acl_version"):
            self.client.create_payload_index(
                self.collection_name, name, models.PayloadSchemaType.INTEGER, wait=True
            )
        self.client.create_payload_index(
            self.collection_name, "is_global", models.PayloadSchemaType.BOOL, wait=True
        )

    def query_authorized(self, embedding, user, kb_id: str, versions: dict, limit: int, metadata: dict):
        self.ensure_collection()
        if not versions:
            return [], []
        acl_or = [
            models.FieldCondition(key="is_global", match=models.MatchValue(value=True)),
            models.FieldCondition(key="allowed_users", match=models.MatchValue(value=user.id)),
        ]
        if user.department_id:
            acl_or.append(
                models.FieldCondition(
                    key="allowed_departments", match=models.MatchValue(value=user.department_id)
                )
            )
        if user.role_ids:
            acl_or.append(
                models.FieldCondition(key="allowed_roles", match=models.MatchAny(any=list(user.role_ids)))
            )
        # 当前关系库版本白名单与 Qdrant ACL 同时匹配，防止失效权限副本放行。
        versions_or = [
            models.Filter(
                must=[
                    models.FieldCondition(key="document_id", match=models.MatchValue(value=doc_id)),
                    models.FieldCondition(key="document_version", match=models.MatchValue(value=version)),
                    models.FieldCondition(key="acl_version", match=models.MatchValue(value=acl_version)),
                ]
            )
            for doc_id, (version, acl_version) in versions.items()
        ]
        query_filter = self._build_filter(user.tenant_id, kb_id, metadata)
        query_filter.must.extend(
            [
                models.FieldCondition(key="acl_schema", match=models.MatchValue(value=2)),
                models.Filter(should=acl_or),
                models.Filter(should=versions_or),
            ]
        )
        try:
            dense = self.client.query_points(
                self.collection_name,
                query=embedding.dense[0],
                using=self.dense_name,
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
            ).points
            sparse_vector = embedding.sparse[0]
            sparse = []
            if sparse_vector.indices:
                sparse = self.client.query_points(
                    self.collection_name,
                    query=models.SparseVector(**sparse_vector.model_dump()),
                    using=self.sparse_name,
                    query_filter=query_filter,
                    limit=limit,
                    with_payload=True,
                ).points
            return tuple(
                [VectorHit(chunk=self._payload_to_chunk(p.payload), score=p.score) for p in hits]
                for hits in (dense, sparse)
            )
        except Exception as exc:
            raise VectorStoreError("授权向量检索失败") from exc

    def restricted_document_hits(
        self, vector, tenant_id: str, kb_id: str, denied_ids: list[str], threshold: float
    ) -> list[tuple[str, float]]:
        """只取文档 ID 和分数；绝不加载受限正文、标题或 metadata。"""
        if not denied_ids:
            return []
        self.ensure_collection()
        query_filter = self._build_filter(tenant_id, kb_id, None)
        query_filter.must.append(
            models.FieldCondition(key="document_id", match=models.MatchAny(any=denied_ids))
        )
        try:
            result = self.client.query_points(
                self.collection_name,
                query=vector,
                using=self.dense_name,
                query_filter=query_filter,
                score_threshold=threshold,
                limit=min(len(denied_ids), 30),
                with_payload=["document_id"],
                with_vectors=False,
            )
            return [
                (str(point.payload.get("document_id")), float(point.score))
                for point in result.points
                if point.payload and point.payload.get("document_id") in denied_ids
            ]
        except Exception as exc:
            raise VectorStoreError("检索状态探测失败") from exc

    def restricted_hybrid_document_hits(
        self, embedding, tenant_id: str, kb_id: str, denied_ids: list[str], threshold: float
    ) -> list[tuple[str, float]]:
        """dense+sparse 都做受限存在性探测，只合并文档 ID 和最高分。"""

        merged = dict(
            self.restricted_document_hits(
                embedding.dense[0], tenant_id, kb_id, denied_ids, threshold
            )
        )
        sparse = embedding.sparse[0]
        if not denied_ids or not sparse.indices:
            return list(merged.items())
        query_filter = self._build_filter(tenant_id, kb_id, None)
        query_filter.must.append(
            models.FieldCondition(key="document_id", match=models.MatchAny(any=denied_ids))
        )
        try:
            result = self.client.query_points(
                self.collection_name,
                query=models.SparseVector(**sparse.model_dump()),
                using=self.sparse_name,
                query_filter=query_filter,
                score_threshold=threshold,
                limit=min(len(denied_ids), 30),
                with_payload=["document_id"],
                with_vectors=False,
            )
            for point in result.points:
                document_id = str((point.payload or {}).get("document_id", ""))
                if document_id in denied_ids:
                    merged[document_id] = max(merged.get(document_id, float("-inf")), float(point.score))
            return list(merged.items())
        except Exception as exc:
            raise VectorStoreError("稀疏检索状态探测失败") from exc

    def restricted_exists(
        self, vector, tenant_id: str, kb_id: str, denied_ids: list[str], threshold: float
    ) -> bool:
        return bool(self.restricted_document_hits(vector, tenant_id, kb_id, denied_ids, threshold))
