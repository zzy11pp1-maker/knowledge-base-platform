"""Reciprocal Rank Fusion（RRF）排序融合。"""

from app.models.domain import FusedCandidate, VectorHit


def reciprocal_rank_fusion(
    dense_hits: list[VectorHit],
    sparse_hits: list[VectorHit],
    *,
    k: int = 60,
) -> list[FusedCandidate]:
    """按排名而非不可比的原始分数量纲融合 dense 与 sparse 结果。"""

    if k <= 0:
        raise ValueError("RRF 的 k 必须大于 0")
    candidates: dict[str, FusedCandidate] = {}
    for rank, hit in enumerate(dense_hits, start=1):
        item = candidates.setdefault(hit.chunk.chunk_id, FusedCandidate(chunk=hit.chunk))
        if item.dense_rank is None:  # 防御重复命中，只记最优排名
            item.dense_rank = rank
            item.dense_score = hit.score
            item.rrf_score += 1.0 / (k + rank)
    for rank, hit in enumerate(sparse_hits, start=1):
        item = candidates.setdefault(hit.chunk.chunk_id, FusedCandidate(chunk=hit.chunk))
        if item.sparse_rank is None:
            item.sparse_rank = rank
            item.sparse_score = hit.score
            item.rrf_score += 1.0 / (k + rank)
    return sorted(
        candidates.values(),
        key=lambda item: (-item.rrf_score, item.chunk.chunk_id),
    )
