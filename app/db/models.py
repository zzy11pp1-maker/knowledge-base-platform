"""SQLAlchemy 2.x 业务模型；ACL 和关联关系以独立行保存。"""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_id() -> str:
    return str(uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """声明式模型基类。"""


class Department(Base):
    __tablename__ = "departments"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)
    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    name: Mapped[str] = mapped_column(String(128))
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("departments.id", ondelete="RESTRICT"))


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("tenant_id", "username"),)
    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    username: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(512))
    department_id: Mapped[str | None] = mapped_column(ForeignKey("departments.id", ondelete="RESTRICT"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    auth_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)
    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    name: Mapped[str] = mapped_column(String(100))
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)


class Permission(Base):
    __tablename__ = "permissions"
    code: Mapped[str] = mapped_column(String(100), primary_key=True)
    description: Mapped[str] = mapped_column(String(255))


class UserRole(Base):
    __tablename__ = "user_roles"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role_id: Mapped[str] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)


class RolePermission(Base):
    __tablename__ = "role_permissions"
    role_id: Mapped[str] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    permission_code: Mapped[str] = mapped_column(
        ForeignKey("permissions.code", ondelete="CASCADE"), primary_key=True
    )


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"
    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class KnowledgeBaseMember(Base):
    __tablename__ = "knowledge_base_members"
    __table_args__ = (CheckConstraint("role IN ('owner','editor','viewer')"),)
    kb_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16))


class DocumentRecord(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    kb_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id", ondelete="RESTRICT"), index=True)
    creator_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    title: Mapped[str] = mapped_column(String(512), default="")
    source: Mapped[str] = mapped_column(String(512), default="")
    version: Mapped[str] = mapped_column(String(64), default="")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(24), default="processing")
    file_type: Mapped[str] = mapped_column(String(16), default="text")
    category: Mapped[str] = mapped_column(String(128), default="未分类")
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    chunk_size: Mapped[int] = mapped_column(Integer, default=1000)
    chunk_overlap: Mapped[int] = mapped_column(Integer, default=100)
    error_message: Mapped[str | None] = mapped_column(String(512))
    acl_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class DocumentPermission(Base):
    __tablename__ = "document_permissions"
    __table_args__ = (
        UniqueConstraint("document_id", "permission_type", "target_id"),
        CheckConstraint("permission_type IN ('global','department','role','user')"),
        CheckConstraint(
            "(permission_type = 'global' AND target_id = '*') OR (permission_type != 'global' AND target_id != '*')"
        ),
    )
    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    permission_type: Mapped[str] = mapped_column(String(20))
    target_id: Mapped[str] = mapped_column(String(128))


class IngestionJob(Base):
    """后台文档导入任务；页面据此展示真实处理状态，而不是模拟进度。"""

    __tablename__ = "ingestion_jobs"
    __table_args__ = (CheckConstraint("status IN ('queued','processing','ready','failed')"),)
    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    kb_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    stage: Mapped[str] = mapped_column(String(100), default="等待处理")
    document_id: Mapped[str | None] = mapped_column(String(128), index=True)
    error_message: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kb_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id", ondelete="RESTRICT"))
    title: Mapped[str] = mapped_column(String(200), default="新会话")
    busy_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    busy_turn: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (CheckConstraint("role IN ('user','assistant')"),)
    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    turn_id: Mapped[str] = mapped_column(String(128), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="complete")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MessageCitation(Base):
    __tablename__ = "message_citations"
    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(16))
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="RESTRICT"))
    chunk_id: Mapped[str] = mapped_column(String(128))
    document_version: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(512))
    source: Mapped[str] = mapped_column(String(512))
    chunk_index: Mapped[int] = mapped_column(Integer)
    # 所有进入提示词的来源都留痕，未显式引用的来源同样参与历史撤权检查。
    used: Mapped[bool] = mapped_column(Boolean, default=True)


class FAQCluster(Base):
    """同一租户和知识库内的语义问题簇。"""

    __tablename__ = "faq_clusters"
    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    kb_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True)
    normalized_question: Mapped[str] = mapped_column(String(1000))
    question_samples_json: Mapped[str] = mapped_column(Text, default="[]")
    message_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    embedding_json: Mapped[str] = mapped_column(Text)
    occurrence_count: Mapped[int] = mapped_column(Integer, default=0)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class FAQCandidate(Base):
    __tablename__ = "faq_candidates"
    __table_args__ = (
        UniqueConstraint("cluster_id"),
        CheckConstraint("status IN ('pending','approved','rejected','published')"),
    )
    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    cluster_id: Mapped[str] = mapped_column(ForeignKey("faq_clusters.id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    kb_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True)
    standard_question: Mapped[str] = mapped_column(String(1000))
    proposed_answer: Mapped[str | None] = mapped_column(Text)
    source_message_id: Mapped[str | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"))
    has_quality_answer: Mapped[bool] = mapped_column(Boolean, default=False)
    occurrence_count: Mapped[int] = mapped_column(Integer, default=0)
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class FAQEntry(Base):
    __tablename__ = "faq_entries"
    __table_args__ = (UniqueConstraint("candidate_id"),)
    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("faq_candidates.id", ondelete="CASCADE"))
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    kb_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True)
    question: Mapped[str] = mapped_column(String(1000))
    answer: Mapped[str] = mapped_column(Text)
    embedding_json: Mapped[str] = mapped_column(Text)
    source_message_id: Mapped[str] = mapped_column(ForeignKey("messages.id", ondelete="RESTRICT"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class KnowledgeGap(Base):
    __tablename__ = "knowledge_gaps"
    __table_args__ = (
        UniqueConstraint("tenant_id", "kb_id", "normalized_question", "gap_type"),
        CheckConstraint("status IN ('open','resolved')"),
    )
    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    kb_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True)
    normalized_question: Mapped[str] = mapped_column(String(1000))
    original_samples_json: Mapped[str] = mapped_column(Text, default="[]")
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    gap_type: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    suggested_action: Mapped[str | None] = mapped_column(String(1000))
    department_id: Mapped[str | None] = mapped_column(String(128), index=True)
    highest_similarity_score: Mapped[float | None] = mapped_column(Float)
    suggested_category: Mapped[str | None] = mapped_column(String(128))


class MetricEvent(Base):
    """轻量真实事件表；原始知识正文不得写入 metadata。"""

    __tablename__ = "metric_events"
    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    kb_id: Mapped[str | None] = mapped_column(String(128), index=True)
    user_id: Mapped[str | None] = mapped_column(String(128), index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    duration_ms: Mapped[float | None] = mapped_column(Float)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class QuestionAudit(Base):
    """单次问答的可追溯审计摘要；只保存知识单元 ID，不复制受限正文。"""

    __tablename__ = "question_audits"
    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    kb_id: Mapped[str] = mapped_column(String(128), index=True)
    conversation_id: Mapped[str] = mapped_column(String(128), index=True)
    message_id: Mapped[str | None] = mapped_column(String(128), index=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    question_text: Mapped[str] = mapped_column(Text)
    recalled_document_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    allowed_document_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    denied_document_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    response_ms: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class KnowledgeTask(Base):
    """由知识缺口一键生成的待补充知识任务。"""

    __tablename__ = "knowledge_tasks"
    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    kb_id: Mapped[str] = mapped_column(String(128), index=True)
    gap_id: Mapped[str] = mapped_column(ForeignKey("knowledge_gaps.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(24), default="pending")
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ModelConfiguration(Base):
    """平台模型接口的非密钥参数；API Key 始终只从环境变量读取。"""

    __tablename__ = "model_configurations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default="default")
    bge_base_url: Mapped[str | None] = mapped_column(String(1000))
    reranker_base_url: Mapped[str | None] = mapped_column(String(1000))
    llm_base_url: Mapped[str | None] = mapped_column(String(1000))
    llm_model: Mapped[str | None] = mapped_column(String(500))
    embedding_model_id: Mapped[str | None] = mapped_column(String(500))
    updated_by: Mapped[str | None] = mapped_column(String(128))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
