"""使用确定性测试替身与真实 Qdrant 本地引擎验证业务编排。

测试替身只替代当前不可用的 GPU HTTP 服务；真实外部联调由 integration 测试负责，
两者不会混淆为同一验证结果。
"""

import unittest

from qdrant_client import QdrantClient

from app.ingestion.readers import TextDocumentReader
from app.models.domain import Chunk, EmbeddingBatch, SparseEmbedding
from app.repositories.qdrant_repository import QdrantRepository
from app.services.document_service import DocumentService
from app.services.search_service import SearchService


class DeterministicEmbedder:
    """只用于验证服务编排的数据确定性替身。"""

    def embed(self, texts: list[str]) -> EmbeddingBatch:
        dense = []
        sparse = []
        for text in texts:
            if "知识库" in text:
                dense.append([1.0, 0.0])
                sparse.append(SparseEmbedding(indices=[1], values=[1.0]))
            else:
                dense.append([0.0, 1.0])
                sparse.append(SparseEmbedding(indices=[2], values=[1.0]))
        return EmbeddingBatch(dense=dense, sparse=sparse)


class DeterministicReranker:
    """根据候选文本返回可预测分数，以断言精排顺序。"""

    def rerank(self, query: str, texts: list[str]) -> list[float]:
        del query
        return [1.0 if "企业知识库" in text else 0.1 for text in texts]


class ServicesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = QdrantClient(location=":memory:")
        self.repository = QdrantRepository(self.client, "service_test", dense_vector_size=2)
        self.embedder = DeterministicEmbedder()

    def tearDown(self) -> None:
        self.client.close()

    def test_import_is_idempotent_and_reindex_removes_old_version(self) -> None:
        reader = TextDocumentReader(max_bytes=10_000)
        service = DocumentService(
            reader=reader,
            embedder=self.embedder,
            repository=self.repository,
            max_chunk_size=80,
            chunk_overlap=10,
            embedding_model_id="test-model",
        )
        content = ("# 企业知识库\n\n" + "企业知识库支持文档导入和检索。" * 20).encode("utf-8")
        first = service.import_bytes(filename="test.md", data=content, tenant_id="t1", kb_id="kb1")
        second = service.import_bytes(filename="test.md", data=content, tenant_id="t1", kb_id="kb1")
        self.assertEqual(first.document.document_id, second.document.document_id)
        self.assertEqual(first.document.document_version, second.document.document_version)
        self.assertEqual(
            self.repository.count_document(tenant_id="t1", kb_id="kb1", document_id=first.document.document_id),
            second.upserted_chunks,
        )

        reindex_service = DocumentService(
            reader=reader,
            embedder=self.embedder,
            repository=self.repository,
            max_chunk_size=45,
            chunk_overlap=5,
            embedding_model_id="test-model",
        )
        reindexed = reindex_service.import_bytes(
            filename="test.md", data=content, tenant_id="t1", kb_id="kb1"
        )
        self.assertNotEqual(first.document.document_version, reindexed.document.document_version)
        self.assertEqual(
            self.repository.count_document(tenant_id="t1", kb_id="kb1", document_id=first.document.document_id),
            reindexed.upserted_chunks,
        )

    def test_hybrid_search_rrf_and_reranker(self) -> None:
        chunks = [
            Chunk(
                chunk_id="00000000-0000-0000-0000-000000000001",
                tenant_id="t1",
                kb_id="kb1",
                document_id="doc1",
                chunk_index=0,
                content="企业知识库支持 Markdown 文档",
                title="知识库",
                source="test.md",
                document_version="v1",
                content_hash="h1",
            ),
            Chunk(
                chunk_id="00000000-0000-0000-0000-000000000002",
                tenant_id="t1",
                kb_id="kb1",
                document_id="doc1",
                chunk_index=1,
                content="智能客服回答问题",
                title="客服",
                source="test.md",
                document_version="v1",
                content_hash="h2",
            ),
        ]
        self.repository.upsert_chunks(chunks, self.embedder.embed([chunk.content for chunk in chunks]))
        service = SearchService(
            embedder=self.embedder,
            reranker=DeterministicReranker(),
            repository=self.repository,
            rrf_k=60,
        )
        result = service.search(
            query="企业知识库支持什么？",
            tenant_id="t1",
            kb_id="kb1",
            candidate_limit=10,
            top_k=2,
        )
        self.assertEqual(result[0].chunk.chunk_id, chunks[0].chunk_id)
        self.assertEqual(result[0].dense_rank, 1)
        self.assertEqual(result[0].sparse_rank, 1)
        self.assertGreater(result[0].rrf_score, 0)
        self.assertEqual(result[0].rerank_score, 1.0)


if __name__ == "__main__":
    unittest.main()
