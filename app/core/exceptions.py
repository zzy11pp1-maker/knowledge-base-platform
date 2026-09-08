"""应用异常：业务层只抛稳定异常，API 层统一转换为 JSON。"""


class PlatformError(Exception):
    """所有可预期平台异常的基类。"""

    code = "platform_error"
    status_code = 500

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ConfigurationError(PlatformError):
    code = "configuration_error"
    status_code = 503


class DocumentReadError(PlatformError):
    code = "document_read_error"
    status_code = 400


class DocumentNotFoundError(PlatformError):
    code = "document_not_found"
    status_code = 404


class EmbeddingServiceError(PlatformError):
    code = "embedding_service_error"
    status_code = 502


class RerankerServiceError(PlatformError):
    code = "reranker_service_error"
    status_code = 502


class VectorStoreError(PlatformError):
    code = "vector_store_error"
    status_code = 502
