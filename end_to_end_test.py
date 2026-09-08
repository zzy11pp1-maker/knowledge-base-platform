"""真实端到端演示：test.md → 入库 → 混合检索 → RRF → 精排。"""

import os
from pathlib import Path

from app.core.config import get_settings
from app.core.dependencies import close_dependencies, get_document_service, get_search_service
from app.core.logging import configure_logging


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    tenant_id = os.getenv("TEST_TENANT_ID", "tenant-demo")
    kb_id = os.getenv("TEST_KB_ID", "kb-demo")
    query = os.getenv("TEST_QUERY", "企业知识库支持什么功能？")
    test_file = Path(__file__).resolve().parent / "data" / "test.md"

    print(f"[1/5] 读取文档：{test_file.name}")
    result = get_document_service().import_path(test_file, tenant_id=tenant_id, kb_id=kb_id)
    print(
        f"[2/5] 入库完成：document_id={result.document.document_id}, "
        f"chunks={result.upserted_chunks}"
    )
    stored = get_document_service().repository.count_document(
        tenant_id=tenant_id,
        kb_id=kb_id,
        document_id=result.document.document_id,
    )
    if stored != result.upserted_chunks:
        raise RuntimeError(f"Qdrant 核验失败：expected={result.upserted_chunks}, actual={stored}")
    print(f"[3/5] Qdrant 核验通过：{stored} 个 Chunk")

    print(f"[4/5] 提问：{query}")
    results = get_search_service().search(
        query=query,
        tenant_id=tenant_id,
        kb_id=kb_id,
        candidate_limit=settings.search_candidate_limit,
        top_k=settings.rerank_top_k,
    )
    print(f"[5/5] 最终 TopK：{len(results)}")
    for rank, item in enumerate(results, start=1):
        print(
            f"\n#{rank} rerank={item.rerank_score:.6f} rrf={item.rrf_score:.6f} "
            f"dense_rank={item.dense_rank} sparse_rank={item.sparse_rank}\n"
            f"source={item.chunk.source} document_id={item.chunk.document_id} "
            f"chunk_id={item.chunk.chunk_id}\n{item.chunk.content}"
        )


if __name__ == "__main__":
    try:
        main()
    finally:
        close_dependencies()
