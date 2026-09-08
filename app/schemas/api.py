"""FastAPI 请求与响应 Schema。"""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]


class ErrorResponse(BaseModel):
    code: str
    message: str
    request_id: str | None = None


class DocumentSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    document_id: str
    tenant_id: str
    kb_id: str
    title: str
    source: str
    document_version: str
    chunk_count: int
    created_at: datetime


class DocumentImportResponse(BaseModel):
    document: DocumentSummaryResponse
    upserted_chunks: int


class DocumentListResponse(BaseModel):
    items: list[DocumentSummaryResponse]
    count: int


class DocumentDeleteResponse(BaseModel):
    document_id: str
    deleted_chunks: int


class SearchRequest(BaseModel):
    tenant_id: Identifier
    kb_id: Identifier
    query: str = Field(min_length=1, max_length=4000)
    candidate_limit: int | None = Field(default=None, ge=1, le=200)
    top_k: int | None = Field(default=None, ge=1, le=50)
    metadata_filters: dict[str, str | int | bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def top_k_not_larger_than_candidates(self) -> "SearchRequest":
        if self.top_k is not None and self.candidate_limit is not None and self.top_k > self.candidate_limit:
            raise ValueError("top_k 不能大于 candidate_limit")
        return self


class SearchHitResponse(BaseModel):
    chunk_id: str
    document_id: str
    content: str
    title: str
    source: str
    metadata: dict
    dense_rank: int | None
    sparse_rank: int | None
    dense_score: float | None
    sparse_score: float | None
    rrf_score: float
    rerank_score: float


class SearchResponse(BaseModel):
    query: str
    items: list[SearchHitResponse]
    count: int
