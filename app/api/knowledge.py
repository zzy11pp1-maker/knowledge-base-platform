"""知识库成员与文档接口；不存在未认证的旧路由旁路。"""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import delete, select, update
from starlette.concurrency import run_in_threadpool

from app.core.auth import Principal, require
from app.core.business_dependencies import get_knowledge_service
from app.core.config import get_settings
from app.core.exceptions import DocumentReadError, PlatformError
from app.db.models import (
    Department,
    DocumentPermission,
    DocumentRecord,
    IngestionJob,
    KnowledgeBase,
    KnowledgeBaseMember,
    Role,
    User,
)
from app.db.session import session_factory
from app.schemas.api import SearchHitResponse
from app.schemas.business import (
    AclInput,
    AclOutput,
    AuthorizedSearchInput,
    DocumentOutput,
    DocumentPatch,
    IngestionJobOutput,
    KnowledgeBaseCreate,
    KnowledgeBaseOutput,
    KnowledgeBasePatch,
    MemberInput,
    MemberOutput,
    MemberPatch,
)
from app.services.access_control import (
    acl_entries,
    acl_payload,
    can_read_document,
    kb_access,
    scoped,
    validate_acl,
)

router = APIRouter()
LOGGER = logging.getLogger(__name__)


def check_tenant(user, tenant_id):
    if tenant_id is not None and tenant_id != user.tenant_id:
        raise HTTPException(403, "租户参数与当前身份不一致")


def _safe_job_error(exc: Exception) -> str:
    """只保存可展示的业务错误；未知异常不写入可能包含密钥的正文。"""

    if isinstance(exc, PlatformError):
        return exc.message[:512]
    if isinstance(exc, HTTPException):
        return str(exc.detail)[:512]
    return f"导入失败（{type(exc).__name__}）"


def _run_ingestion_job(
    job_id: str,
    service,
    user: Principal,
    kb_id: str,
    filename: str,
    data: bytes,
    category: str,
    chunk_size: int | None,
    chunk_overlap: int | None,
) -> None:
    """在响应返回后执行真实解析、切分、Embedding 与向量入库。"""

    with session_factory()() as db:
        job = db.get(IngestionJob, job_id)
        if job is None:
            return
        job.status, job.progress, job.stage = "processing", 15, "解析、清洗与切分"
        db.commit()
    try:
        with session_factory()() as db:
            job = db.get(IngestionJob, job_id)
            job.progress, job.stage = 45, "Embedding 与向量索引"
            db.commit()
        document = service.import_bytes(
            user,
            kb_id,
            filename,
            data,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            category=category,
        )
        with session_factory()() as db:
            job = db.get(IngestionJob, job_id)
            job.status, job.progress, job.stage = "ready", 100, "解析与向量索引完成"
            job.document_id, job.error_message = document.id, None
            db.commit()
    except Exception as exc:
        # 不记录异常正文，避免第三方响应或配置片段进入日志。
        LOGGER.error("后台导入失败 job_id=%s type=%s", job_id, type(exc).__name__)
        with session_factory()() as db:
            job = db.get(IngestionJob, job_id)
            if job is not None:
                job.status, job.progress, job.stage = "failed", 100, "导入失败"
                job.error_message = _safe_job_error(exc)
                db.commit()


def _create_ingestion_job(db, user: Principal, kb_id: str, filename: str) -> IngestionJob:
    job = IngestionJob(
        tenant_id=user.tenant_id,
        kb_id=kb_id,
        user_id=user.id,
        filename=filename,
        status="queued",
        progress=5,
        stage="等待处理",
    )
    db.add(job)
    db.flush()
    return job


@router.post("/knowledge-bases", response_model=KnowledgeBaseOutput, status_code=201)
def create_kb(body: KnowledgeBaseCreate, user: Principal = Depends(require("knowledge:create"))):
    with session_factory()() as db:
        kb = KnowledgeBase(tenant_id=user.tenant_id, owner_id=user.id, **body.model_dump())
        db.add(kb)
        db.flush()
        db.add(KnowledgeBaseMember(kb_id=kb.id, user_id=user.id, role="owner"))
        db.commit()
        return kb


@router.get("/knowledge-bases", response_model=list[KnowledgeBaseOutput])
def list_kbs(user: Principal = Depends(require("knowledge:read"))):
    with session_factory()() as db:
        return list(
            db.scalars(
                select(KnowledgeBase)
                .join(KnowledgeBaseMember)
                .where(
                    KnowledgeBase.tenant_id == user.tenant_id,
                    KnowledgeBase.is_deleted.is_(False),
                    KnowledgeBaseMember.user_id == user.id,
                )
                .order_by(KnowledgeBase.id)
            )
        )


@router.get("/knowledge-bases/{kb_id}", response_model=KnowledgeBaseOutput)
def get_kb(kb_id: str, user: Principal = Depends(require("knowledge:read"))):
    with session_factory()() as db:
        return kb_access(db, user, kb_id)


@router.patch("/knowledge-bases/{kb_id}", response_model=KnowledgeBaseOutput)
def update_kb(kb_id: str, body: KnowledgeBasePatch, user: Principal = Depends(require("knowledge:update"))):
    with session_factory()() as db:
        kb = kb_access(db, user, kb_id, "owner")
        for key, value in body.model_dump(exclude_none=True).items():
            setattr(kb, key, value)
        db.commit()
        return kb


@router.delete("/knowledge-bases/{kb_id}", status_code=204)
def delete_kb(kb_id: str, user: Principal = Depends(require("knowledge:delete"))):
    with session_factory()() as db:
        kb = kb_access(db, user, kb_id, "owner")
        # 软删除立即关闭检索及历史访问，保留审计与恢复所需的关系记录。
        kb.is_deleted = True
        db.commit()


@router.post("/knowledge-bases/{kb_id}/members", response_model=MemberOutput, status_code=201)
def add_member(kb_id: str, body: MemberInput, user: Principal = Depends(require("knowledge:update"))):
    with session_factory()() as db:
        kb_access(db, user, kb_id, "owner")
        target = scoped(db, User, body.user_id, user.tenant_id)
        if not target.is_active:
            raise HTTPException(422, "用户已停用")
        member = KnowledgeBaseMember(kb_id=kb_id, **body.model_dump())
        db.add(member)
        db.commit()
        return {"user_id": member.user_id, "role": member.role}


@router.get("/knowledge-bases/{kb_id}/members", response_model=list[MemberOutput])
def members(kb_id: str, user: Principal = Depends(require("knowledge:read"))):
    with session_factory()() as db:
        kb_access(db, user, kb_id)
        return [
            {"user_id": x.user_id, "role": x.role}
            for x in db.scalars(select(KnowledgeBaseMember).where(KnowledgeBaseMember.kb_id == kb_id))
        ]


def editable_member(db, user, kb_id, user_id):
    kb_access(db, user, kb_id, "owner")
    row = db.get(KnowledgeBaseMember, (kb_id, user_id))
    if row is None:
        raise HTTPException(404, "成员不存在")
    if row.role == "owner":
        raise HTTPException(409, "不能删除或降级知识库所有者")
    return row


@router.patch("/knowledge-bases/{kb_id}/members/{user_id}", response_model=MemberOutput)
def update_member(
    kb_id: str, user_id: str, body: MemberPatch, user: Principal = Depends(require("knowledge:update"))
):
    with session_factory()() as db:
        row = editable_member(db, user, kb_id, user_id)
        row.role = body.role
        db.commit()
        return {"user_id": row.user_id, "role": row.role}


@router.delete("/knowledge-bases/{kb_id}/members/{user_id}", status_code=204)
def remove_member(kb_id: str, user_id: str, user: Principal = Depends(require("knowledge:update"))):
    with session_factory()() as db:
        db.delete(editable_member(db, user, kb_id, user_id))
        db.commit()


@router.post("/documents/import/jobs", response_model=IngestionJobOutput, status_code=202)
async def create_import_job(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(),
    kb_id: str = Form(min_length=1, max_length=128),
    category: str = Form("未分类", min_length=1, max_length=128),
    chunk_size: int | None = Form(None, ge=100, le=10000),
    chunk_overlap: int | None = Form(None, ge=0, le=9999),
    user: Principal = Depends(require("knowledge:create")),
    service=Depends(get_knowledge_service),
):
    """创建单篇后台导入任务，前端通过任务接口读取真实状态。"""

    with session_factory()() as db:
        kb_access(db, user, kb_id, "editor")
    try:
        data = await file.read(get_settings().max_upload_bytes + 1)
        if len(data) > get_settings().max_upload_bytes:
            raise DocumentReadError("文档超过大小限制")
        filename = file.filename or ""
    finally:
        await file.close()
    with session_factory()() as db:
        job = _create_ingestion_job(db, user, kb_id, filename)
        db.commit()
        db.refresh(job)
        output = IngestionJobOutput.model_validate(job)
    background_tasks.add_task(
        _run_ingestion_job,
        job.id,
        service,
        user,
        kb_id,
        filename,
        data,
        category,
        chunk_size,
        chunk_overlap,
    )
    return output


@router.post("/documents/import/jobs/batch", status_code=202)
async def create_batch_import_jobs(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(),
    kb_id: str = Form(min_length=1, max_length=128),
    category: str = Form("未分类", min_length=1, max_length=128),
    chunk_size: int | None = Form(None, ge=100, le=10000),
    chunk_overlap: int | None = Form(None, ge=0, le=9999),
    user: Principal = Depends(require("knowledge:create")),
    service=Depends(get_knowledge_service),
):
    """批量创建独立任务；单个文件失败不会阻断同批其他文件。"""

    settings = get_settings()
    if not files or len(files) > settings.max_batch_files:
        raise HTTPException(422, f"每批文件数量必须在 1 到 {settings.max_batch_files} 之间")
    with session_factory()() as db:
        kb_access(db, user, kb_id, "editor")
    pending: list[tuple[str, bytes]] = []
    try:
        for file in files:
            data = await file.read(settings.max_upload_bytes + 1)
            if len(data) > settings.max_upload_bytes:
                raise DocumentReadError(f"文档 {file.filename or ''} 超过大小限制")
            pending.append((file.filename or "", data))
    finally:
        for file in files:
            await file.close()
    with session_factory()() as db:
        queued = [(_create_ingestion_job(db, user, kb_id, filename), data) for filename, data in pending]
        db.commit()
        for job, _ in queued:
            db.refresh(job)
        output = [IngestionJobOutput.model_validate(job) for job, _ in queued]
    for job, data in queued:
        background_tasks.add_task(
            _run_ingestion_job,
            job.id,
            service,
            user,
            kb_id,
            job.filename,
            data,
            category,
            chunk_size,
            chunk_overlap,
        )
    return {"items": output}


@router.get("/ingestion-jobs/{job_id}", response_model=IngestionJobOutput)
def ingestion_job(job_id: str, user: Principal = Depends(require("knowledge:read"))):
    """查询本租户、本知识库的导入进度，避免跨租户枚举任务。"""

    with session_factory()() as db:
        job = scoped(db, IngestionJob, job_id, user.tenant_id)
        kb_access(db, user, job.kb_id)
        if job.user_id != user.id and "knowledge:update" not in user.permissions:
            raise HTTPException(404, "导入任务不存在或不可访问")
        return job


@router.post("/documents/import", response_model=DocumentOutput, status_code=201)
async def import_document(
    file: UploadFile = File(),
    kb_id: str = Form(min_length=1, max_length=128),
    document_id: str | None = Form(None, min_length=1, max_length=128),
    tenant_id: str | None = Form(None),
    category: str = Form("未分类", min_length=1, max_length=128),
    chunk_size: int | None = Form(None, ge=100, le=10000),
    chunk_overlap: int | None = Form(None, ge=0, le=9999),
    user: Principal = Depends(require("knowledge:create")),
    service=Depends(get_knowledge_service),
):
    check_tenant(user, tenant_id)
    with session_factory()() as db:
        kb_access(db, user, kb_id, "editor")
    try:
        data = await file.read(get_settings().max_upload_bytes + 1)
        if len(data) > get_settings().max_upload_bytes:
            raise DocumentReadError("文档超过大小限制")
        return await run_in_threadpool(
            lambda: service.import_bytes(
                user, kb_id, file.filename or "", data, document_id,
                chunk_size=chunk_size, chunk_overlap=chunk_overlap, category=category,
            )
        )
    finally:
        await file.close()


@router.post("/documents/import/batch")
async def import_documents_batch(
    files: list[UploadFile] = File(),
    kb_id: str = Form(min_length=1, max_length=128),
    category: str = Form("未分类", min_length=1, max_length=128),
    chunk_size: int | None = Form(None, ge=100, le=10000),
    chunk_overlap: int | None = Form(None, ge=0, le=9999),
    user: Principal = Depends(require("knowledge:create")),
    service=Depends(get_knowledge_service),
):
    """批量导入逐项返回结果；一个坏文件不会回滚已成功的其他文件。"""

    settings = get_settings()
    if not files or len(files) > settings.max_batch_files:
        raise HTTPException(422, f"每批文件数量必须在 1 到 {settings.max_batch_files} 之间")
    with session_factory()() as db:
        kb_access(db, user, kb_id, "editor")
    results = []
    for file in files:
        try:
            data = await file.read(settings.max_upload_bytes + 1)
            if len(data) > settings.max_upload_bytes:
                raise DocumentReadError("文档超过大小限制")
            row = await run_in_threadpool(
                lambda current=file, payload=data: service.import_bytes(
                    user, kb_id, current.filename or "", payload,
                    chunk_size=chunk_size, chunk_overlap=chunk_overlap, category=category,
                )
            )
            results.append({"filename": file.filename, "status": "ready", "document_id": row.id})
        except (DocumentReadError, PlatformError, HTTPException) as exc:
            detail = exc.message if isinstance(exc, PlatformError) else str(exc.detail)
            results.append({"filename": file.filename, "status": "failed", "error": detail})
        finally:
            await file.close()
    return {"items": results, "success_count": sum(x["status"] == "ready" for x in results),
            "failed_count": sum(x["status"] == "failed" for x in results)}


@router.post("/documents/{document_id}/reindex", response_model=DocumentOutput)
async def reindex_document(
    document_id: str,
    file: UploadFile = File(),
    chunk_size: int | None = Form(None, ge=100, le=10000),
    chunk_overlap: int | None = Form(None, ge=0, le=9999),
    user: Principal = Depends(require("knowledge:update")),
    service=Depends(get_knowledge_service),
):
    """用新文件内容原子式重建同一文档；旧向量在新版本成功前仍保留。"""

    with session_factory()() as db:
        doc = scoped(db, DocumentRecord, document_id, user.tenant_id)
        kb_access(db, user, doc.kb_id, "editor")
        kb_id = doc.kb_id
    try:
        data = await file.read(get_settings().max_upload_bytes + 1)
        if len(data) > get_settings().max_upload_bytes:
            raise DocumentReadError("文档超过大小限制")
        return await run_in_threadpool(
            lambda: service.import_bytes(
                user, kb_id, file.filename or "", data, document_id, "reindex",
                chunk_size=chunk_size, chunk_overlap=chunk_overlap, category=doc.category,
            )
        )
    finally:
        await file.close()


@router.get("/documents", response_model=list[DocumentOutput])
def list_documents(
    kb_id: str,
    tenant_id: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    user: Principal = Depends(require("knowledge:read")),
):
    check_tenant(user, tenant_id)
    with session_factory()() as db:
        kb_access(db, user, kb_id)
        rows = db.scalars(
            select(DocumentRecord)
            .where(DocumentRecord.tenant_id == user.tenant_id, DocumentRecord.kb_id == kb_id)
            .order_by(DocumentRecord.id)
        )
        manager = "knowledge:update" in user.permissions
        if manager:
            try:
                kb_access(db, user, kb_id, "editor")
            except HTTPException:
                manager = False
        visible = list(rows) if manager else [x for x in rows if can_read_document(db, user, x)]
        return [_document_output(db, x) for x in visible[offset : offset + limit]]


@router.get("/documents/{document_id}", response_model=DocumentOutput)
def document(document_id: str, user: Principal = Depends(require("knowledge:read"))):
    with session_factory()() as db:
        doc = scoped(db, DocumentRecord, document_id, user.tenant_id)
        readable = can_read_document(db, user, doc)
        if not readable:
            try:
                kb_access(db, user, doc.kb_id, "editor")
                readable = "knowledge:update" in user.permissions
            except HTTPException:
                readable = False
        if not readable:
            raise HTTPException(404, "文档不存在或不可访问")
        return _document_output(db, doc)


def _document_output(db, doc: DocumentRecord):
    labels = []
    for entry in acl_entries(db, doc.id):
        label = "全部成员" if entry.permission_type == "global" else f"{entry.permission_type}:{entry.target_id}"
        labels.append(label)
    return {column.name: getattr(doc, column.name) for column in DocumentRecord.__table__.columns
            if column.name in DocumentOutput.model_fields} | {"permission_labels": labels}


@router.patch("/documents/{document_id}", response_model=DocumentOutput)
def update_document(
    document_id: str,
    body: DocumentPatch,
    user: Principal = Depends(require("knowledge:update")),
    service=Depends(get_knowledge_service),
):
    """更新知识单元台账；启停立即影响 SQL 二次鉴权和后续检索。"""

    with session_factory()() as db:
        doc = scoped(db, DocumentRecord, document_id, user.tenant_id)
        kb_access(db, user, doc.kb_id, "editor")
        if doc.status in ("processing", "reindex", "acl_syncing"):
            raise HTTPException(409, "文档正在处理")
        old = {key: getattr(doc, key) for key in body.model_dump(exclude_none=True)}
        for key, value in body.model_dump(exclude_none=True).items():
            setattr(doc, key, value)
        try:
            service.repository.set_document_metadata(
                user.tenant_id, doc.kb_id, doc.id,
                {"title": doc.title, "category": doc.category, "is_enabled": doc.is_enabled},
            )
        except Exception:
            for key, value in old.items():
                setattr(doc, key, value)
            db.rollback()
            raise
        db.commit()
        return _document_output(db, doc)


@router.get("/knowledge-bases/{kb_id}/acl-options")
def acl_options(kb_id: str, user: Principal = Depends(require("knowledge:update"))):
    """提供 ACL 对话框所需的租户内真实对象，前端无需手填 ID。"""

    with session_factory()() as db:
        kb_access(db, user, kb_id, "owner")
        return {
            "departments": [{"id": x.id, "name": x.name, "parent_id": x.parent_id} for x in db.scalars(
                select(Department).where(Department.tenant_id == user.tenant_id).order_by(Department.name)
            )],
            "roles": [{"id": x.id, "name": x.name} for x in db.scalars(
                select(Role).where(Role.tenant_id == user.tenant_id).order_by(Role.name)
            )],
            "users": [{"id": x.id, "name": x.username} for x in db.scalars(
                select(User).where(User.tenant_id == user.tenant_id, User.is_active.is_(True)).order_by(User.username)
            )],
        }


@router.get("/documents/{document_id}/permissions", response_model=AclOutput)
def get_acl(document_id: str, user: Principal = Depends(require("knowledge:update"))):
    with session_factory()() as db:
        doc = scoped(db, DocumentRecord, document_id, user.tenant_id)
        kb_access(db, user, doc.kb_id, "owner")
        return {"document_id": doc.id, "acl_version": doc.acl_version, "entries": acl_entries(db, doc.id)}


@router.put("/documents/{document_id}/permissions", response_model=AclOutput)
def put_acl(
    document_id: str,
    body: AclInput,
    user: Principal = Depends(require("knowledge:update")),
    service=Depends(get_knowledge_service),
):
    with session_factory()() as db:
        doc = scoped(db, DocumentRecord, document_id, user.tenant_id)
        kb_access(db, user, doc.kb_id, "owner")
        if doc.status not in ("ready", "acl_failed"):
            raise HTTPException(409, "文档状态不允许更新权限")
        validate_acl(db, user, body.entries)
        acquired = db.execute(
            update(DocumentRecord)
            .where(
                DocumentRecord.id == doc.id,
                DocumentRecord.status == doc.status,
                DocumentRecord.acl_version == doc.acl_version,
            )
            .values(status="acl_syncing", acl_version=doc.acl_version + 1)
        )
        if acquired.rowcount != 1:
            raise HTTPException(409, "文档权限状态已变化，请重试")
        db.execute(delete(DocumentPermission).where(DocumentPermission.document_id == doc.id))
        db.add_all(DocumentPermission(document_id=doc.id, **x.model_dump()) for x in body.entries)
        db.commit()
        try:
            service.repository.set_document_acl(user.tenant_id, doc.kb_id, doc.id, acl_payload(db, doc))
        except Exception:
            doc.status = "acl_failed"
            db.commit()
            raise
        doc.status = "ready"
        db.commit()
        return {"document_id": doc.id, "acl_version": doc.acl_version, "entries": body.entries}


@router.delete("/documents/{document_id}")
def delete_document(
    document_id: str,
    user: Principal = Depends(require("knowledge:delete")),
    service=Depends(get_knowledge_service),
):
    return {"document_id": document_id, "deleted_chunks": service.delete(user, document_id)}


@router.post("/search")
def search(
    body: AuthorizedSearchInput,
    user: Principal = Depends(require("knowledge:read")),
    service=Depends(get_knowledge_service),
):
    check_tenant(user, body.tenant_id)
    hits, restricted = service.search(
        user,
        body.kb_id,
        body.query,
        body.candidate_limit,
        min(body.top_k, body.candidate_limit),
        body.metadata_filters,
    )
    items = [
        SearchHitResponse(
            chunk_id=x.chunk.chunk_id,
            document_id=x.chunk.document_id,
            content=x.chunk.content,
            title=x.chunk.title,
            source=x.chunk.source,
            metadata=x.chunk.metadata,
            dense_rank=x.dense_rank,
            sparse_rank=x.sparse_rank,
            dense_score=x.dense_score,
            sparse_score=x.sparse_score,
            rrf_score=x.rrf_score,
            rerank_score=x.rerank_score,
        )
        for x in hits
    ]
    return {
        "query": body.query,
        "items": items,
        "count": len(items),
        "restricted_sources_detected": restricted,
    }
