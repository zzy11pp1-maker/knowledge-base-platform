"""真实 HTTP 端到端：RBAC、ACL、GPU Hybrid/Rerank、LLM、SSE、Citation、历史。"""

import argparse
import json
from uuid import uuid4

import httpx

PASSWORD = "Acceptance-password-42"


class API:
    def __init__(self, base_url):
        self.client = httpx.Client(base_url=base_url, timeout=180, trust_env=False)
        self.token = None

    def call(self, method, path, *, json_body=None, files=None, data=None, expected=(200, 201, 204)):
        headers = {"Authorization": "Bearer " + self.token} if self.token else {}
        response = self.client.request(method, path, json=json_body, files=files, data=data, headers=headers)
        if response.status_code not in expected:
            detail = (
                response.json().get("message", "")
                if response.headers.get("content-type", "").startswith("application/json")
                else ""
            )
            raise AssertionError(f"{method} {path}: HTTP {response.status_code} {detail}")
        return response

    def login(self, tenant, username, password=PASSWORD):
        response = self.call(
            "POST", "/auth/login", json_body={"tenant_id": tenant, "username": username, "password": password}
        )
        self.token = response.json()["access_token"]
        return self.token


def parse_sse(response):
    events = []
    event, data = None, []
    for line in response.iter_lines():
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
        elif line == "" and event:
            events.append((event, json.loads("\n".join(data))))
            event, data = None, []
    return events


def main(base_url, tenant):
    api = API(base_url)
    checks = {}
    admin = api.login(tenant, "admin")
    checks["login_me"] = api.call("GET", "/auth/me").json()["username"] == "admin"
    tech_dept = api.call("POST", "/departments", json_body={"name": "技术部-" + uuid4().hex[:6]}).json()["id"]
    sales_dept = api.call("POST", "/departments", json_body={"name": "销售部-" + uuid4().hex[:6]}).json()[
        "id"
    ]
    role = api.call(
        "POST",
        "/roles",
        json_body={"name": "知识用户-" + uuid4().hex[:6], "permission_codes": ["knowledge:read", "chat:use"]},
    ).json()["id"]
    suffix = uuid4().hex[:6]
    tech_id = api.call(
        "POST",
        "/users",
        json_body={
            "username": "tech-" + suffix,
            "password": PASSWORD,
            "department_id": tech_dept,
            "role_ids": [role],
        },
    ).json()["id"]
    sales_id = api.call(
        "POST",
        "/users",
        json_body={
            "username": "sales-" + suffix,
            "password": PASSWORD,
            "department_id": sales_dept,
            "role_ids": [role],
        },
    ).json()["id"]
    kb = api.call("POST", "/knowledge-bases", json_body={"name": "验收知识库-" + suffix}).json()["id"]
    for user_id in (tech_id, sales_id):
        api.call("POST", f"/knowledge-bases/{kb}/members", json_body={"user_id": user_id, "role": "viewer"})
    tech_doc = api.call(
        "POST",
        "/documents/import",
        data={"kb_id": kb},
        files={
            "file": ("tech.md", "# 技术发布\n内部技术发布代号是 Apollo-77，仅限技术部。", "text/markdown")
        },
    ).json()["id"]
    sales_doc = api.call(
        "POST",
        "/documents/import",
        data={"kb_id": kb},
        files={"file": ("sales.md", "# 销售手册\n标准客户演示需要先确认业务场景。", "text/markdown")},
    ).json()["id"]
    api.call(
        "PUT",
        f"/documents/{tech_doc}/permissions",
        json_body={"entries": [{"permission_type": "department", "target_id": tech_dept}]},
    )
    api.call(
        "PUT",
        f"/documents/{sales_doc}/permissions",
        json_body={"entries": [{"permission_type": "department", "target_id": sales_dept}]},
    )
    api.token = None
    tech_token = api.login(tenant, "tech-" + suffix)
    search = api.call("POST", "/search", json_body={"kb_id": kb, "query": "技术发布代号是什么？"}).json()
    checks["hybrid_rrf_rerank"] = bool(
        search["items"]
        and search["items"][0]["dense_rank"]
        and search["items"][0]["sparse_rank"]
        and search["items"][0]["rerank_score"] is not None
    )
    checks["tech_acl"] = all(x["document_id"] == tech_doc for x in search["items"])
    answer = api.call("POST", "/chat", json_body={"kb_id": kb, "query": "技术发布代号是什么？"}).json()
    checks["llm_grounding"] = "Apollo-77" in answer["answer"]
    checks["citation_exact"] = bool(
        answer["citations"]
        and answer["citations"][0]["document_id"] == tech_doc
        and answer["citations"][0]["chunk_id"]
    )
    messages = api.call("GET", f"/conversations/{answer['conversation_id']}/messages").json()
    checks["conversation_history"] = len(messages) == 2 and messages[-1]["content"] == answer["answer"]
    api.token = tech_token
    with api.client.stream(
        "POST",
        "/chat/stream",
        json={"kb_id": kb, "query": "再确认一次技术发布代号。"},
        headers={"Authorization": "Bearer " + tech_token},
    ) as response:
        if response.status_code != 200:
            raise AssertionError("SSE HTTP " + str(response.status_code))
        events = parse_sse(response)
    kinds = [x[0] for x in events]
    checks["sse_protocol"] = (
        all(x in kinds for x in ("start", "token", "citation", "done")) and "error" not in kinds
    )
    api.token = None
    api.login(tenant, "sales-" + suffix)
    denied_search = api.call(
        "POST", "/search", json_body={"kb_id": kb, "query": "技术发布代号是什么？"}
    ).json()
    checks["sales_search_no_leak"] = all(
        x["document_id"] != tech_doc and "Apollo-77" not in json.dumps(x, ensure_ascii=False)
        for x in denied_search["items"]
    )
    denied_chat = api.call("POST", "/chat", json_body={"kb_id": kb, "query": "技术发布代号是什么？"}).json()
    checks["sales_chat_no_leak"] = "Apollo-77" not in json.dumps(denied_chat, ensure_ascii=False) and all(
        x["document_id"] != tech_doc for x in denied_chat["citations"]
    )
    api.token = admin
    health = api.call("GET", "/health/dependencies").json()
    checks["dependency_health"] = health["status"] == "ok"
    result = {
        "checks": checks,
        "passed": sum(checks.values()),
        "failed": sum(not x for x in checks.values()),
        "skipped": 0,
        "sse_event_counts": {x: kinds.count(x) for x in set(kinds)},
    }
    print(json.dumps(result, ensure_ascii=False))
    api.client.close()
    return all(checks.values())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:18129")
    parser.add_argument("--tenant", default="phase2-acceptance")
    args = parser.parse_args()
    try:
        ok = main(args.base_url, args.tenant)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "passed": 0,
                    "failed": 1,
                    "skipped": 0,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:300],
                },
                ensure_ascii=False,
            )
        )
        ok = False
    raise SystemExit(0 if ok else 1)
