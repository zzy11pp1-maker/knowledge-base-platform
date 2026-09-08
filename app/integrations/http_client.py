"""外部 HTTP 服务的公共重试与安全日志逻辑。"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx


LOGGER = logging.getLogger(__name__)
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


class JsonHttpClient:
    """小型同步 JSON 客户端；业务接口由上层客户端负责校验。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        connect_timeout: float,
        read_timeout: float,
        max_retries: int,
        client: httpx.Client | None = None,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries 不能小于 0")
        headers = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=base_url,
            headers=headers,
            timeout=httpx.Timeout(read_timeout, connect=connect_timeout),
        )
        self._max_retries = max_retries

    def request_json(self, method: str, path: str, **kwargs: Any) -> Any:
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = self._client.request(method, path, **kwargs)
            except httpx.TransportError as exc:
                last_error = exc
                if attempt >= self._max_retries:
                    break
                LOGGER.warning("外部服务调用异常 type=%s attempt=%s", type(exc).__name__, attempt + 1)
                time.sleep(min(0.25 * (2**attempt), 1.0))
                continue

            if response.status_code in RETRYABLE_STATUS and attempt < self._max_retries:
                last_error = httpx.HTTPStatusError(
                    "retryable status",
                    request=response.request,
                    response=response,
                )
                LOGGER.warning("外部服务暂时失败 status=%s attempt=%s", response.status_code, attempt + 1)
                time.sleep(min(0.25 * (2**attempt), 1.0))
                continue
            try:
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPStatusError, ValueError) as exc:
                # 4xx 或无效 JSON 不是瞬时故障，重复请求没有意义。
                last_error = exc
                break
        if last_error is None:
            # 防止未来修改循环边界后在 Python -O 模式下静默跳过内部不变量检查。
            raise RuntimeError("外部 HTTP 请求未执行")
        raise last_error

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
