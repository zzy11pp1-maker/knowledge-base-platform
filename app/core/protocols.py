"""业务层依赖的最小接口。

使用标准库 Protocol 解耦具体 HTTP 客户端，便于后续替换模型服务和编写确定性单测。
"""

from typing import Protocol

from app.models.domain import EmbeddingBatch


class EmbeddingProvider(Protocol):
    def embed(self, texts: list[str]) -> EmbeddingBatch: ...


class RerankingProvider(Protocol):
    def rerank(self, query: str, texts: list[str]) -> list[float]: ...
