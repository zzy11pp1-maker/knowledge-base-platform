"""在不连接外部服务时验证 FastAPI 契约和 multipart 导入。"""

import unittest
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.router import router
from app.core.dependencies import get_document_service, get_search_service
from app.models.domain import Chunk, DocumentSummary, FusedCandidate, IngestionResult

# 单独保留基础路由契约回归；正式 main 只挂载受保护认证路由。
app = FastAPI()
app.include_router(router)


SUMMARY = DocumentSummary(
    document_id="doc-1",
    tenant_id="tenant-1",
    kb_id="kb-1",
    title="test",
    source="test.md",
    document_version="v1",
    chunk_count=1,
    created_at=datetime.now(timezone.utc),
)


class DocumentServiceStub:
    def import_bytes(self, **kwargs) -> IngestionResult:
        assert kwargs["filename"] == "test.md"
        assert kwargs["tenant_id"] == "tenant-1"
        return IngestionResult(document=SUMMARY, upserted_chunks=1)

    def list_documents(self, **kwargs) -> list[DocumentSummary]:
        assert kwargs["kb_id"] == "kb-1"
        return [SUMMARY]

    def delete_document(self, **kwargs) -> int:
        assert kwargs["document_id"] == "doc-1"
        return 1


class SearchServiceStub:
    def search(self, **kwargs) -> list[FusedCandidate]:
        assert kwargs["query"] == "知识库支持什么？"
        chunk = Chunk(
            chunk_id="00000000-0000-0000-0000-000000000001",
            tenant_id="tenant-1",
            kb_id="kb-1",
            document_id="doc-1",
            chunk_index=0,
            content="企业知识库支持 Markdown 文档。",
            title="知识库",
            source="test.md",
            document_version="v1",
            content_hash="hash-1",
        )
        return [
            FusedCandidate(
                chunk=chunk,
                dense_rank=1,
                sparse_rank=1,
                dense_score=0.9,
                sparse_score=1.2,
                rrf_score=0.03,
                rerank_score=2.5,
            )
        ]


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        app.dependency_overrides[get_document_service] = lambda: DocumentServiceStub()
        app.dependency_overrides[get_search_service] = lambda: SearchServiceStub()
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        app.dependency_overrides.clear()

    def test_document_import_list_and_delete(self) -> None:
        imported = self.client.post(
            "/documents/import",
            data={"tenant_id": "tenant-1", "kb_id": "kb-1"},
            files={"file": ("test.md", b"# test", "text/markdown")},
        )
        self.assertEqual(imported.status_code, 201)
        self.assertEqual(imported.json()["upserted_chunks"], 1)

        listed = self.client.get("/documents", params={"tenant_id": "tenant-1", "kb_id": "kb-1"})
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["count"], 1)

        deleted = self.client.delete(
            "/documents/doc-1",
            params={"tenant_id": "tenant-1", "kb_id": "kb-1"},
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json()["deleted_chunks"], 1)

    def test_search_contract(self) -> None:
        response = self.client.post(
            "/search",
            json={
                "tenant_id": "tenant-1",
                "kb_id": "kb-1",
                "query": "知识库支持什么？",
                "candidate_limit": 10,
                "top_k": 3,
            },
        )
        self.assertEqual(response.status_code, 200)
        item = response.json()["items"][0]
        self.assertEqual(item["dense_rank"], 1)
        self.assertEqual(item["sparse_rank"], 1)
        self.assertEqual(item["rerank_score"], 2.5)


if __name__ == "__main__":
    unittest.main()
