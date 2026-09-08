"""受保护链路装配；复用第一阶段连接池，不重复打开本地 Qdrant。"""

from functools import lru_cache

from app.core.config import get_settings
from app.core.dependencies import get_bge_client, get_qdrant_repository, get_reranker_client
from app.ingestion.readers import TextDocumentReader
from app.repositories.authorized_qdrant import AuthorizedQdrantRepository
from app.services.authorized_knowledge import AuthorizedKnowledgeService
from app.services.document_service import DocumentService
from app.services.model_config import runtime_settings


@lru_cache(maxsize=1)
def get_knowledge_service():
    settings = runtime_settings()
    base = get_qdrant_repository()
    repository = AuthorizedQdrantRepository(
        base.client,
        settings.qdrant_collection,
        settings.dense_vector_size,
        upsert_batch_size=settings.qdrant_upsert_batch_size,
    )
    documents = DocumentService(
        reader=TextDocumentReader(settings.max_upload_bytes),
        embedder=get_bge_client(),
        repository=repository,
        max_chunk_size=settings.max_chunk_size,
        chunk_overlap=settings.chunk_overlap,
        embedding_model_id=settings.embedding_model_id,
    )
    return AuthorizedKnowledgeService(documents, repository, get_bge_client(), get_reranker_client())
