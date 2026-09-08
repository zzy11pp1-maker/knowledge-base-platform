"""远程 BGE-M3 dense+sparse Embedding 客户端。"""

from __future__ import annotations

import logging
from math import isfinite
from typing import Any

import httpx

from app.core.exceptions import EmbeddingServiceError
from app.integrations.http_client import JsonHttpClient
from app.models.domain import EmbeddingBatch, SparseEmbedding


LOGGER = logging.getLogger(__name__)


class BgeM3Client:
    """调用独立 GPU 服务，不在业务进程加载 FlagEmbedding。"""

    def __init__(
        self,
        *,
        base_url: str,
        embedding_path: str = "/v1/embedding",
        health_path: str = "/health",
        api_key: str | None = None,
        batch_size: int = 16,
        dense_vector_size: int = 1024,
        connect_timeout: float = 5,
        read_timeout: float = 60,
        max_retries: int = 2,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.embedding_path = embedding_path
        self.health_path = health_path
        self.batch_size = batch_size
        self.dense_vector_size = dense_vector_size
        self.http = JsonHttpClient(
            base_url=base_url,
            api_key=api_key,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            max_retries=max_retries,
            client=http_client,
        )

    def embed(self, texts: list[str]) -> EmbeddingBatch:
        """分批获取向量，并严格校验远程响应。"""

        if not texts or any(not text.strip() for text in texts):
            raise EmbeddingServiceError("Embedding 输入不能为空")
        all_dense: list[list[float]] = []
        all_sparse: list[SparseEmbedding] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            try:
                payload = self.http.request_json("POST", self.embedding_path, json={"texts": batch})
                parsed = self._parse_response(payload, expected_count=len(batch))
            except EmbeddingServiceError:
                raise
            except Exception as exc:
                LOGGER.exception("BGE-M3 调用失败 batch_size=%s", len(batch))
                raise EmbeddingServiceError(f"BGE-M3 调用失败：{type(exc).__name__}") from exc
            all_dense.extend(parsed.dense)
            all_sparse.extend(parsed.sparse)
        return EmbeddingBatch(dense=all_dense, sparse=all_sparse)

    def health(self) -> dict[str, Any]:
        try:
            payload = self.http.request_json("GET", self.health_path)
            return {"ok": True, "detail": payload}
        except Exception as exc:
            return {"ok": False, "detail": type(exc).__name__}

    def close(self) -> None:
        self.http.close()

    def _parse_response(self, payload: Any, *, expected_count: int) -> EmbeddingBatch:
        if not isinstance(payload, dict) or "dense" not in payload or "sparse" not in payload:
            raise EmbeddingServiceError("BGE-M3 响应缺少 dense 或 sparse")
        dense_raw = payload["dense"]
        sparse_raw = payload["sparse"]
        if expected_count == 1 and dense_raw and isinstance(dense_raw[0], (int, float)):
            dense_raw = [dense_raw]
        if expected_count == 1 and isinstance(sparse_raw, dict):
            sparse_raw = [sparse_raw]
        if not isinstance(dense_raw, list) or not isinstance(sparse_raw, list):
            raise EmbeddingServiceError("BGE-M3 响应类型错误")
        if len(dense_raw) != expected_count or len(sparse_raw) != expected_count:
            raise EmbeddingServiceError("BGE-M3 响应数量与输入数量不一致")

        dense: list[list[float]] = []
        for vector in dense_raw:
            if not isinstance(vector, list) or len(vector) != self.dense_vector_size:
                raise EmbeddingServiceError(f"dense 向量维度必须为 {self.dense_vector_size}")
            converted = [float(value) for value in vector]
            if not all(isfinite(value) for value in converted):
                raise EmbeddingServiceError("dense 向量包含非有限数值")
            dense.append(converted)
        sparse = [self._parse_sparse(item) for item in sparse_raw]
        return EmbeddingBatch(dense=dense, sparse=sparse)

    @staticmethod
    def _parse_sparse(item: Any) -> SparseEmbedding:
        if not isinstance(item, dict):
            raise EmbeddingServiceError("sparse 向量必须是对象")
        if "indices" in item and "values" in item:
            indices = [int(index) for index in item["indices"]]
            values = [float(value) for value in item["values"]]
        else:
            try:
                pairs = sorted((int(index), float(value)) for index, value in item.items() if float(value) != 0)
            except (TypeError, ValueError) as exc:
                raise EmbeddingServiceError("sparse 键必须是 tokenizer token id") from exc
            indices = [index for index, _ in pairs]
            values = [value for _, value in pairs]
        try:
            return SparseEmbedding(indices=indices, values=values)
        except ValueError as exc:
            raise EmbeddingServiceError(f"sparse 向量不合法：{exc}") from exc
