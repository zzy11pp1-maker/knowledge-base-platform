"""统一配置入口。

真实地址、密钥和部署差异全部由环境变量或项目根目录的 ``.env`` 提供。
模块导入时不强制要求外部服务配置，因此未配置服务时 ``/health`` 仍可启动。
"""

import os
from functools import lru_cache

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.exceptions import ConfigurationError


class Settings(BaseSettings):
    """应用配置；字段名可直接使用对应的大写环境变量。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "2.9 企业知识库管理平台"
    app_env: str = "development"
    log_level: str = "INFO"

    # 业务数据库与身份认证；JWT 密钥没有默认值，未配置时拒绝登录。
    database_url: str = "sqlite:///.local/platform.db"
    jwt_secret: SecretStr | None = None
    jwt_issuer: str = "knowledge-platform"
    jwt_audience: str = "knowledge-platform-api"
    access_token_minutes: int = 30
    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    llm_model: str | None = None
    llm_timeout: float = 90
    llm_max_tokens: int = 1500
    chat_history_messages: int = 12
    chat_context_chars: int = 18000
    restricted_score_threshold: float = 0.35

    max_upload_bytes: int = 20 * 1024 * 1024
    max_chunk_size: int = 1000
    chunk_overlap: int = 100

    bge_base_url: str | None = None
    bge_embedding_path: str = "/v1/embedding"
    bge_health_path: str = "/health"
    bge_api_key: SecretStr | None = None
    embedding_batch_size: int = 16
    dense_vector_size: int = 1024
    embedding_model_id: str = "bge-m3"

    reranker_base_url: str | None = None
    reranker_path: str = "/v1/rerank"
    reranker_health_path: str = "/health"
    reranker_api_key: SecretStr | None = None

    http_connect_timeout: float = 5.0
    http_read_timeout: float = 60.0
    http_max_retries: int = 2

    qdrant_url: str | None = None
    qdrant_location: str | None = None
    qdrant_api_key: SecretStr | None = None
    qdrant_collection: str = "knowledge_chunks_v1"
    qdrant_prefer_grpc: bool = False
    qdrant_upsert_batch_size: int = 64

    rrf_k: int = 60
    search_candidate_limit: int = 30
    rerank_top_k: int = 5

    # 第三阶段业务阈值。FAQ 只在同一租户、同一知识库内聚类和命中。
    faq_similarity_threshold: float = 0.88
    faq_min_occurrences: int = 2
    gap_retrieval_score_threshold: float = 0.015
    gap_rerank_score_threshold: float = 0.0
    max_batch_files: int = 20

    @model_validator(mode="after")
    def validate_ranges(self) -> "Settings":
        """尽早拦截会导致切分死循环或无效查询的配置。"""

        if self.max_chunk_size <= 0:
            raise ValueError("MAX_CHUNK_SIZE 必须大于 0")
        if self.chunk_overlap < 0 or self.chunk_overlap >= self.max_chunk_size:
            raise ValueError("CHUNK_OVERLAP 必须大于等于 0 且小于 MAX_CHUNK_SIZE")
        positive_fields = {
            "ACCESS_TOKEN_MINUTES": self.access_token_minutes,
            "LLM_TIMEOUT": self.llm_timeout,
            "LLM_MAX_TOKENS": self.llm_max_tokens,
            "CHAT_HISTORY_MESSAGES": self.chat_history_messages,
            "CHAT_CONTEXT_CHARS": self.chat_context_chars,
            "MAX_UPLOAD_BYTES": self.max_upload_bytes,
            "EMBEDDING_BATCH_SIZE": self.embedding_batch_size,
            "DENSE_VECTOR_SIZE": self.dense_vector_size,
            "HTTP_CONNECT_TIMEOUT": self.http_connect_timeout,
            "HTTP_READ_TIMEOUT": self.http_read_timeout,
            "RRF_K": self.rrf_k,
            "SEARCH_CANDIDATE_LIMIT": self.search_candidate_limit,
            "RERANK_TOP_K": self.rerank_top_k,
            "QDRANT_UPSERT_BATCH_SIZE": self.qdrant_upsert_batch_size,
            "FAQ_MIN_OCCURRENCES": self.faq_min_occurrences,
            "MAX_BATCH_FILES": self.max_batch_files,
        }
        for name, value in positive_fields.items():
            if value <= 0:
                raise ValueError(f"{name} 必须大于 0")
        if self.http_max_retries < 0:
            raise ValueError("HTTP_MAX_RETRIES 不能小于 0")
        if self.qdrant_url and self.qdrant_location:
            raise ValueError("QDRANT_URL 与 QDRANT_LOCATION 只能配置一个")
        if not 0 < self.faq_similarity_threshold <= 1:
            raise ValueError("FAQ_SIMILARITY_THRESHOLD 必须在 (0, 1] 内")
        return self

    def require_bge_url(self) -> str:
        if not self.bge_base_url:
            raise ConfigurationError("缺少 BGE_BASE_URL，请在 .env 中配置远程 BGE-M3 地址")
        return self.bge_base_url.rstrip("/")

    def require_reranker_url(self) -> str:
        if not self.reranker_base_url:
            raise ConfigurationError("缺少 RERANKER_BASE_URL，请在 .env 中配置远程 Reranker 地址")
        return self.reranker_base_url.rstrip("/")

    def require_qdrant_url(self) -> str:
        if not self.qdrant_url:
            raise ConfigurationError("缺少 QDRANT_URL，请在 .env 中配置 Qdrant 地址")
        return self.qdrant_url.rstrip("/")

    def require_qdrant_target(self) -> tuple[str, str]:
        """返回远程 URL 或本地存储路径，方便无 Docker 的开发环境。"""

        if self.qdrant_location:
            return "location", self.qdrant_location
        return "url", self.require_qdrant_url()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """缓存配置，避免每个请求重复解析环境变量。"""

    return Settings(_env_file=os.environ.get("PLATFORM_ENV_FILE", ".env"))
