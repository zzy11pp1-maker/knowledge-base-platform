"""FAQ 自动沉淀与安全缓存；任何命中都重新核验来源文档 ACL。"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from threading import RLock

from fastapi import HTTPException
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import (
    Conversation,
    FAQCandidate,
    FAQCluster,
    FAQEntry,
    Message,
    MessageCitation,
    DocumentRecord,
    now,
)
from app.db.session import session_factory
from app.services.access_control import can_read_document, fresh_user, kb_access


def normalize_question(value: str) -> str:
    """做可解释的轻量标准化，避免删除有语义的中文词。"""

    value = unicodedata.normalize("NFKC", value).strip().lower()
    value = re.sub(r"\s+", " ", value)
    return re.sub(r"[?？!！。,.，;；:：]+$", "", value).strip()


def cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm = math.sqrt(sum(a * a for a in left) * sum(b * b for b in right))
    return dot / norm if norm else 0.0


class FAQService:
    _cache: dict[tuple[str, str, str], list[dict]] = {}
    _cache_lock = RLock()

    def __init__(self, embedder):
        self.embedder = embedder

    def mine(self, user, kb_id: str, *, automatic: bool = False) -> dict:
        # 自动观察只允许处理调用者自己的可访问 KB；人工批量挖掘要求管理员权限。
        user = fresh_user(user, "knowledge:read" if automatic else "faq:manage")
        with session_factory()() as db:
            kb_access(db, user, kb_id, "viewer" if automatic else "owner")
            rows = list(
                db.execute(
                    select(Message, Conversation)
                    .join(Conversation, Conversation.id == Message.conversation_id)
                    .where(
                        Conversation.tenant_id == user.tenant_id,
                        Conversation.kb_id == kb_id,
                        Message.role == "user",
                        Message.status == "complete",
                    )
                    .order_by(Message.created_at, Message.id)
                )
            )
            existing_ids = {
                item
                for cluster in db.scalars(
                    select(FAQCluster).where(
                        FAQCluster.tenant_id == user.tenant_id, FAQCluster.kb_id == kb_id
                    )
                )
                for item in json.loads(cluster.message_ids_json)
            }
            pending = [(message, conv) for message, conv in rows if message.id not in existing_ids]
        if not pending:
            return {"processed": 0, "clusters": 0, "candidates": 0}

        vectors = self.embedder.embed([message.content for message, _ in pending]).dense
        created_clusters = created_candidates = 0
        with session_factory()() as db:
            clusters = list(
                db.scalars(
                    select(FAQCluster).where(
                        FAQCluster.tenant_id == user.tenant_id, FAQCluster.kb_id == kb_id
                    )
                )
            )
            for (message, _), vector in zip(pending, vectors, strict=True):
                normalized = normalize_question(message.content)
                best = None
                best_score = -1.0
                for cluster in clusters:
                    score = 1.0 if cluster.normalized_question == normalized else cosine(
                        vector, json.loads(cluster.embedding_json)
                    )
                    if score > best_score:
                        best, best_score = cluster, score
                if best is None or best_score < get_settings().faq_similarity_threshold:
                    best = FAQCluster(
                        tenant_id=user.tenant_id,
                        kb_id=kb_id,
                        normalized_question=normalized,
                        question_samples_json="[]",
                        message_ids_json="[]",
                        embedding_json=json.dumps(vector),
                        occurrence_count=0,
                        first_seen_at=message.created_at,
                    )
                    db.add(best)
                    db.flush()
                    clusters.append(best)
                    created_clusters += 1
                    best_score = 1.0
                samples = json.loads(best.question_samples_json)
                ids = json.loads(best.message_ids_json)
                if message.id not in ids:
                    ids.append(message.id)
                    if message.content not in samples and len(samples) < 10:
                        samples.append(message.content)
                    old_count = best.occurrence_count
                    best.occurrence_count += 1
                    best.last_seen_at = message.created_at
                    best.question_samples_json = json.dumps(samples, ensure_ascii=False)
                    best.message_ids_json = json.dumps(ids)
                    # 以运行均值更新簇中心，避免首个问题永久支配语义。
                    old = json.loads(best.embedding_json)
                    best.embedding_json = json.dumps(
                        [(a * old_count + b) / best.occurrence_count for a, b in zip(old, vector, strict=True)]
                    )
                if best.occurrence_count >= get_settings().faq_min_occurrences:
                    candidate = db.scalar(select(FAQCandidate).where(FAQCandidate.cluster_id == best.id))
                    if candidate is None:
                        answer = db.scalar(
                            select(Message)
                            .where(
                                Message.conversation_id == message.conversation_id,
                                Message.turn_id == message.turn_id,
                                Message.role == "assistant",
                                Message.status == "complete",
                            )
                            .order_by(Message.created_at.desc())
                        )
                        quality = bool(
                            answer
                            and db.scalar(
                                select(MessageCitation.id).where(
                                    MessageCitation.message_id == answer.id, MessageCitation.used.is_(True)
                                )
                            )
                        )
                        candidate = FAQCandidate(
                            cluster_id=best.id,
                            tenant_id=user.tenant_id,
                            kb_id=kb_id,
                            standard_question=best.normalized_question,
                            proposed_answer=answer.content if quality else None,
                            source_message_id=answer.id if quality else None,
                            has_quality_answer=quality,
                            occurrence_count=best.occurrence_count,
                            confidence_score=max(0.0, min(1.0, best_score)),
                        )
                        db.add(candidate)
                        created_candidates += 1
                    else:
                        candidate.occurrence_count = best.occurrence_count
                        candidate.confidence_score = max(
                            candidate.confidence_score, max(0.0, min(1.0, best_score))
                        )
            db.commit()
        return {"processed": len(pending), "clusters": created_clusters, "candidates": created_candidates}

    def review(
        self,
        user,
        candidate_id: str,
        status: str,
        answer: str | None = None,
        question: str | None = None,
    ):
        user = fresh_user(user, "faq:manage")
        if status not in ("approved", "rejected"):
            raise HTTPException(422, "审核状态只能是 approved 或 rejected")
        with session_factory()() as db:
            row = db.get(FAQCandidate, candidate_id)
            if row is None or row.tenant_id != user.tenant_id:
                raise HTTPException(404, "FAQ 候选不存在")
            kb_access(db, user, row.kb_id, "owner")
            if row.source_message_id and not self.safe_message_citations(db, user, row.source_message_id):
                raise HTTPException(404, "FAQ 候选不存在或来源不可访问")
            if row.status != "pending":
                raise HTTPException(409, "FAQ 候选已审核")
            if question is not None:
                row.standard_question = question.strip()
                cluster = db.get(FAQCluster, row.cluster_id)
                cluster.embedding_json = json.dumps(
                    self.embedder.embed([row.standard_question]).dense[0]
                )
            if answer is not None:
                row.proposed_answer = answer.strip()
            if status == "approved" and (not row.has_quality_answer or not row.source_message_id):
                raise HTTPException(409, "没有带有效引用的历史答案，不能批准为缓存 FAQ")
            row.status, row.reviewed_by, row.reviewed_at = status, user.id, now()
            db.commit()
            db.refresh(row)
            return row

    def publish(self, user, candidate_id: str):
        user = fresh_user(user, "faq:manage")
        with session_factory()() as db:
            row = db.get(FAQCandidate, candidate_id)
            if row is None or row.tenant_id != user.tenant_id:
                raise HTTPException(404, "FAQ 候选不存在")
            kb_access(db, user, row.kb_id, "owner")
            if row.status != "approved" or not row.proposed_answer or not row.source_message_id:
                raise HTTPException(409, "FAQ 尚未批准或缺少可追溯答案")
            citations = self.safe_message_citations(db, user, row.source_message_id)
            if not citations:
                raise HTTPException(404, "FAQ 来源引用已失效或不可访问")
            entry = db.scalar(select(FAQEntry).where(FAQEntry.candidate_id == row.id))
            if entry is None:
                cluster = db.get(FAQCluster, row.cluster_id)
                entry = FAQEntry(
                    candidate_id=row.id,
                    tenant_id=row.tenant_id,
                    kb_id=row.kb_id,
                    question=row.standard_question,
                    answer=row.proposed_answer,
                    embedding_json=cluster.embedding_json,
                    source_message_id=row.source_message_id,
                )
                db.add(entry)
            row.status = "published"
            db.commit()
            db.refresh(entry)
            self.invalidate(row.tenant_id, row.kb_id)
            return entry

    @classmethod
    def invalidate(cls, tenant_id: str | None = None, kb_id: str | None = None) -> None:
        with cls._cache_lock:
            if tenant_id is None:
                cls._cache.clear()
                return
            for key in list(cls._cache):
                if key[1] == tenant_id and (kb_id is None or key[2] == kb_id):
                    cls._cache.pop(key, None)

    def update_entry(self, user, entry_id: str, enabled: bool):
        user = fresh_user(user, "faq:manage")
        with session_factory()() as db:
            row = db.get(FAQEntry, entry_id)
            if row is None or row.tenant_id != user.tenant_id:
                raise HTTPException(404, "FAQ 不存在")
            kb_access(db, user, row.kb_id, "owner")
            row.enabled = enabled
            db.commit()
            db.refresh(row)
            self.invalidate(row.tenant_id, row.kb_id)
            return row

    def cached_entries(self, tenant_id: str, kb_id: str) -> list[dict]:
        key = (get_settings().database_url, tenant_id, kb_id)
        with self._cache_lock:
            cached = self._cache.get(key)
        if cached is not None:
            return cached
        with session_factory()() as db:
            entries = list(db.scalars(select(FAQEntry).where(
                FAQEntry.tenant_id == tenant_id, FAQEntry.kb_id == kb_id, FAQEntry.enabled.is_(True)
            )))
            loaded = [
                {
                    "id": row.id,
                    "normalized_question": normalize_question(row.question),
                    "embedding": json.loads(row.embedding_json),
                }
                for row in entries
            ]
        with self._cache_lock:
            return self._cache.setdefault(key, loaded)

    def safe_citations(self, db, user, entry: FAQEntry):
        return self.safe_message_citations(db, user, entry.source_message_id)

    def safe_message_citations(self, db, user, message_id: str):
        """返回完整可读的引用集合；任一来源撤权时整个缓存答案失效。"""

        citations = list(db.scalars(select(MessageCitation).where(
            MessageCitation.message_id == message_id, MessageCitation.used.is_(True)
        ).order_by(MessageCitation.label)))
        if not citations:
            return []
        for citation in citations:
            doc = db.get(DocumentRecord, citation.document_id)
            if doc is None or doc.version != citation.document_version or not can_read_document(db, user, doc):
                return []
        return citations

    def match(self, user, kb_id: str, question: str):
        user = fresh_user(user)
        with session_factory()() as db:
            kb_access(db, user, kb_id)
        entries = self.cached_entries(user.tenant_id, kb_id)
        if not entries:
            return None
        normalized = normalize_question(question)
        exact = next((item for item in entries if item["normalized_question"] == normalized), None)
        if exact is not None:
            ranked = [exact]
        else:
            vector = self.embedder.embed([question]).dense[0]
            ranked = sorted(entries, key=lambda x: cosine(vector, x["embedding"]), reverse=True)
            if cosine(vector, ranked[0]["embedding"]) < get_settings().faq_similarity_threshold:
                return None
        with session_factory()() as db:
            entry = db.get(FAQEntry, ranked[0]["id"])
            if entry is None or not entry.enabled:
                self.invalidate(user.tenant_id, kb_id)
                return None
            citations = self.safe_citations(db, user, entry)
            if not citations:
                return None
            entry.hit_count += 1
            db.commit()
            return entry.answer, citations, entry.id
