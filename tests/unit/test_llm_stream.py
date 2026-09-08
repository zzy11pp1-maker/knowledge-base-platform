"""仅单元测试使用 HTTP MockTransport，覆盖提供商协议和取消清理。"""

import json
import unittest
from contextlib import aclosing

import httpx

from app.core.config import Settings
from app.core.exceptions import ConfigurationError
from app.integrations.llm_client import CompatibleLLMClient, LLMServiceError


def frame(content=None, finish=None):
    return (
        "data: "
        + json.dumps({"choices": [{"index": 0, "delta": {"content": content}, "finish_reason": finish}]})
        + "\n\n"
    )


class TrackingStream(httpx.AsyncByteStream):
    def __init__(self, content):
        self.content = content
        self.closed = False

    async def __aiter__(self):
        for part in self.content:
            yield part.encode()

    async def aclose(self):
        self.closed = True


class LLMStreamTest(unittest.IsolatedAsyncioTestCase):
    def client(self, content, status=200, content_type="text/event-stream"):
        self.tracking = TrackingStream(content)
        settings = Settings(
            _env_file=None,
            llm_base_url="https://unit.invalid/v1",
            llm_model="unit-only",
            llm_api_key="unit-secret",
        )
        return CompatibleLLMClient(
            settings,
            httpx.MockTransport(
                lambda request: httpx.Response(
                    status, headers={"content-type": content_type}, stream=self.tracking
                )
            ),
        )

    async def collect(self, client):
        return "".join([x async for x in client.stream([{"role": "user", "content": "test"}])])

    async def test_stream_normal_and_closed(self):
        client = self.client([frame("hello"), frame(finish="stop"), "data: [DONE]\n\n"])
        self.assertEqual(await self.collect(client), "hello")
        self.assertTrue(self.tracking.closed)

    async def test_truncated_stream_rejected(self):
        with self.assertRaises(LLMServiceError):
            await self.collect(self.client([frame("partial")]))
        self.assertTrue(self.tracking.closed)

    async def test_token_budget_exhaustion_rejected(self):
        with self.assertRaises(LLMServiceError):
            await self.collect(self.client([frame("partial"), frame(finish="length"), "data: [DONE]\n\n"]))

    async def test_malformed_json_rejected(self):
        with self.assertRaises(LLMServiceError):
            await self.collect(self.client(["data: broken\n\n"]))

    async def test_status_body_not_exposed(self):
        with self.assertRaises(LLMServiceError) as raised:
            await self.collect(self.client(["provider-secret-body"], status=401))
        self.assertNotIn("provider-secret-body", str(raised.exception))

    async def test_content_type_rejected(self):
        with self.assertRaises(LLMServiceError):
            await self.collect(self.client(["{}"], content_type="application/json"))

    async def test_reasoning_is_not_output(self):
        reasoning = 'data: {"choices":[{"delta":{"reasoning_content":"hidden"}}]}\n\n'
        client = self.client([reasoning, frame("answer"), frame(finish="stop"), "data: [DONE]\n\n"])
        self.assertEqual(await self.collect(client), "answer")

    async def test_consumer_close_releases_http(self):
        client = self.client([frame("first"), frame("second"), frame(finish="stop"), "data: [DONE]\n\n"])
        async with aclosing(client.stream([])) as stream:
            self.assertEqual(await anext(stream), "first")
        self.assertTrue(self.tracking.closed)

    async def test_no_cleartext_remote_key(self):
        with self.assertRaises(ConfigurationError):
            CompatibleLLMClient(
                Settings(
                    _env_file=None,
                    llm_base_url="http://remote.invalid",
                    llm_model="unit",
                    llm_api_key="unit-secret",
                )
            )

    async def test_missing_key(self):
        with self.assertRaises(ConfigurationError):
            CompatibleLLMClient(
                Settings(_env_file=None, llm_model="unit", llm_base_url="https://unit.invalid")
            )
