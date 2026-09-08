"""OpenAI-compatible HTTP 适配器：真实 SSE 增量读取，密钥和正文绝不写日志。"""

import asyncio
import json
from urllib.parse import urlsplit

import httpx

from app.core.config import get_settings
from app.services.model_config import runtime_settings
from app.core.exceptions import ConfigurationError, PlatformError


class LLMServiceError(PlatformError):
    code = "llm_service_error"
    status_code = 502


class CompatibleLLMClient:
    def __init__(self, settings=None, transport=None):
        self.settings = settings or runtime_settings()
        self.transport = transport
        self.last_usage = {"prompt_tokens": 0, "completion_tokens": 0, "available": False}
        url = self.settings.llm_base_url
        if not url or not self.settings.llm_model or not self.settings.llm_api_key:
            raise ConfigurationError("缺少 LLM_BASE_URL、LLM_MODEL 或 LLM_API_KEY")
        parsed = urlsplit(url)
        if (
            parsed.scheme not in ("https", "http")
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ConfigurationError("LLM_BASE_URL 不是合法服务地址")
        if parsed.scheme != "https" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ConfigurationError("非本机 LLM 服务必须使用 HTTPS")

    def _client(self):
        return httpx.AsyncClient(
            base_url=self.settings.llm_base_url.rstrip("/") + "/",
            headers={"Authorization": "Bearer " + self.settings.llm_api_key.get_secret_value()},
            timeout=httpx.Timeout(self.settings.llm_timeout, connect=10),
            follow_redirects=False,
            trust_env=False,
            transport=self.transport,
        )

    async def health(self):
        try:
            async with self._client() as client:
                response = await client.get("models")
                if response.status_code != 200:
                    return {"ok": False, "detail": "provider_http_" + str(response.status_code)}
                exists = any(x.get("id") == self.settings.llm_model for x in response.json().get("data", []))
                return {"ok": exists, "model_available": exists}
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            return {"ok": False, "detail": "provider_unavailable"}

    async def stream(self, messages):
        """消费提供商 SSE；不暴露 reasoning_content，不回显异常响应正文。"""
        completed = finished = received = False
        self.last_usage = {"prompt_tokens": 0, "completion_tokens": 0, "available": False}
        payload = {
            "model": self.settings.llm_model,
            "messages": messages,
            "stream": True,
            "max_tokens": self.settings.llm_max_tokens,
        }
        try:
            async with asyncio.timeout(self.settings.llm_timeout):
                async with self._client() as client:
                    async with client.stream("POST", "chat/completions", json=payload) as response:
                        if response.status_code != 200:
                            raise LLMServiceError("LLM 服务拒绝请求，HTTP " + str(response.status_code))
                        if "text/event-stream" not in response.headers.get("content-type", ""):
                            raise LLMServiceError("LLM 未返回 SSE 协议")
                        lines = []
                        async for line in response.aiter_lines():
                            if len(line) > 1_000_000:
                                raise LLMServiceError("LLM 事件过大")
                            if line.startswith("data:"):
                                lines.append(line[5:].lstrip(" "))
                            elif line == "" and lines:
                                data = "\n".join(lines)
                                lines.clear()
                                if data == "[DONE]":
                                    completed = True
                                    break
                                event = json.loads(data)
                                usage = event.get("usage")
                                if isinstance(usage, dict):
                                    self.last_usage = {
                                        "prompt_tokens": int(usage.get("prompt_tokens", 0)),
                                        "completion_tokens": int(usage.get("completion_tokens", 0)),
                                        "available": True,
                                    }
                                if "error" in event:
                                    raise LLMServiceError("LLM 流返回错误")
                                for choice in event.get("choices", []):
                                    if choice.get("index", 0) != 0:
                                        continue
                                    reason = choice.get("finish_reason")
                                    if reason is not None:
                                        if reason != "stop":
                                            raise LLMServiceError("LLM 输出未正常完成")
                                        finished = True
                                    content = choice.get("delta", {}).get("content")
                                    if content:
                                        if not isinstance(content, str):
                                            raise LLMServiceError("LLM 增量格式错误")
                                        received = True
                                        yield content
            if not completed or not finished or not received:
                raise LLMServiceError("LLM 流提前结束或内容为空")
        except (httpx.HTTPError, TimeoutError) as exc:
            raise LLMServiceError("LLM 请求超时或网络中断") from exc
        except (ValueError, TypeError, AttributeError) as exc:
            raise LLMServiceError("LLM 响应格式错误") from exc
