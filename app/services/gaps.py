"""知识缺口聚合；租户和知识库是去重键的一部分。"""

import json

from fastapi import HTTPException
from sqlalchemy import func, select

from app.db.models import KnowledgeGap, now
from app.db.session import session_factory
from app.services.access_control import fresh_user, kb_access
from app.services.faq import normalize_question


class GapService:
    TYPES = {"empty_retrieval", "low_retrieval_score", "low_rerank_score", "evidence_refusal", "repeated_unresolved"}

    def record(
        self, tenant_id: str, kb_id: str, question: str, gap_type: str,
        suggested_action=None, *, department_id=None, highest_similarity_score=None,
        suggested_category=None,
    ):
        if gap_type not in self.TYPES:
            raise ValueError("未知知识缺口类型")
        normalized = normalize_question(question)
        with session_factory()() as db:
            row = db.scalar(
                select(KnowledgeGap).where(
                    KnowledgeGap.tenant_id == tenant_id,
                    KnowledgeGap.kb_id == kb_id,
                    KnowledgeGap.normalized_question == normalized,
                    KnowledgeGap.gap_type == gap_type,
                )
            )
            if row is None:
                row = KnowledgeGap(
                    tenant_id=tenant_id,
                    kb_id=kb_id,
                    normalized_question=normalized,
                    original_samples_json=json.dumps([question], ensure_ascii=False),
                    gap_type=gap_type,
                    suggested_action=suggested_action,
                    department_id=department_id,
                    highest_similarity_score=highest_similarity_score,
                    suggested_category=suggested_category,
                )
                db.add(row)
            else:
                samples = json.loads(row.original_samples_json)
                if question not in samples and len(samples) < 10:
                    samples.append(question)
                row.original_samples_json = json.dumps(samples, ensure_ascii=False)
                row.occurrence_count += 1
                row.last_seen_at = now()
                row.status = "open"
                row.department_id = row.department_id or department_id
                if highest_similarity_score is not None:
                    row.highest_similarity_score = max(
                        row.highest_similarity_score or highest_similarity_score, highest_similarity_score
                    )
                row.suggested_category = row.suggested_category or suggested_category
            db.flush()
            # 同一标准问题可能先后触发不同失败原因；跨类型累计到三次后，额外形成
            # repeated_unresolved 缺口，方便运营人员直接识别“反复追问仍未解决”。
            if gap_type != "repeated_unresolved":
                repeated_total = int(
                    db.scalar(
                        select(func.sum(KnowledgeGap.occurrence_count)).where(
                            KnowledgeGap.tenant_id == tenant_id,
                            KnowledgeGap.kb_id == kb_id,
                            KnowledgeGap.normalized_question == normalized,
                            KnowledgeGap.gap_type != "repeated_unresolved",
                        )
                    )
                    or 0
                )
                if repeated_total >= 3:
                    repeated = db.scalar(
                        select(KnowledgeGap).where(
                            KnowledgeGap.tenant_id == tenant_id,
                            KnowledgeGap.kb_id == kb_id,
                            KnowledgeGap.normalized_question == normalized,
                            KnowledgeGap.gap_type == "repeated_unresolved",
                        )
                    )
                    if repeated is None:
                        db.add(
                            KnowledgeGap(
                                tenant_id=tenant_id,
                                kb_id=kb_id,
                                normalized_question=normalized,
                                original_samples_json=json.dumps([question], ensure_ascii=False),
                                occurrence_count=repeated_total,
                                gap_type="repeated_unresolved",
                                suggested_action="优先补充知识或发布经过审核的 FAQ",
                                department_id=department_id,
                                highest_similarity_score=highest_similarity_score,
                                suggested_category=suggested_category,
                            )
                        )
                    else:
                        repeated.occurrence_count = repeated_total
                        repeated.last_seen_at = now()
                        repeated.status = "open"
            db.commit()
            db.refresh(row)
            return row

    def list(self, user, kb_id: str, status: str | None = None):
        user = fresh_user(user, "gap:manage")
        with session_factory()() as db:
            kb_access(db, user, kb_id, "owner")
            statement = select(KnowledgeGap).where(
                KnowledgeGap.tenant_id == user.tenant_id, KnowledgeGap.kb_id == kb_id
            )
            if status:
                statement = statement.where(KnowledgeGap.status == status)
            return list(db.scalars(statement.order_by(KnowledgeGap.occurrence_count.desc(), KnowledgeGap.last_seen_at.desc())))

    def resolve(self, user, gap_id: str):
        user = fresh_user(user, "gap:manage")
        with session_factory()() as db:
            row = db.get(KnowledgeGap, gap_id)
            if row is None or row.tenant_id != user.tenant_id:
                raise HTTPException(404, "知识缺口不存在")
            kb_access(db, user, row.kb_id, "owner")
            row.status = "resolved"
            db.commit()
            db.refresh(row)
            return row
