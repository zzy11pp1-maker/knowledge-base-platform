"""FAQ、知识缺口、运营看板和受保护 Citation 接口。"""

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from app.core.auth import Principal, current_user, require
from app.core.business_dependencies import get_knowledge_service
from app.db.models import FAQCandidate, FAQCluster, FAQEntry, KnowledgeGap, KnowledgeTask, Message, MessageCitation, DocumentRecord, QuestionAudit
from app.db.session import session_factory
from app.schemas.business import FAQCandidateOutput, FAQEntryOutput, FAQEntryPatch, FAQReviewInput, KnowledgeGapOutput, ModelConfigPatch, PageViewInput, QuestionAuditOutput
from app.services.access_control import can_read_document, fresh_user, kb_access
from app.services.dashboard import DashboardService
from app.services.faq import FAQService
from app.services.gaps import GapService
from app.services.metrics import record_metric

router = APIRouter()


def candidate_output(db, row: FAQCandidate) -> dict:
    source_ids = []
    if row.source_message_id:
        source_ids = list(dict.fromkeys(db.scalars(select(MessageCitation.document_id).where(
            MessageCitation.message_id == row.source_message_id, MessageCitation.used.is_(True)
        ))))
    cluster = db.get(FAQCluster, row.cluster_id)
    return {field: getattr(row, field) for field in FAQCandidateOutput.model_fields if hasattr(row, field)} | {
        "question_samples": json.loads(cluster.question_samples_json) if cluster else [],
        "source_document_ids": source_ids,
    }


def faq_service(knowledge=Depends(get_knowledge_service)):
    return FAQService(knowledge.embedder)


@router.post("/knowledge-bases/{kb_id}/faq/mine")
def mine_faq(kb_id: str, user: Principal = Depends(require("faq:manage")), service=Depends(faq_service)):
    return service.mine(user, kb_id)


@router.get("/knowledge-bases/{kb_id}/faq/candidates", response_model=list[FAQCandidateOutput])
def faq_candidates(kb_id: str, user: Principal = Depends(require("faq:manage")),
                   service=Depends(faq_service)):
    with session_factory()() as db:
        kb_access(db, user, kb_id, "owner")
        rows = list(db.scalars(select(FAQCandidate).where(
            FAQCandidate.tenant_id == user.tenant_id, FAQCandidate.kb_id == kb_id
        ).order_by(FAQCandidate.occurrence_count.desc())))
        return [candidate_output(db, row) for row in rows if not row.source_message_id
                or service.safe_message_citations(db, user, row.source_message_id)]


@router.patch("/faq/candidates/{candidate_id}", response_model=FAQCandidateOutput)
def review_faq(candidate_id: str, body: FAQReviewInput, user: Principal = Depends(require("faq:manage")), service=Depends(faq_service)):
    row = service.review(user, candidate_id, body.status, body.answer, body.question)
    with session_factory()() as db:
        return candidate_output(db, row)


@router.post("/faq/candidates/{candidate_id}/publish", response_model=FAQEntryOutput)
def publish_faq(candidate_id: str, user: Principal = Depends(require("faq:manage")), service=Depends(faq_service)):
    return service.publish(user, candidate_id)


@router.get("/knowledge-bases/{kb_id}/faqs", response_model=list[FAQEntryOutput])
def faqs(kb_id: str, user: Principal = Depends(require("knowledge:read")), service=Depends(faq_service)):
    with session_factory()() as db:
        kb_access(db, user, kb_id)
        entries = list(db.scalars(select(FAQEntry).where(
            FAQEntry.tenant_id == user.tenant_id, FAQEntry.kb_id == kb_id, FAQEntry.enabled.is_(True)
        )))
        return [entry for entry in entries if service.safe_citations(db, user, entry)]


@router.patch("/faqs/{entry_id}", response_model=FAQEntryOutput)
def update_faq(entry_id: str, body: FAQEntryPatch, user: Principal = Depends(require("faq:manage")),
               service=Depends(faq_service)):
    return service.update_entry(user, entry_id, body.enabled)


@router.get("/knowledge-bases/{kb_id}/gaps", response_model=list[KnowledgeGapOutput])
def gaps(kb_id: str, status: str | None = Query(None), user: Principal = Depends(require("gap:manage"))):
    return GapService().list(user, kb_id, status)


@router.get("/knowledge-bases/{kb_id}/gaps/trend")
def gap_trend(kb_id: str, days: int = Query(7, ge=1, le=30),
              user: Principal = Depends(require("gap:manage"))):
    from datetime import timedelta
    from app.db.models import KnowledgeGap, now
    with session_factory()() as db:
        kb_access(db, user, kb_id, "owner")
        rows = list(db.scalars(select(KnowledgeGap).where(
            KnowledgeGap.tenant_id == user.tenant_id, KnowledgeGap.kb_id == kb_id,
            KnowledgeGap.last_seen_at >= now() - timedelta(days=days))))
        daily = {}
        for row in rows:
            key = row.last_seen_at.date().isoformat()
            daily[key] = daily.get(key, 0) + row.occurrence_count
        return [{"date": key, "occurrences": daily[key]} for key in sorted(daily)]


@router.post("/gaps/{gap_id}/resolve", response_model=KnowledgeGapOutput)
def resolve_gap(gap_id: str, user: Principal = Depends(require("gap:manage"))):
    return GapService().resolve(user, gap_id)


@router.post("/gaps/{gap_id}/knowledge-task", status_code=201)
def gap_to_knowledge_task(gap_id: str, user: Principal = Depends(require("gap:manage"))):
    """从缺口一键建立待补充知识任务，保留来源缺口和知识库范围。"""

    with session_factory()() as db:
        gap = db.get(KnowledgeGap, gap_id)
        if gap is None or gap.tenant_id != user.tenant_id:
            raise HTTPException(404, "知识缺口不存在")
        kb_access(db, user, gap.kb_id, "owner")
        existing = db.scalar(select(KnowledgeTask).where(
            KnowledgeTask.gap_id == gap.id, KnowledgeTask.status == "pending"
        ))
        if existing:
            return existing
        row = KnowledgeTask(tenant_id=user.tenant_id, kb_id=gap.kb_id, gap_id=gap.id,
                            title=f"补充知识：{gap.normalized_question}", created_by=user.id)
        db.add(row); db.commit(); db.refresh(row)
        return row


@router.post("/gaps/{gap_id}/faq-candidate", response_model=FAQCandidateOutput, status_code=201)
def gap_to_faq(gap_id: str, user: Principal = Depends(require("gap:manage")), service=Depends(faq_service)):
    """把真实缺口转成待审核候选；没有可追溯答案前不能批准或发布。"""

    user = fresh_user(user, "faq:manage")
    with session_factory()() as db:
        gap = db.get(KnowledgeGap, gap_id)
        if gap is None or gap.tenant_id != user.tenant_id:
            raise HTTPException(404, "知识缺口不存在")
        kb_access(db, user, gap.kb_id, "owner")
        vector = service.embedder.embed([gap.normalized_question]).dense[0]
        cluster = FAQCluster(tenant_id=user.tenant_id, kb_id=gap.kb_id,
                             normalized_question=gap.normalized_question,
                             question_samples_json=gap.original_samples_json, message_ids_json="[]",
                             embedding_json=json.dumps(vector), occurrence_count=gap.occurrence_count,
                             first_seen_at=gap.first_seen_at, last_seen_at=gap.last_seen_at)
        db.add(cluster); db.flush()
        candidate = FAQCandidate(cluster_id=cluster.id, tenant_id=user.tenant_id, kb_id=gap.kb_id,
                                 standard_question=gap.normalized_question,
                                 occurrence_count=gap.occurrence_count, has_quality_answer=False)
        db.add(candidate); db.commit(); db.refresh(candidate)
        return candidate_output(db, candidate)


@router.get("/knowledge-bases/{kb_id}/question-audits", response_model=list[QuestionAuditOutput])
def question_audits(kb_id: str, user: Principal = Depends(require("audit:view"))):
    """按老师规定格式输出单次问答的召回、放行、拦截、Token 与耗时。"""

    with session_factory()() as db:
        kb_access(db, user, kb_id, "owner")
        rows = list(db.scalars(select(QuestionAudit).where(
            QuestionAudit.tenant_id == user.tenant_id, QuestionAudit.kb_id == kb_id
        ).order_by(QuestionAudit.created_at.desc())))
        return [{
            "id": row.id, "kb_id": row.kb_id, "conversation_id": row.conversation_id,
            "message_id": row.message_id, "user_id": row.user_id, "question_text": row.question_text,
            "recalled_document_ids": json.loads(row.recalled_document_ids_json),
            "allowed_document_ids": json.loads(row.allowed_document_ids_json),
            "denied_document_ids": json.loads(row.denied_document_ids_json),
            "prompt_tokens": row.prompt_tokens, "completion_tokens": row.completion_tokens,
            "response_ms": row.response_ms, "created_at": row.created_at,
        } for row in rows]


@router.post("/metrics/page-view", status_code=204)
def page_view(body: PageViewInput, user: Principal = Depends(current_user)):
    """记录真实登录访问；调用者只能以 JWT 中自己的身份产生事件。"""

    if body.kb_id:
        with session_factory()() as db:
            kb_access(db, user, body.kb_id)
    record_metric("page_view", tenant_id=user.tenant_id, kb_id=body.kb_id, user_id=user.id, metadata={"route": body.route})


@router.get("/dashboard/summary")
def dashboard(days: int = Query(7, ge=1, le=30), kb_id: str | None = None,
              user: Principal = Depends(require("dashboard:view"))):
    return DashboardService().summary(user, days, kb_id)


@router.get("/citations/{message_id}/{chunk_id}")
def citation_content(message_id: str, chunk_id: str, user: Principal = Depends(require("chat:use")),
                     knowledge=Depends(get_knowledge_service)):
    fresh_user(user, "knowledge:read")
    with session_factory()() as db:
        message = db.get(Message, message_id)
        citation = db.scalar(select(MessageCitation).where(
            MessageCitation.message_id == message_id, MessageCitation.chunk_id == chunk_id,
            MessageCitation.used.is_(True)))
        if message is None or citation is None:
            raise HTTPException(404, "引用不存在或不可访问")
        from app.db.models import Conversation
        conv = db.get(Conversation, message.conversation_id)
        if conv is None or conv.tenant_id != user.tenant_id or conv.user_id != user.id:
            raise HTTPException(404, "引用不存在或不可访问")
        doc = db.get(DocumentRecord, citation.document_id)
        if doc is None or doc.version != citation.document_version or not can_read_document(db, user, doc):
            raise HTTPException(404, "引用不存在或不可访问")
        payload = knowledge.repository.get_chunk(user.tenant_id, doc.kb_id, doc.id, chunk_id)
        if payload is None or payload.document_version != doc.version:
            raise HTTPException(404, "引用内容已失效")
        return {"document_id": doc.id, "title": doc.title, "source": doc.source,
                "chunk_id": chunk_id, "chunk_index": payload.chunk_index, "content": payload.content}


@router.get("/system/model-config")
def get_model_config(user: Principal = Depends(require("user:manage"))):
    from app.services.model_config import public_config
    return public_config()


@router.patch("/system/model-config")
def update_model_config(body: ModelConfigPatch, user: Principal = Depends(require("user:manage"))):
    """维护模型地址与型号；密钥不接受、不入库、不回显。"""

    from app.core.dependencies import close_dependencies
    from app.db.models import ModelConfiguration
    from app.services.model_config import public_config
    with session_factory()() as db:
        row = db.get(ModelConfiguration, "default")
        if row is None:
            row = ModelConfiguration(id="default")
            db.add(row)
        for key, value in body.model_dump(exclude_unset=True).items():
            setattr(row, key, value.rstrip("/") if isinstance(value, str) and key.endswith("base_url") else value)
        row.updated_by = user.id
        db.commit()
    close_dependencies()
    get_knowledge_service.cache_clear()
    return public_config()
