"""外部客户端和业务服务的生命周期管理。"""

from functools import lru_cache

from qdrant_client import QdrantClient

from app.core.config import get_settings
from app.services.model_config import runtime_settings
from app.ingestion.readers import TextDocumentReader
from app.integrations.bge_client import BgeM3Client
from app.integrations.reranker_client import RerankerClient
from app.repositories.qdrant_repository import QdrantRepository
from app.services.document_service import DocumentService
from app.services.search_service import SearchService


def _secret_value(secret: object | None) -> str | None:
    if secret is None:
        return None
    value = secret.get_secret_value()  # type: ignore[attr-defined]
    return value or None


@lru_cache(maxsize=1)
def get_bge_client() -> BgeM3Client:
    settings = runtime_settings()
    return BgeM3Client(
        base_url=settings.require_bge_url(),
        embedding_path=settings.bge_embedding_path,
        health_path=settings.bge_health_path,
        api_key=_secret_value(settings.bge_api_key),
        batch_size=settings.embedding_batch_size,
        dense_vector_size=settings.dense_vector_size,
        connect_timeout=settings.http_connect_timeout,
        read_timeout=settings.http_read_timeout,
        max_retries=settings.http_max_retries,
    )


@lru_cache(maxsize=1)
def get_reranker_client() -> RerankerClient:
    settings = runtime_settings()
    return RerankerClient(
        base_url=settings.require_reranker_url(),
        rerank_path=settings.reranker_path,
        health_path=settings.reranker_health_path,
        api_key=_secret_value(settings.reranker_api_key),
        connect_timeout=settings.http_connect_timeout,
        read_timeout=settings.http_read_timeout,
        max_retries=settings.http_max_retries,
    )


@lru_cache(maxsize=1)
def get_qdrant_repository() -> QdrantRepository:
    settings = runtime_settings()
    target_type, target = settings.require_qdrant_target()
    if target_type == "location":
        client = QdrantClient(path=target)
    else:
        client = QdrantClient(
            url=target,
            api_key=_secret_value(settings.qdrant_api_key),
            prefer_grpc=settings.qdrant_prefer_grpc,
            timeout=settings.http_read_timeout,
        )
    return QdrantRepository(
        client,
        settings.qdrant_collection,
        settings.dense_vector_size,
        upsert_batch_size=settings.qdrant_upsert_batch_size,
    )


@lru_cache(maxsize=1)
def get_document_service() -> DocumentService:
    settings = runtime_settings()
    return DocumentService(
        reader=TextDocumentReader(settings.max_upload_bytes),
        embedder=get_bge_client(),
        repository=get_qdrant_repository(),
        max_chunk_size=settings.max_chunk_size,
        chunk_overlap=settings.chunk_overlap,
        embedding_model_id=settings.embedding_model_id,
    )


@lru_cache(maxsize=1)
def get_search_service() -> SearchService:
    settings = runtime_settings()
    return SearchService(
        embedder=get_bge_client(),
        reranker=get_reranker_client(),
        repository=get_qdrant_repository(),
        rrf_k=settings.rrf_k,
    )


def close_dependencies() -> None:
    """应用退出时释放连接池；测试也可调用以清理缓存。"""

    if get_bge_client.cache_info().currsize:
        get_bge_client().close()
    if get_reranker_client.cache_info().currsize:
        get_reranker_client().close()
    if get_qdrant_repository.cache_info().currsize:
        get_qdrant_repository().client.close()
    get_document_service.cache_clear()
    get_search_service.cache_clear()
    get_bge_client.cache_clear()
    get_reranker_client.cache_clear()
    get_qdrant_repository.cache_clear()
