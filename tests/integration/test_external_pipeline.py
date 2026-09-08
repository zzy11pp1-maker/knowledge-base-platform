"""真实外部服务集成测试。

仅当 ``RUN_EXTERNAL_INTEGRATION=1`` 时执行；不会用 mock 冒充远程服务成功。
"""

import os
import unittest
from pathlib import Path

from app.core.config import get_settings
from app.core.dependencies import close_dependencies, get_document_service, get_search_service


@unittest.skipUnless(os.getenv("RUN_EXTERNAL_INTEGRATION") == "1", "未启用真实外部服务测试")
class ExternalPipelineTest(unittest.TestCase):
    imported_document: tuple[str, str, str] | None = None

    def tearDown(self) -> None:
        try:
            if self.imported_document is not None:
                tenant_id, kb_id, document_id = self.imported_document
                get_document_service().delete_document(
                    tenant_id=tenant_id,
                    kb_id=kb_id,
                    document_id=document_id,
                )
        finally:
            close_dependencies()
            get_settings.cache_clear()

    def test_import_search_and_delete(self) -> None:
        tenant_id = os.getenv("TEST_TENANT_ID", "tenant-integration")
        kb_id = os.getenv("TEST_KB_ID", "kb-integration")
        query = os.getenv("TEST_QUERY", "企业知识库支持什么功能？")
        path = Path(__file__).resolve().parents[2] / "data" / "test.md"

        imported = get_document_service().import_path(path, tenant_id=tenant_id, kb_id=kb_id)
        # 即使后续检索或精排断言失败，tearDown 也会回收本次测试数据。
        self.imported_document = (tenant_id, kb_id, imported.document.document_id)
        self.assertGreater(imported.upserted_chunks, 0)
        self.assertEqual(
            get_document_service().repository.count_document(
                tenant_id=tenant_id,
                kb_id=kb_id,
                document_id=imported.document.document_id,
            ),
            imported.upserted_chunks,
        )

        results = get_search_service().search(
            query=query,
            tenant_id=tenant_id,
            kb_id=kb_id,
            candidate_limit=10,
            top_k=3,
        )
        self.assertGreater(len(results), 0)
        self.assertTrue(all(item.rerank_score is not None for item in results))

        deleted = get_document_service().delete_document(
            tenant_id=tenant_id,
            kb_id=kb_id,
            document_id=imported.document.document_id,
        )
        self.assertEqual(deleted, imported.upserted_chunks)
        self.imported_document = None


if __name__ == "__main__":
    unittest.main()
