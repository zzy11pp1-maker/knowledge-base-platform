"""基础文档与检索 API。"""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings, get_settings
from app.core.dependencies import get_document_service, get_search_service
from app.core.exceptions import DocumentReadError
from app.schemas.api import (
    DocumentDeleteResponse,
    DocumentImportResponse,
    DocumentListResponse,
    SearchHitResponse,
    SearchRequest,
    SearchResponse,
)
from app.services.document_service import DocumentService
from app.services.search_service import SearchService


router = APIRouter()


@router.post(
    "/documents/import",
    response_model=DocumentImportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def import_document(
    file: Annotated[UploadFile, File(description="Markdown 或 TXT 文档")],
    tenant_id: Annotated[str, Form(min_length=1, max_length=128)],
    kb_id: Annotated[str, Form(min_length=1, max_length=128)],
    document_id: Annotated[str | None, Form(min_length=1, max_length=128)] = None,
    service: DocumentService = Depends(get_document_service),
    settings: Settings = Depends(get_settings),
) -> DocumentImportResponse:
    """读取、切分、向量化并幂等写入文档。"""

    filename = file.filename or ""
    data = await file.read(settings.max_upload_bytes + 1)
    await file.close()
    if len(data) > settings.max_upload_bytes:
        raise DocumentReadError(f"文档超过大小限制：{settings.max_upload_bytes} bytes")
    result = await run_in_threadpool(
        service.import_bytes,
        filename=filename,
        data=data,
        tenant_id=tenant_id,
        kb_id=kb_id,
        document_id=document_id,
    )
    return DocumentImportResponse.model_validate(result, from_attributes=True)


@router.post("/search", response_model=SearchResponse)
def search(
    request: SearchRequest,
    service: SearchService = Depends(get_search_service),
    settings: Settings = Depends(get_settings),
) -> SearchResponse:
    """执行 dense+sparse、RRF 与远程 Reranker。"""

    candidate_limit = request.candidate_limit or settings.search_candidate_limit
    top_k = request.top_k or settings.rerank_top_k
    if top_k > candidate_limit:
        top_k = candidate_limit
    candidates = service.search(
        query=request.query.strip(),
        tenant_id=request.tenant_id,
        kb_id=request.kb_id,
        candidate_limit=candidate_limit,
        top_k=top_k,
        metadata_filters=request.metadata_filters,
    )
    items = [
        SearchHitResponse(
            chunk_id=item.chunk.chunk_id,
            document_id=item.chunk.document_id,
            content=item.chunk.content,
            title=item.chunk.title,
            source=item.chunk.source,
            metadata=item.chunk.metadata,
            dense_rank=item.dense_rank,
            sparse_rank=item.sparse_rank,
            dense_score=item.dense_score,
            sparse_score=item.sparse_score,
            rrf_score=item.rrf_score,
            rerank_score=item.rerank_score if item.rerank_score is not None else 0.0,
        )
        for item in candidates
    ]
    return SearchResponse(query=request.query, items=items, count=len(items))


@router.get("/documents", response_model=DocumentListResponse)
def list_documents(
    tenant_id: Annotated[str, Query(min_length=1, max_length=128)],
    kb_id: Annotated[str, Query(min_length=1, max_length=128)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    service: DocumentService = Depends(get_document_service),
) -> DocumentListResponse:
    items = service.list_documents(tenant_id=tenant_id, kb_id=kb_id, offset=offset, limit=limit)
    return DocumentListResponse(items=items, count=len(items))


@router.delete("/documents/{document_id}", response_model=DocumentDeleteResponse)
def delete_document(
    document_id: str,
    tenant_id: Annotated[str, Query(min_length=1, max_length=128)],
    kb_id: Annotated[str, Query(min_length=1, max_length=128)],
    service: DocumentService = Depends(get_document_service),
) -> DocumentDeleteResponse:
    deleted = service.delete_document(tenant_id=tenant_id, kb_id=kb_id, document_id=document_id)
    return DocumentDeleteResponse(document_id=document_id, deleted_chunks=deleted)
