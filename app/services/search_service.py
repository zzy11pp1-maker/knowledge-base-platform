"""dense+sparse → RRF → Reranker 检索编排。"""

from app.core.protocols import EmbeddingProvider, RerankingProvider
from app.models.domain import FusedCandidate
from app.rag.rrf import reciprocal_rank_fusion
from app.repositories.qdrant_repository import QdrantRepository


class SearchService:
    """完成第一阶段可解释的混合检索闭环。"""

    def __init__(
        self,
        *,
        embedder: EmbeddingProvider,
        reranker: RerankingProvider,
        repository: QdrantRepository,
        rrf_k: int,
    ) -> None:
        self.embedder = embedder
        self.reranker = reranker
        self.repository = repository
        self.rrf_k = rrf_k

    def search(
        self,
        *,
        query: str,
        tenant_id: str,
        kb_id: str,
        candidate_limit: int,
        top_k: int,
        metadata_filters: dict[str, str | int | bool] | None = None,
    ) -> list[FusedCandidate]:
        query_embedding = self.embedder.embed([query])
        dense_hits = self.repository.search_dense(
            query_embedding.dense[0],
            tenant_id=tenant_id,
            kb_id=kb_id,
            limit=candidate_limit,
            metadata_filters=metadata_filters,
        )
        sparse_hits = self.repository.search_sparse(
            query_embedding.sparse[0],
            tenant_id=tenant_id,
            kb_id=kb_id,
            limit=candidate_limit,
            metadata_filters=metadata_filters,
        )
        candidates = reciprocal_rank_fusion(dense_hits, sparse_hits, k=self.rrf_k)[:candidate_limit]
        if not candidates:
            return []
        scores = self.reranker.rerank(query, [item.chunk.content for item in candidates])
        for item, score in zip(candidates, scores):
            item.rerank_score = score
        candidates.sort(
            key=lambda item: (
                -(item.rerank_score if item.rerank_score is not None else float("-inf")),
                -item.rrf_score,
                item.chunk.chunk_id,
            )
        )
        return candidates[:top_k]
