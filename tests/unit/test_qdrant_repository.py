"""使用 Qdrant 本地内存模式验证真实写入、过滤、检索与删除。"""

import tempfile
import unittest
from datetime import datetime, timezone

from qdrant_client import QdrantClient

from app.models.domain import Chunk, EmbeddingBatch, SparseEmbedding
from app.repositories.qdrant_repository import QdrantRepository


def make_chunk(index: int, tenant_id: str = "tenant-a", category: str = "guide") -> Chunk:
    return Chunk(
        chunk_id=f"00000000-0000-0000-0000-{index:012d}",
        tenant_id=tenant_id,
        kb_id="kb-1",
        document_id=f"doc-{tenant_id}",
        chunk_index=index,
        content=f"知识内容 {index}",
        title="测试文档",
        source="test.md",
        document_version="v1",
        content_hash=f"hash-{index}",
        metadata={"category": category},
        created_at=datetime.now(timezone.utc),
    )


class QdrantRepositoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = QdrantClient(location=":memory:")
        # 三条数据按每批两条写入，确保测试实际经过分批 upsert 路径。
        self.repository = QdrantRepository(
            self.client,
            "test_chunks",
            dense_vector_size=4,
            upsert_batch_size=2,
        )
        self.chunks = [make_chunk(1), make_chunk(2), make_chunk(3, tenant_id="tenant-b")]
        embeddings = EmbeddingBatch(
            dense=[[1, 0, 0, 0], [0.9, 0.1, 0, 0], [1, 0, 0, 0]],
            sparse=[
                SparseEmbedding(indices=[1], values=[1.0]),
                SparseEmbedding(indices=[2], values=[1.0]),
                SparseEmbedding(indices=[1], values=[1.0]),
            ],
        )
        self.repository.upsert_chunks(self.chunks, embeddings)

    def tearDown(self) -> None:
        self.client.close()

    def test_dense_and_sparse_search_enforce_tenant(self) -> None:
        dense = self.repository.search_dense(
            [1, 0, 0, 0], tenant_id="tenant-a", kb_id="kb-1", limit=10
        )
        sparse = self.repository.search_sparse(
            SparseEmbedding(indices=[1], values=[1.0]),
            tenant_id="tenant-a",
            kb_id="kb-1",
            limit=10,
        )
        self.assertEqual({hit.chunk.tenant_id for hit in dense}, {"tenant-a"})
        self.assertEqual([hit.chunk.chunk_id for hit in sparse], [self.chunks[0].chunk_id])

    def test_empty_sparse_query_returns_no_results(self) -> None:
        hits = self.repository.search_sparse(
            SparseEmbedding(indices=[], values=[]),
            tenant_id="tenant-a",
            kb_id="kb-1",
            limit=10,
        )
        self.assertEqual(hits, [])

    def test_metadata_filter_and_document_list(self) -> None:
        hits = self.repository.search_dense(
            [1, 0, 0, 0],
            tenant_id="tenant-a",
            kb_id="kb-1",
            limit=10,
            metadata_filters={"category": "guide"},
        )
        documents = self.repository.list_documents(tenant_id="tenant-a", kb_id="kb-1", offset=0, limit=10)
        self.assertEqual(len(hits), 2)
        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0].chunk_count, 2)

    def test_delete_document(self) -> None:
        deleted = self.repository.delete_document(
            tenant_id="tenant-a", kb_id="kb-1", document_id="doc-tenant-a"
        )
        self.assertEqual(deleted, 2)
        self.assertEqual(
            self.repository.count_document(
                tenant_id="tenant-a", kb_id="kb-1", document_id="doc-tenant-a"
            ),
            0,
        )
        self.assertEqual(
            self.repository.count_document(
                tenant_id="tenant-b", kb_id="kb-1", document_id="doc-tenant-b"
            ),
            1,
        )

    def test_local_persistent_storage_survives_reopen(self) -> None:
        """额外验证无 Docker 开发模式不是纯内存假实现。"""

        with tempfile.TemporaryDirectory() as temp_dir:
            first_client = QdrantClient(path=temp_dir)
            first_repo = QdrantRepository(first_client, "persistent_chunks", dense_vector_size=4)
            chunk = make_chunk(99)
            first_repo.upsert_chunks(
                [chunk],
                EmbeddingBatch(
                    dense=[[1, 0, 0, 0]],
                    sparse=[SparseEmbedding(indices=[1], values=[1.0])],
                ),
            )
            first_client.close()

            second_client = QdrantClient(path=temp_dir)
            second_repo = QdrantRepository(second_client, "persistent_chunks", dense_vector_size=4)
            try:
                self.assertEqual(
                    second_repo.count_document(
                        tenant_id=chunk.tenant_id,
                        kb_id=chunk.kb_id,
                        document_id=chunk.document_id,
                    ),
                    1,
                )
            finally:
                second_client.close()


if __name__ == "__main__":
    unittest.main()
