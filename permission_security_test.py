"""真实权限泄漏验证：外部服务照常调用，探针仅记录测试标记是否越界。"""

import json
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.chat import get_chat_service
from app.core.business_dependencies import get_knowledge_service
from app.db.session import initialize_database, session_factory
from app.integrations.llm_client import CompatibleLLMClient
from app.services.chat import ChatService
from app.services.identity_service import bootstrap_admin
from main import app

MARKER = "Apollo-77"
PASSWORD = "Permission-test-password-42"


class AuditReranker:
    def __init__(self, inner):
        self.inner = inner
        self.marker_seen = False

    def rerank(self, query, texts):
        self.marker_seen |= any(MARKER in text for text in texts)
        return self.inner.rerank(query, texts)


class AuditLLM:
    def __init__(self, inner):
        self.inner = inner
        self.marker_seen = False

    async def stream(self, messages):
        self.marker_seen |= any(MARKER in item.get("content", "") for item in messages)
        async for token in self.inner.stream(messages):
            yield token


def request(client, token, method, path, body=None, **kwargs):
    response = client.request(method, path, json=body, headers={"Authorization": "Bearer " + token}, **kwargs)
    if response.status_code not in (200, 201, 204):
        raise AssertionError(f"{method} {path}: HTTP {response.status_code}")
    return response


def login(client, tenant, username):
    response = client.post(
        "/auth/login", json={"tenant_id": tenant, "username": username, "password": PASSWORD}
    )
    if response.status_code != 200:
        raise AssertionError("登录失败 HTTP " + str(response.status_code))
    return response.json()["access_token"]


def main():
    tenant = "permission-" + uuid4().hex[:10]
    initialize_database()
    with session_factory()() as db:
        bootstrap_admin(db, tenant, "admin", PASSWORD)
        db.commit()
    checks = {}
    with TestClient(app) as client:
        knowledge = get_knowledge_service()
        reranker = AuditReranker(knowledge.reranker)
        knowledge.reranker = reranker
        llm = AuditLLM(CompatibleLLMClient())
        app.dependency_overrides[get_chat_service] = lambda: ChatService(knowledge, llm)
        try:
            admin = login(client, tenant, "admin")
            tech_dept = request(client, admin, "POST", "/departments", {"name": "技术部"}).json()["id"]
            reader = request(
                client,
                admin,
                "POST",
                "/roles",
                {"name": "知识用户", "permission_codes": ["knowledge:read", "chat:use"]},
            ).json()["id"]
            users = {}
            for name, department in (("tech", tech_dept), ("sales", None)):
                users[name] = request(
                    client,
                    admin,
                    "POST",
                    "/users",
                    {
                        "username": name,
                        "password": PASSWORD,
                        "department_id": department,
                        "role_ids": [reader],
                    },
                ).json()["id"]
            kb = request(client, admin, "POST", "/knowledge-bases", {"name": "权限验证"}).json()["id"]
            for user_id in users.values():
                request(
                    client,
                    admin,
                    "POST",
                    f"/knowledge-bases/{kb}/members",
                    {"user_id": user_id, "role": "viewer"},
                )
            doc = request(
                client,
                admin,
                "POST",
                "/documents/import",
                files={
                    "file": (
                        "secret.md",
                        "# 技术机密\n内部技术发布代号是 Apollo-77，仅限技术部。",
                        "text/markdown",
                    )
                },
                data={"kb_id": kb},
            ).json()["id"]
            request(
                client,
                admin,
                "PUT",
                f"/documents/{doc}/permissions",
                {"entries": [{"permission_type": "department", "target_id": tech_dept}]},
            )
            tech = login(client, tenant, "tech")
            answer = request(
                client, tech, "POST", "/chat", {"kb_id": kb, "query": "内部技术发布代号是什么？"}
            ).json()
            checks["tech_answer"] = MARKER in answer["answer"]
            checks["tech_citation"] = bool(
                answer["citations"] and answer["citations"][0]["document_id"] == doc
            )
            checks["tech_reached_reranker"] = reranker.marker_seen
            checks["tech_reached_llm"] = llm.marker_seen
            reranker.marker_seen = llm.marker_seen = False
            sales = login(client, tenant, "sales")
            search = request(
                client, sales, "POST", "/search", {"kb_id": kb, "query": "内部技术发布代号是什么？"}
            )
            denied = request(
                client, sales, "POST", "/chat", {"kb_id": kb, "query": "内部技术发布代号是什么？"}
            )
            checks["sales_response_no_marker"] = MARKER not in search.text and MARKER not in denied.text
            checks["sales_no_citation"] = all(x.get("document_id") != doc for x in denied.json()["citations"])
            checks["sales_not_sent_to_reranker"] = not reranker.marker_seen
            checks["sales_not_sent_to_llm"] = not llm.marker_seen
        finally:
            app.dependency_overrides.pop(get_chat_service, None)
    result = {
        "checks": checks,
        "passed": sum(checks.values()),
        "failed": sum(not x for x in checks.values()),
        "skipped": 0,
    }
    print(json.dumps(result, ensure_ascii=False))
    return all(checks.values())


if __name__ == "__main__":
    try:
        success = main()
    except Exception as exc:
        print(
            json.dumps(
                {
                    "passed": 0,
                    "failed": 1,
                    "skipped": 0,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:200],
                },
                ensure_ascii=False,
            )
        )
        success = False
    raise SystemExit(0 if success else 1)
