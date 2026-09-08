"""按当前用户实际授权范围汇总运营指标。"""

from datetime import timedelta
import json
import math

from sqlalchemy import func, or_, select

from app.db.models import (
    Conversation, DocumentRecord, FAQCluster, FAQEntry, KnowledgeBase, KnowledgeBaseMember,
    KnowledgeGap, MessageCitation, MetricEvent, QuestionAudit, now,
)
from app.db.session import session_factory
from app.services.access_control import can_read_document, fresh_user, kb_access


def percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    index = max(0, math.ceil(ratio * len(values)) - 1)
    return round(values[index], 2)


class DashboardService:
    def summary(self, user, days: int, kb_id: str | None = None):
        user = fresh_user(user, "dashboard:view")
        since = now() - timedelta(days=days)
        with session_factory()() as db:
            memberships = list(db.scalars(select(KnowledgeBaseMember).where(KnowledgeBaseMember.user_id == user.id)))
            allowed_kbs = {x.kb_id for x in memberships}
            if "user:manage" in user.permissions:
                allowed_kbs = set(db.scalars(select(KnowledgeBase.id).where(
                    KnowledgeBase.tenant_id == user.tenant_id, KnowledgeBase.is_deleted.is_(False)
                )))
            if kb_id:
                kb_access(db, user, kb_id)
                allowed_kbs &= {kb_id}
            kbs = list(db.scalars(select(KnowledgeBase).where(
                KnowledgeBase.tenant_id == user.tenant_id,
                KnowledgeBase.id.in_(allowed_kbs), KnowledgeBase.is_deleted.is_(False)))) if allowed_kbs else []
            docs = list(db.scalars(select(DocumentRecord).where(
                DocumentRecord.tenant_id == user.tenant_id, DocumentRecord.kb_id.in_(allowed_kbs)))) if allowed_kbs else []
            readable = [doc for doc in docs if can_read_document(db, user, doc)]
            readable_ids = {doc.id for doc in readable}
            event_scope = MetricEvent.kb_id.in_(allowed_kbs)
            if kb_id is None:
                event_scope = or_(event_scope, MetricEvent.kb_id.is_(None))
            events = list(db.scalars(select(MetricEvent).where(
                MetricEvent.tenant_id == user.tenant_id,
                event_scope if allowed_kbs else MetricEvent.kb_id.is_(None),
                MetricEvent.created_at >= since,
            )))
            faq_entries = list(db.scalars(select(FAQEntry).where(
                FAQEntry.tenant_id == user.tenant_id, FAQEntry.kb_id.in_(allowed_kbs), FAQEntry.enabled.is_(True)
            ))) if allowed_kbs else []
            # FAQ 只有其来源引用全部仍可读才进入统计，防止排名泄漏标题或答案。
            visible_faq = []
            for faq in faq_entries:
                citation_docs = set(db.scalars(
                    select(MessageCitation.document_id).where(MessageCitation.message_id == faq.source_message_id)
                ))
                if citation_docs and citation_docs <= readable_ids:
                    visible_faq.append(faq)
            citation_counts = dict(db.execute(
                select(MessageCitation.document_id, func.count(MessageCitation.id))
                .where(MessageCitation.document_id.in_(readable_ids), MessageCitation.used.is_(True))
                .group_by(MessageCitation.document_id)
            ).all()) if readable_ids else {}
            manager = "gap:manage" in user.permissions
            gaps = list(db.scalars(select(KnowledgeGap).where(
                KnowledgeGap.tenant_id == user.tenant_id, KnowledgeGap.kb_id.in_(allowed_kbs)
            ).order_by(KnowledgeGap.occurrence_count.desc()).limit(10))) if allowed_kbs and manager else []
            audits = list(db.scalars(select(QuestionAudit).where(
                QuestionAudit.tenant_id == user.tenant_id,
                QuestionAudit.kb_id.in_(allowed_kbs), QuestionAudit.created_at >= since,
            ))) if allowed_kbs else []
            clusters = list(db.scalars(select(FAQCluster).where(
                FAQCluster.tenant_id == user.tenant_id, FAQCluster.kb_id.in_(allowed_kbs)
            ).order_by(FAQCluster.occurrence_count.desc()).limit(10))) if allowed_kbs and manager else []

        durations = [x.duration_ms for x in events if x.event_type == "response_total" and x.duration_ms is not None]
        event_durations = lambda kind: [x.duration_ms for x in events if x.event_type == kind and x.duration_ms is not None]
        average = lambda values: round(sum(values) / len(values), 2) if values else 0.0
        uv = {x.user_id for x in events if x.event_type == "page_view" and x.user_id}
        question_uv = {x.user_id for x in audits}
        by_day = {}
        for event in events:
            key = event.created_at.date().isoformat()
            item = by_day.setdefault(key, {"date": key, "pv": 0, "questions": 0, "llm_calls": 0,
                                                   "tokens": 0, "average_response_ms": 0.0})
            item["pv"] += event.event_type == "page_view"
            item["llm_calls"] += event.event_type == "llm_total"
            item["tokens"] += event.prompt_tokens + event.completion_tokens
        response_groups: dict[str, list[float]] = {}
        for audit in audits:
            key = audit.created_at.date().isoformat()
            item = by_day.setdefault(key, {"date": key, "pv": 0, "questions": 0, "llm_calls": 0,
                                                   "tokens": 0, "average_response_ms": 0.0})
            item["questions"] += 1
            response_groups.setdefault(key, []).append(audit.response_ms)
        for key, values in response_groups.items():
            by_day[key]["average_response_ms"] = round(sum(values) / len(values), 2)
        faq_hits = sum(x.event_type == "faq_hit" for x in events)
        covered = sum(bool(json.loads(x.allowed_document_ids_json)) for x in audits)
        top_questions = (
            [{"id": x.id, "question": x.normalized_question, "count": x.occurrence_count} for x in clusters]
            if manager else
            [{"id": x.id, "question": x.question, "count": x.hit_count} for x in
             sorted(visible_faq, key=lambda x: x.hit_count, reverse=True)[:10]]
        )
        return {
            "days": days,
            "pv": sum(x.event_type == "page_view" for x in events), "uv": len(uv),
            "question_uv": len(question_uv),
            "knowledge_base_count": len(kbs), "document_count": len(readable),
            "chunk_count": sum(x.chunk_count for x in readable),
            "knowledge_unit_count": sum(x.chunk_count for x in readable), "faq_count": len(visible_faq),
            "question_count": len(audits),
            "daily_question_count": sum(x.created_at.date() >= (now() - timedelta(days=1)).date() for x in audits),
            "weekly_question_count": sum(x.created_at.date() >= (now() - timedelta(days=7)).date() for x in audits),
            "faq_hit_rate": round(faq_hits / len(audits), 4) if audits else 0.0,
            "knowledge_coverage_rate": round(covered / len(audits), 4) if audits else 0.0,
            "llm_call_count": sum(x.event_type == "llm_total" for x in events),
            "token_total": sum(x.prompt_tokens + x.completion_tokens for x in events),
            "average_response_ms": round(sum(durations) / len(durations), 2) if durations else 0.0,
            "p50_response_ms": percentile(durations, .5), "p95_response_ms": percentile(durations, .95),
            "average_retrieval_ms": average(event_durations("retrieval")),
            "average_rerank_ms": average(event_durations("rerank")),
            "average_first_token_ms": average(event_durations("llm_first_token")),
            "average_llm_total_ms": average(event_durations("llm_total")),
            "permission_denied_count": sum(x.event_type == "permission_denied" for x in events),
            "top_faq": [{"id": x.id, "question": x.question, "hits": x.hit_count}
                        for x in sorted(visible_faq, key=lambda x: x.hit_count, reverse=True)[:10]],
            "top_questions": top_questions,
            "popular_documents": [{"document_id": doc.id, "title": doc.title, "hits": citation_counts.get(doc.id, 0)}
                                  for doc in sorted(readable, key=lambda x: citation_counts.get(x.id, 0), reverse=True)[:10]],
            "top_gaps": [{"id": x.id, "question": x.normalized_question, "count": x.occurrence_count,
                          "type": x.gap_type} for x in gaps],
            "trend": [by_day[key] for key in sorted(by_day)],
        }
