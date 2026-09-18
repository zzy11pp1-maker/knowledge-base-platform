"""身份与权限单元/组件测试：真实 SQLite 和 Qdrant，模型替身仅存在于本测试文件。"""

import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from qdrant_client import QdrantClient
from sqlalchemy import select

from app.api.chat import get_chat_service
from app.core.auth import verify_password
from app.core.business_dependencies import get_knowledge_service
from app.core.config import get_settings
from app.db.models import Message, User
from app.db.session import close_database, session_factory
from app.ingestion.readers import TextDocumentReader
from app.integrations.llm_client import LLMServiceError
from app.models.domain import EmbeddingBatch, SparseEmbedding
from app.repositories.authorized_qdrant import AuthorizedQdrantRepository
from app.services.authorized_knowledge import AuthorizedKnowledgeService
from app.services.chat import ChatService, CitationStream
from app.services.document_service import DocumentService
from app.services.identity_service import bootstrap_admin
from main import app


class UnitEmbedder:
    def embed(self, texts):
        return EmbeddingBatch(
            dense=[[1.0, 0.0, 0.0, 0.0] for _ in texts],
            sparse=[SparseEmbedding(indices=[1], values=[1.0]) for _ in texts],
        )


class UnitReranker:
    def __init__(self):
        self.inputs = []

    def rerank(self, query, texts):
        self.inputs.extend(texts)
        return [1.0 for _ in texts]


class UnitLLM:
    def __init__(self):
        self.inputs = []
        self.fail = False
        self.closed = False

    async def stream(self, messages):
        self.inputs.append(messages)
        try:
            if self.fail:
                raise LLMServiceError("unit failure")
            yield "测试回答 "
            yield "[S"
            yield "1]"
        finally:
            self.closed = True


class Phase2Test(unittest.TestCase):
    def setUp(self):
        close_database()
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(
            os.environ,
            {
                "DATABASE_URL": "sqlite:///" + self.tmp.name.replace("\\", "/") + "/test.db",
                "JWT_SECRET": "unit-only-secret-not-for-production-0000",
                "LOG_LEVEL": "ERROR",
            },
        )
        self.env.start()
        get_settings.cache_clear()
        self.client = TestClient(app, raise_server_exceptions=True)
        self.client.__enter__()
        self.vector = QdrantClient(":memory:")
        self.repo = AuthorizedQdrantRepository(self.vector, "unit_acl", 4)
        embed = UnitEmbedder()
        self.reranker = UnitReranker()
        self.knowledge = AuthorizedKnowledgeService(
            DocumentService(
                reader=TextDocumentReader(100000),
                embedder=embed,
                repository=self.repo,
                max_chunk_size=1000,
                chunk_overlap=100,
                embedding_model_id="unit-only",
            ),
            self.repo,
            embed,
            self.reranker,
        )
        self.llm = UnitLLM()
        app.dependency_overrides[get_knowledge_service] = lambda: self.knowledge
        app.dependency_overrides[get_chat_service] = lambda: ChatService(self.knowledge, self.llm)
        with session_factory()() as db:
            admin = bootstrap_admin(db, "unit", "admin", "Unit-password-42")
            db.commit()
            self.admin_id = admin.id
        self.admin = self.login("admin")
        self.dept = self.req("POST", "/departments", {"name": "tech"}).json()["id"]
        self.role = self.req(
            "POST", "/roles", {"name": "reader", "permission_codes": ["knowledge:read", "chat:use"]}
        ).json()["id"]
        self.tech_id = self.req(
            "POST",
            "/users",
            {
                "username": "tech",
                "password": "Unit-password-42",
                "department_id": self.dept,
                "role_ids": [self.role],
            },
        ).json()["id"]
        self.sales_id = self.req(
            "POST", "/users", {"username": "sales", "password": "Unit-password-42", "role_ids": [self.role]}
        ).json()["id"]
        self.tech, self.sales = self.login("tech"), self.login("sales")
        self.kb = self.req("POST", "/knowledge-bases", {"name": "test kb"}).json()["id"]
        for user_id in (self.tech_id, self.sales_id):
            self.req("POST", f"/knowledge-bases/{self.kb}/members", {"user_id": user_id, "role": "viewer"})

    def tearDown(self):
        app.dependency_overrides.clear()
        self.client.__exit__(None, None, None)
        self.vector.close()
        self.env.stop()
        get_settings.cache_clear()
        self.tmp.cleanup()

    def login(self, username):
        response = self.client.post(
            "/auth/login", json={"tenant_id": "unit", "username": username, "password": "Unit-password-42"}
        )
        self.assertEqual(response.status_code, 200)
        return {"Authorization": "Bearer " + response.json()["access_token"]}

    def req(self, method, path, body=None, headers=None):
        return self.client.request(
            method, path, json=body, headers=self.admin if headers is None else headers
        )

    def doc(self, entries=None):
        response = self.client.post(
            "/documents/import",
            data={"kb_id": self.kb},
            files={"file": ("secret.md", "# Secret\nInternal code Apollo-77", "text/markdown")},
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 201, response.text)
        doc_id = response.json()["id"]
        if entries is not None:
            result = self.req("PUT", f"/documents/{doc_id}/permissions", {"entries": entries})
            self.assertEqual(result.status_code, 200, result.text)
        return doc_id

    def search(self, headers=None, **fields):
        return self.req(
            "POST", "/search", {"kb_id": self.kb, "query": "code", **fields}, headers=headers or self.tech
        )

    def test_01_login_and_me(self):
        result = self.req("GET", "/auth/me", headers=self.tech)
        self.assertEqual(result.json()["id"], self.tech_id)
        self.assertNotIn("password", result.text)

    def test_02_wrong_password(self):
        self.assertEqual(
            self.client.post(
                "/auth/login", json={"tenant_id": "unit", "username": "admin", "password": "wrong"}
            ).status_code,
            401,
        )

    def test_03_missing_token(self):
        self.assertEqual(
            self.req("POST", "/search", {"kb_id": self.kb, "query": "x"}, headers={}).status_code, 401
        )

    def test_04_invalid_token(self):
        self.assertEqual(
            self.req("GET", "/auth/me", headers={"Authorization": "Bearer bad"}).status_code, 401
        )

    def test_05_disabled_token(self):
        self.req("PATCH", f"/users/{self.tech_id}", {"is_active": False})
        self.assertEqual(self.req("GET", "/auth/me", headers=self.tech).status_code, 401)

    def test_06_password_hash(self):
        with session_factory()() as db:
            value = db.get(User, self.tech_id).password_hash
            self.assertTrue(value.startswith("$argon2id$"))
            self.assertTrue(verify_password("Unit-password-42", value))
            self.assertFalse(verify_password("wrong", value))

    def test_07_rbac_management_denied(self):
        for path in ("/users", "/roles", "/departments", "/permissions"):
            self.assertEqual(self.req("GET", path, headers=self.tech).status_code, 403)

    def test_08_unknown_permission(self):
        self.assertEqual(
            self.req("POST", "/roles", {"name": "bad", "permission_codes": ["all:admin"]}).status_code, 422
        )

    def test_09_role_changes_live(self):
        self.req("PATCH", f"/roles/{self.role}", {"permission_codes": ["chat:use"]})
        self.assertEqual(self.search().status_code, 403)

    def test_10_department_cycle(self):
        child = self.req("POST", "/departments", {"name": "child", "parent_id": self.dept}).json()["id"]
        self.assertEqual(
            self.req("PATCH", f"/departments/{self.dept}", {"parent_id": child}).status_code, 422
        )

    def test_11_department_tree(self):
        self.req("POST", "/departments", {"name": "child", "parent_id": self.dept})
        self.assertEqual(len(self.req("GET", "/departments").json()[0]["children"]), 1)

    def test_12_department_in_use(self):
        self.assertEqual(self.req("DELETE", f"/departments/{self.dept}").status_code, 409)

    def test_13_nonmember_kb_denied(self):
        self.req("DELETE", f"/knowledge-bases/{self.kb}/members/{self.tech_id}")
        self.assertEqual(self.search().status_code, 404)

    def test_14_viewer_cannot_upload(self):
        self.assertEqual(
            self.client.post(
                "/documents/import",
                data={"kb_id": self.kb},
                files={"file": ("x.txt", "test")},
                headers=self.tech,
            ).status_code,
            403,
        )

    def test_15_viewer_cannot_change_acl(self):
        doc = self.doc()
        self.assertEqual(
            self.req("PUT", f"/documents/{doc}/permissions", {"entries": []}, headers=self.tech).status_code,
            403,
        )

    def test_16_owner_cannot_be_removed(self):
        self.assertEqual(
            self.req("DELETE", f"/knowledge-bases/{self.kb}/members/{self.admin_id}").status_code, 409
        )

    def test_17_global_acl(self):
        self.doc([{"permission_type": "global"}])
        self.assertEqual(self.search(self.sales).json()["count"], 1)

    def test_18_department_acl(self):
        self.doc([{"permission_type": "department", "target_id": self.dept}])
        self.assertEqual(self.search().json()["count"], 1)
        self.assertEqual(self.search(self.sales).json()["count"], 0)

    def test_19_role_acl(self):
        self.doc([{"permission_type": "role", "target_id": self.role}])
        self.assertEqual(self.search().json()["count"], 1)

    def test_20_user_acl(self):
        self.doc([{"permission_type": "user", "target_id": self.sales_id}])
        self.assertEqual(self.search(self.sales).json()["count"], 1)
        self.assertEqual(self.search().json()["count"], 0)

    def test_21_empty_acl_denies_admin(self):
        self.doc([])
        self.assertEqual(self.search(self.admin).json()["count"], 0)

    def test_22_acl_or(self):
        self.doc(
            [
                {"permission_type": "department", "target_id": self.dept},
                {"permission_type": "user", "target_id": self.sales_id},
            ]
        )
        self.assertEqual(self.search().json()["count"], 1)
        self.assertEqual(self.search(self.sales).json()["count"], 1)

    def test_23_cross_tenant_parameter(self):
        self.assertEqual(self.search(tenant_id="foreign").status_code, 403)

    def test_24_no_metadata_leak(self):
        doc = self.doc([])
        self.assertEqual(self.req("GET", f"/documents/{doc}", headers=self.tech).status_code, 404)
        self.assertEqual(self.req("GET", f"/documents?kb_id={self.kb}", headers=self.tech).json(), [])
        self.assertNotIn("secret", self.search().text.lower())

    def test_24b_sparse_only_restricted_recall_is_detected_without_content(self):
        document_id = self.doc([])
        hits = self.repo.restricted_hybrid_document_hits(
            EmbeddingBatch(
                dense=[[0.0, 1.0, 0.0, 0.0]],
                sparse=[SparseEmbedding(indices=[1], values=[1.0])],
            ),
            "unit",
            self.kb,
            [document_id],
            0.35,
        )
        self.assertEqual([item[0] for item in hits], [document_id])

    def test_25_no_reranker_leak(self):
        self.doc([])
        self.search()
        self.assertEqual(self.reranker.inputs, [])

    def test_26_no_llm_leak(self):
        self.doc([])
        result = self.req("POST", "/chat", {"kb_id": self.kb, "query": "code?"}, self.tech)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.llm.inputs, [])
        self.assertNotIn("Apollo", result.text)
        self.assertEqual(result.json()["citations"], [])

    def test_27_hybrid_rrf(self):
        self.doc([{"permission_type": "global"}])
        hit = self.search().json()["items"][0]
        self.assertEqual((hit["dense_rank"], hit["sparse_rank"]), (1, 1))
        self.assertAlmostEqual(hit["rrf_score"], 2 / 61)

    def test_28_acl_payload(self):
        self.doc([{"permission_type": "department", "target_id": self.dept}])
        points, _ = self.vector.scroll("unit_acl", with_payload=True)
        self.assertEqual(points[0].payload["allowed_departments"], [self.dept])
        self.assertEqual(points[0].payload["acl_schema"], 2)

    def test_29_sync_chat_citation(self):
        doc = self.doc([{"permission_type": "global"}])
        result = self.req("POST", "/chat", {"kb_id": self.kb, "query": "code?"}, self.tech)
        self.assertEqual(result.status_code, 200, result.text)
        citation = result.json()["citations"][0]
        self.assertEqual(citation["document_id"], doc)
        self.assertTrue(citation["chunk_id"])
        self.assertTrue(self.llm.closed)

    def test_30_sse_events(self):
        self.doc([{"permission_type": "global"}])
        result = self.req("POST", "/chat/stream", {"kb_id": self.kb, "query": "code?"}, self.tech)
        self.assertIn("text/event-stream", result.headers["content-type"])
        for kind in ("start", "token", "citation", "done"):
            self.assertIn("event: " + kind, result.text)
        self.assertNotIn("event: error", result.text)

    def test_31_sse_error_not_done(self):
        self.doc([{"permission_type": "global"}])
        self.llm.fail = True
        result = self.req("POST", "/chat/stream", {"kb_id": self.kb, "query": "x"}, self.tech)
        self.assertIn("event: error", result.text)
        self.assertNotIn("event: done", result.text)
        with session_factory()() as db:
            row = db.scalar(select(Message).where(Message.role == "assistant"))
            self.assertEqual(row.content, "")
            self.assertEqual(row.status, "failed")

    def test_32_conversation_isolation(self):
        conv = self.req("POST", "/conversations", {"kb_id": self.kb}, self.tech).json()["id"]
        self.assertEqual(
            self.req("GET", f"/conversations/{conv}/messages", headers=self.sales).status_code, 404
        )

    def test_33_history_persisted(self):
        self.doc([{"permission_type": "global"}])
        result = self.req("POST", "/chat", {"kb_id": self.kb, "query": "x"}, self.tech).json()
        messages = self.req(
            "GET", f"/conversations/{result['conversation_id']}/messages", headers=self.tech
        ).json()
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[-1]["content"], result["answer"])

    def test_34_history_revoked(self):
        doc = self.doc([{"permission_type": "global"}])
        result = self.req("POST", "/chat", {"kb_id": self.kb, "query": "x"}, self.tech).json()
        self.req("PUT", f"/documents/{doc}/permissions", {"entries": []})
        messages = self.req(
            "GET", f"/conversations/{result['conversation_id']}/messages", headers=self.tech
        ).json()
        self.assertEqual(messages[-1]["status"], "restricted")
        self.assertEqual(messages[-1]["content"], "")
        self.assertEqual(messages[-1]["citations"], [])

    def test_35_citation_parser(self):
        parser = CitationStream({"[S1]"})
        self.assertEqual(parser.feed("a [S"), "a ")
        self.assertEqual(parser.feed("999] b [S1]"), " b [S1]")

    def test_36_soft_delete(self):
        self.doc([{"permission_type": "global"}])
        self.req("DELETE", f"/knowledge-bases/{self.kb}")
        self.assertEqual(self.search().status_code, 404)

    def test_37_unknown_input_rejected(self):
        self.assertEqual(
            self.req(
                "POST", "/users", {"username": "bad", "password": "Unit-password-42", "tenant_id": "foreign"}
            ).status_code,
            422,
        )

    def test_38_no_password_validation_echo(self):
        result = self.req("POST", "/users", {"username": "bad", "password": "sh0rt"})
        self.assertEqual(result.status_code, 422)
        self.assertNotIn("sh0rt", result.text)

    def test_39_protected_health(self):
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertEqual(self.req("GET", "/health/dependencies", headers=self.tech).status_code, 403)


if __name__ == "__main__":
    unittest.main()
