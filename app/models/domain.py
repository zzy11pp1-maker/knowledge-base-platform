"""第一阶段领域模型。"""

from datetime import datetime, timezone
from math import isfinite
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> datetime:
    """生成带时区的 UTC 时间，避免跨环境时间歧义。"""

    return datetime.now(timezone.utc)


class Document(BaseModel):
    """一次可追踪、可重建的源文档版本。"""

    model_config = ConfigDict(extra="forbid")

    document_id: str
    tenant_id: str
    kb_id: str
    title: str
    source: str
    content: str
    content_hash: str
    version: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class Chunk(BaseModel):
    """写入向量库的最小知识单元。"""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    tenant_id: str
    kb_id: str
    document_id: str
    chunk_index: int = Field(ge=0)
    content: str = Field(min_length=1)
    title: str
    source: str
    document_version: str
    content_hash: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def reject_blank_identifiers(self) -> "Chunk":
        for field_name in ("tenant_id", "kb_id", "document_id", "chunk_id", "source"):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} 不能为空")
        return self


class SparseEmbedding(BaseModel):
    """Qdrant 可直接使用的稀疏向量结构。"""

    indices: list[int]
    values: list[float]

    @model_validator(mode="after")
    def validate_shape(self) -> "SparseEmbedding":
        if len(self.indices) != len(self.values):
            raise ValueError("稀疏向量 indices 与 values 长度不一致")
        if any(index < 0 for index in self.indices):
            raise ValueError("稀疏向量索引不能为负数")
        if len(set(self.indices)) != len(self.indices):
            raise ValueError("稀疏向量索引不能重复")
        if not all(isfinite(value) for value in self.values):
            raise ValueError("稀疏向量包含非有限数值")
        return self


class EmbeddingBatch(BaseModel):
    """一批文本的 dense+sparse 结果。"""

    dense: list[list[float]]
    sparse: list[SparseEmbedding]


class VectorHit(BaseModel):
    """单路向量检索命中。"""

    chunk: Chunk
    score: float


class FusedCandidate(BaseModel):
    """RRF 融合后的候选，保留每路排名与原始分数。"""

    chunk: Chunk
    dense_rank: int | None = None
    sparse_rank: int | None = None
    dense_score: float | None = None
    sparse_score: float | None = None
    rrf_score: float = 0.0
    rerank_score: float | None = None


class DocumentSummary(BaseModel):
    """不加载正文即可展示的文档摘要。"""

    document_id: str
    tenant_id: str
    kb_id: str
    title: str
    source: str
    document_version: str
    chunk_count: int
    created_at: datetime


class IngestionResult(BaseModel):
    """文档入库结果。"""

    document: DocumentSummary
    upserted_chunks: int
