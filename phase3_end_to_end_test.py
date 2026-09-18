"""完整功能 HTTP E2E：真实 GPU、Qdrant、LLM、SSE 与三类用户安全边界。"""

import argparse
import json
from time import sleep
from uuid import uuid4

from phase2_end_to_end_test import API, PASSWORD, parse_sse


def require(value, name, checks):
    checks[name] = bool(value)
    if not value:
        raise AssertionError(name)


def wait_ingestion_jobs(api, job_ids):
    """轮询服务端持久化任务状态，失败时保留明确验证原因。"""

    for _ in range(600):
        jobs = [api.call("GET", f"/ingestion-jobs/{job_id}").json() for job_id in job_ids]
        failed = [job for job in jobs if job["status"] == "failed"]
        if failed:
            raise AssertionError("document_ingestion_failed: " + "; ".join(
                f"{job['filename']}={job.get('error_message') or 'unknown'}" for job in failed
            ))
        if all(job["status"] == "ready" for job in jobs):
            return jobs
        sleep(0.5)
    raise AssertionError("document_ingestion_timeout")


def main(base_url: str, tenant: str):
    checks = {}
    api = API(base_url); admin = api.login(tenant, "admin")
    suffix = uuid4().hex[:8]
    tech_department = api.call("POST", "/departments", json_body={"name": "P3技术-" + suffix}).json()["id"]
    sales_department = api.call("POST", "/departments", json_body={"name": "P3销售-" + suffix}).json()["id"]
    role = api.call("POST", "/roles", json_body={"name": "P3用户-" + suffix,
        "permission_codes": ["knowledge:read", "chat:use", "dashboard:view"]}).json()["id"]
    tech_name, sales_name = "p3-tech-" + suffix, "p3-sales-" + suffix
    tech_id = api.call("POST", "/users", json_body={"username": tech_name, "password": PASSWORD,
        "department_id": tech_department, "role_ids": [role]}).json()["id"]
    sales_id = api.call("POST", "/users", json_body={"username": sales_name, "password": PASSWORD,
        "department_id": sales_department, "role_ids": [role]}).json()["id"]
    kb = api.call("POST", "/knowledge-bases", json_body={"name": "E2E验证库-" + suffix}).json()["id"]
    for user_id in (tech_id, sales_id):
        api.call("POST", f"/knowledge-bases/{kb}/members", json_body={"user_id": user_id, "role": "viewer"})

    batch = api.call("POST", "/documents/import/jobs/batch", data={"kb_id": kb}, files=[
        ("files", ("apollo.md", "# 技术资料\nApollo-77 是技术发布代号。", "text/markdown")),
        ("files", ("sales.txt", "销售公开流程使用 CRM-21。", "text/plain")),
    ], expected=(202,)).json()
    batch_jobs = wait_ingestion_jobs(api, [item["id"] for item in batch["items"]])
    require(len(batch_jobs) == 2 and all(x["progress"] == 100 for x in batch_jobs), "batch_import", checks)
    tech_doc, sales_doc = [x["document_id"] for x in batch_jobs]
    single_job = api.call("POST", "/documents/import/jobs", data={"kb_id": kb, "category": "制度",
        "chunk_size": "512", "chunk_overlap": "64"}, files={
        "file": ("travel.md", "# 差旅制度\n普通员工差旅住宿上限 500 元。", "text/markdown")},
        expected=(202,)).json()
    single_status = wait_ingestion_jobs(api, [single_job["id"]])[0]
    single = api.call("GET", f"/documents/{single_status['document_id']}").json()
    require(single["status"] == "ready" and single["chunk_count"] > 0, "single_import", checks)
    api.call("PATCH", f"/documents/{single['id']}", json_body={"title": "差旅报销标准", "is_enabled": True})
    require(api.call("GET", f"/documents/{single['id']}").json()["title"] == "差旅报销标准",
            "knowledge_unit_update_read", checks)
    api.call("PUT", f"/documents/{tech_doc}/permissions", json_body={"entries": [
        {"permission_type": "department", "target_id": tech_department},
        {"permission_type": "user", "target_id": api.call("GET", "/auth/me").json()["id"]}]})
    api.call("PUT", f"/documents/{sales_doc}/permissions", json_body={"entries": [{"permission_type": "global"}]})
    api.call("PUT", f"/documents/{single['id']}/permissions", json_body={"entries": [
        {"permission_type": "global"}, {"permission_type": "department", "target_id": tech_department},
        {"permission_type": "role", "target_id": role},
        {"permission_type": "user", "target_id": api.call("GET", "/auth/me").json()["id"]},
    ]})
    require(len(api.call("GET", f"/documents/{single['id']}/permissions").json()["entries"]) == 4,
            "mixed_four_dimensional_acl", checks)

    api.token = api.login(tenant, tech_name)
    conversation = api.call("POST", "/conversations", json_body={"kb_id": kb, "title": "完整功能验证"}).json()["id"]
    latest = None
    for _ in range(2):
        with api.client.stream("POST", "/chat/stream", json={"kb_id": kb, "query": "技术发布代号是什么？",
            "conversation_id": conversation}, headers={"Authorization": "Bearer " + api.token}) as response:
            if response.status_code != 200:
                raise AssertionError("SSE HTTP " + str(response.status_code))
            events = parse_sse(response)
        latest = next(data for name, data in events if name == "done")
        require("Apollo-77" in latest["answer"], "tech_sse_answer", checks)
        require(bool(latest["citations"]), "tech_citation", checks)
    citation = latest["citations"][0]
    content = api.call("GET", f"/citations/{latest['message_id']}/{citation['chunk_id']}").json()
    require("Apollo-77" in content["content"], "citation_content", checks)
    require(len(api.call("GET", f"/conversations/{conversation}/messages").json()) >= 4,
            "conversation_restore", checks)

    api.token = admin
    candidates = api.call("GET", f"/knowledge-bases/{kb}/faq/candidates").json()
    require(bool(candidates), "faq_auto_mined", checks)
    target = candidates[0]["id"]
    api.call("PATCH", f"/faq/candidates/{target}", json_body={"status": "approved"})
    api.call("POST", f"/faq/candidates/{target}/publish")
    require(bool(api.call("GET", f"/knowledge-bases/{kb}/faqs").json()), "faq_published", checks)
    api.token = api.login(tenant, tech_name)
    faq_answer = api.call("POST", "/chat", json_body={"kb_id": kb, "query": "技术发布代号是什么？"}).json()
    require(faq_answer["faq_hit"] and "Apollo-77" in faq_answer["answer"], "faq_cache_hit", checks)
    api.token = admin
    dashboard = api.call("GET", f"/dashboard/summary?days=7&kb_id={kb}").json()
    require(dashboard["document_count"] == 3 and dashboard["chunk_count"] >= 3,
            "admin_dashboard", checks)
    require(all(key in dashboard for key in ("pv", "uv", "knowledge_unit_count", "top_questions",
        "popular_documents", "token_total", "trend")), "dashboard_required_metrics", checks)
    require(bool(api.call("GET", f"/knowledge-bases/{kb}/question-audits").json()),
            "question_audit", checks)

    api.token = api.login(tenant, sales_name)
    faqs = api.call("GET", f"/knowledge-bases/{kb}/faqs").json()
    require(all("Apollo" not in x["answer"] for x in faqs), "sales_faq_acl", checks)
    sales_answer = api.call("POST", "/chat", json_body={"kb_id": kb, "query": "技术发布代号是什么？"}).json()
    require("Apollo-77" not in sales_answer["answer"] and not sales_answer["faq_hit"], "sales_chat_acl", checks)
    require(sales_answer["restricted_sources_detected"] and "权限" in sales_answer["answer"],
            "explicit_permission_notice", checks)
    sales_dashboard = api.call("GET", f"/dashboard/summary?days=7&kb_id={kb}").json()
    require(all(x["document_id"] != tech_doc for x in sales_dashboard["popular_documents"]),
            "sales_dashboard_acl", checks)
    require(api.call("GET", f"/knowledge-bases/{kb}/gaps", expected=(403,)).status_code == 403,
            "sales_gap_denied", checks)

    api.token = admin
    gaps = api.call("GET", f"/knowledge-bases/{kb}/gaps").json()
    require(isinstance(gaps, list), "admin_gap_visible", checks)
    if gaps:
        task = api.call("POST", f"/gaps/{gaps[0]['id']}/knowledge-task").json()
        require(task["gap_id"] == gaps[0]["id"], "gap_to_knowledge_task", checks)
    require(api.call("GET", "/system/model-config").status_code == 200, "model_config_protected", checks)
    result = {"checks": checks, "passed": sum(checks.values()), "failed": sum(not x for x in checks.values()),
              "skipped": 0}
    print(json.dumps(result, ensure_ascii=False))
    return all(checks.values())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--tenant", default="phase3-acceptance"); args = parser.parse_args()
    raise SystemExit(0 if main(args.base_url, args.tenant) else 1)
