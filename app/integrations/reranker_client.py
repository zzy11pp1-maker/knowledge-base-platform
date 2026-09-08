"""远程 Reranker 客户端。"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from math import isfinite
from typing import Any

import httpx

from app.core.exceptions import RerankerServiceError
from app.integrations.http_client import JsonHttpClient


LOGGER = logging.getLogger(__name__)


class RerankerClient:
    """调用远程精排服务，并把不同常见返回格式归一为输入顺序分数。"""

    def __init__(
        self,
        *,
        base_url: str,
        rerank_path: str = "/v1/rerank",
        health_path: str = "/health",
        api_key: str | None = None,
        connect_timeout: float = 5,
        read_timeout: float = 60,
        max_retries: int = 2,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.rerank_path = rerank_path
        self.health_path = health_path
        self.http = JsonHttpClient(
            base_url=base_url,
            api_key=api_key,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            max_retries=max_retries,
            client=http_client,
        )

    def rerank(self, query: str, texts: list[str]) -> list[float]:
        if not query.strip() or not texts:
            raise RerankerServiceError("Reranker 的 query 和 texts 不能为空")
        try:
            payload = self.http.request_json("POST", self.rerank_path, json={"query": query, "texts": texts})
            return self._parse_response(payload, texts)
        except RerankerServiceError:
            raise
        except Exception as exc:
            LOGGER.exception("Reranker 调用失败 candidate_count=%s", len(texts))
            raise RerankerServiceError(f"Reranker 调用失败：{type(exc).__name__}") from exc

    def health(self) -> dict[str, Any]:
        try:
            payload = self.http.request_json("GET", self.health_path)
            return {"ok": True, "detail": payload}
        except Exception as exc:
            return {"ok": False, "detail": type(exc).__name__}

    def close(self) -> None:
        self.http.close()

    @staticmethod
    def _parse_response(payload: Any, texts: list[str]) -> list[float]:
        results = payload.get("rerank_result", payload.get("results")) if isinstance(payload, dict) else payload
        if not isinstance(results, list) or len(results) != len(texts):
            raise RerankerServiceError("Reranker 响应数量与候选数量不一致")
        if all(isinstance(item, (int, float)) for item in results):
            scores = [float(item) for item in results]
        else:
            scores: list[float | None] = [None] * len(texts)
            text_positions: dict[str, deque[int]] = defaultdict(deque)
            for index, text in enumerate(texts):
                text_positions[text].append(index)
            for position, item in enumerate(results):
                if not isinstance(item, dict):
                    raise RerankerServiceError("Reranker 响应条目类型错误")
                raw_score = item.get("score", item.get("relevance_score"))
                if raw_score is None:
                    raise RerankerServiceError("Reranker 响应缺少 score")
                if "index" in item:
                    index = int(item["index"])
                elif "text" in item and text_positions[str(item["text"])]:
                    # 现有 AutoDL 接口返回排序后的 text+score；队列可正确处理重复文本。
                    index = text_positions[str(item["text"])].popleft()
                else:
                    index = position
                if index < 0 or index >= len(texts) or scores[index] is not None:
                    raise RerankerServiceError("Reranker 响应 index 无效或重复")
                scores[index] = float(raw_score)
            if any(score is None for score in scores):
                raise RerankerServiceError("Reranker 响应未覆盖全部候选")
            scores = [float(score) for score in scores]
        if not all(isfinite(score) for score in scores):
            raise RerankerServiceError("Reranker 分数包含非有限数值")
        return scores
