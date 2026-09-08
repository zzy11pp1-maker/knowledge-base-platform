"""RRF 融合测试。"""

import unittest
from datetime import datetime, timezone

from app.models.domain import Chunk, VectorHit
from app.rag.rrf import reciprocal_rank_fusion


def make_chunk(index: int) -> Chunk:
    return Chunk(
        chunk_id=f"00000000-0000-0000-0000-{index:012d}",
        tenant_id="t1",
        kb_id="kb1",
        document_id="doc1",
        chunk_index=index,
        content=f"内容 {index}",
        title="标题",
        source="test.md",
        document_version="v1",
        content_hash=f"hash-{index}",
        created_at=datetime.now(timezone.utc),
    )


class RrfTest(unittest.TestCase):
    def test_fuses_and_keeps_both_ranks(self) -> None:
        first, second, third = make_chunk(1), make_chunk(2), make_chunk(3)
        dense = [VectorHit(chunk=first, score=0.9), VectorHit(chunk=second, score=0.8)]
        sparse = [VectorHit(chunk=second, score=2.0), VectorHit(chunk=third, score=1.0)]
        result = reciprocal_rank_fusion(dense, sparse, k=60)
        self.assertEqual(result[0].chunk.chunk_id, second.chunk_id)
        self.assertEqual(result[0].dense_rank, 2)
        self.assertEqual(result[0].sparse_rank, 1)
        self.assertGreater(result[0].rrf_score, result[1].rrf_score)

    def test_rejects_invalid_k(self) -> None:
        with self.assertRaises(ValueError):
            reciprocal_rank_fusion([], [], k=0)


if __name__ == "__main__":
    unittest.main()
