"""受保护业务输入输出契约，未知字段拒绝接收以避免越权赋值。"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Id = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class LoginInput(StrictModel):
    tenant_id: Id
    username: Name
    password: str = Field(min_length=1, max_length=256)


class TokenOutput(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserCreate(StrictModel):
    username: Name
    password: str = Field(min_length=12, max_length=256)
    department_id: Id | None = None
    role_ids: list[Id] = Field(default_factory=list, max_length=100)


class UserPatch(StrictModel):
    password: str | None = Field(default=None, min_length=12, max_length=256)
    department_id: Id | None = None
    role_ids: list[Id] | None = Field(default=None, max_length=100)
    is_active: bool | None = None


class UserOutput(BaseModel):
    id: str
    tenant_id: str
    username: str
    department_id: str | None
    is_active: bool
    role_ids: list[str]


class DepartmentCreate(StrictModel):
    name: Name
    parent_id: Id | None = None


class DepartmentPatch(StrictModel):
    name: Name | None = None
    parent_id: Id | None = None


class DepartmentOutput(BaseModel):
    id: str
    name: str
    parent_id: str | None
    children: list["DepartmentOutput"] = Field(default_factory=list)


class RoleCreate(StrictModel):
    name: Name
    permission_codes: list[Id] = Field(default_factory=list, max_length=100)


class RolePatch(StrictModel):
    name: Name | None = None
    permission_codes: list[Id] | None = Field(default=None, max_length=100)


class RoleOutput(BaseModel):
    id: str
    name: str
    is_system: bool
    permission_codes: list[str]


class KnowledgeBaseCreate(StrictModel):
    name: Name
    description: str = Field(default="", max_length=4000)


class KnowledgeBasePatch(StrictModel):
    name: Name | None = None
    description: str | None = Field(default=None, max_length=4000)


class KnowledgeBaseOutput(StrictModel):
    id: str
    name: str
    description: str
    owner_id: str


class MemberInput(StrictModel):
    user_id: Id
    role: Literal["editor", "viewer"]


class MemberPatch(StrictModel):
    role: Literal["editor", "viewer"]


class MemberOutput(BaseModel):
    user_id: str
    role: str


class AclEntry(StrictModel):
    permission_type: Literal["global", "department", "role", "user"]
    target_id: Id | None = None

    @model_validator(mode="after")
    def valid_target(self):
        if self.permission_type == "global":
            if self.target_id not in (None, "*"):
                raise ValueError("global 不接受目标对象")
            self.target_id = "*"
        elif self.target_id is None or self.target_id == "*":
            raise ValueError("非 global 权限必须指定目标")
        return self


class AclInput(StrictModel):
    entries: list[AclEntry] = Field(max_length=200)


class AclOutput(BaseModel):
    document_id: str
    acl_version: int
    entries: list[AclEntry]


class DocumentOutput(StrictModel):
    id: str
    kb_id: str
    title: str
    source: str
    version: str
    status: str
    chunk_count: int
    file_type: str
    category: str
    is_enabled: bool
    chunk_size: int
    chunk_overlap: int
    error_message: str | None
    created_at: datetime
    updated_at: datetime | None
    permission_labels: list[str] = Field(default_factory=list)


class DocumentPatch(StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=512)
    category: str | None = Field(default=None, min_length=1, max_length=128)
    is_enabled: bool | None = None


class IngestionJobOutput(StrictModel):
    id: str
    kb_id: str
    filename: str
    status: Literal["queued", "processing", "ready", "failed"]
    progress: int
    stage: str
    document_id: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class AuthorizedSearchInput(StrictModel):
    # 为旧客户端保留可选 tenant；若提供则必须与认证租户一致。
    tenant_id: Id | None = None
    kb_id: Id
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
    candidate_limit: int = Field(default=30, ge=1, le=200)
    top_k: int = Field(default=5, ge=1, le=50)
    metadata_filters: dict[str, str | int | bool] = Field(default_factory=dict)


class Citation(StrictModel):
    label: str
    document_id: str
    chunk_id: str
    title: str
    source: str
    chunk_index: int
    document_version: str


class ConversationCreate(StrictModel):
    kb_id: Id
    title: Name = "新会话"


class ConversationOutput(StrictModel):
    id: str
    kb_id: str
    title: str
    created_at: datetime


class ChatInput(StrictModel):
    kb_id: Id
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
    conversation_id: Id | None = None


class ChatOutput(BaseModel):
    conversation_id: str
    message_id: str
    answer: str
    citations: list[Citation]
    restricted_sources_detected: bool
    faq_hit: bool = False


class FAQReviewInput(StrictModel):
    status: Literal["approved", "rejected"]
    question: str | None = Field(default=None, min_length=1, max_length=1000)
    answer: str | None = Field(default=None, min_length=1, max_length=100_000)


class FAQCandidateOutput(StrictModel):
    id: str
    cluster_id: str
    kb_id: str
    standard_question: str
    proposed_answer: str | None
    has_quality_answer: bool
    occurrence_count: int
    confidence_score: float
    question_samples: list[str] = Field(default_factory=list)
    source_document_ids: list[str] = Field(default_factory=list)
    status: str
    created_at: datetime


class FAQEntryOutput(StrictModel):
    id: str
    kb_id: str
    question: str
    answer: str
    enabled: bool
    hit_count: int
    published_at: datetime


class FAQEntryPatch(StrictModel):
    enabled: bool


class KnowledgeGapOutput(StrictModel):
    id: str
    kb_id: str
    normalized_question: str
    original_samples_json: str
    occurrence_count: int
    first_seen_at: datetime
    last_seen_at: datetime
    gap_type: str
    status: str
    suggested_action: str | None
    department_id: str | None
    highest_similarity_score: float | None
    suggested_category: str | None


class QuestionAuditOutput(StrictModel):
    id: str
    kb_id: str
    conversation_id: str
    message_id: str | None
    user_id: str
    question_text: str
    recalled_document_ids: list[str]
    allowed_document_ids: list[str]
    denied_document_ids: list[str]
    prompt_tokens: int
    completion_tokens: int
    response_ms: float
    created_at: datetime


class PageViewInput(StrictModel):
    route: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    kb_id: Id | None = None


class ModelConfigPatch(StrictModel):
    bge_base_url: str | None = Field(default=None, max_length=1000)
    reranker_base_url: str | None = Field(default=None, max_length=1000)
    llm_base_url: str | None = Field(default=None, max_length=1000)
    llm_model: str | None = Field(default=None, max_length=500)
    embedding_model_id: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def valid_urls(self):
        from urllib.parse import urlsplit
        for name in ("bge_base_url", "reranker_base_url", "llm_base_url"):
            value = getattr(self, name)
            if value is None:
                continue
            parsed = urlsplit(value)
            if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError(f"{name} 不是合法 HTTP(S) 地址")
        return self
