"""验证远程模型客户端的协议、批处理和响应校验。"""

import unittest

import httpx

from app.core.exceptions import EmbeddingServiceError
from app.integrations.bge_client import BgeM3Client
from app.integrations.reranker_client import RerankerClient


class HttpClientsTest(unittest.TestCase):
    def test_rejects_negative_retry_count(self) -> None:
        with self.assertRaises(ValueError):
            BgeM3Client(base_url="http://test", max_retries=-1)

    def test_bge_batches_and_normalizes_sparse(self) -> None:
        calls: list[list[str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            texts = __import__("json").loads(request.content)["texts"]
            calls.append(texts)
            return httpx.Response(
                200,
                json={
                    "dense": [[1.0, 0.0, 0.0] for _ in texts],
                    "sparse": [{"8": 0.5, "2": 0.9} for _ in texts],
                },
            )

        http_client = httpx.Client(base_url="http://test", transport=httpx.MockTransport(handler))
        client = BgeM3Client(
            base_url="http://test",
            batch_size=2,
            dense_vector_size=3,
            max_retries=0,
            http_client=http_client,
        )
        result = client.embed(["a", "b", "c"])
        self.assertEqual([len(batch) for batch in calls], [2, 1])
        self.assertEqual(result.sparse[0].indices, [2, 8])
        self.assertEqual(len(result.dense), 3)
        http_client.close()

    def test_bge_rejects_wrong_dimension(self) -> None:
        transport = httpx.MockTransport(
            lambda _: httpx.Response(200, json={"dense": [[1.0]], "sparse": [{"1": 0.1}]})
        )
        http_client = httpx.Client(base_url="http://test", transport=transport)
        client = BgeM3Client(
            base_url="http://test",
            dense_vector_size=3,
            max_retries=0,
            http_client=http_client,
        )
        with self.assertRaises(EmbeddingServiceError):
            client.embed(["a"])
        http_client.close()

    def test_reranker_restores_input_order(self) -> None:
        transport = httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={"rerank_result": [{"index": 1, "score": 0.9}, {"index": 0, "score": 0.3}]},
            )
        )
        http_client = httpx.Client(base_url="http://test", transport=transport)
        client = RerankerClient(base_url="http://test", max_retries=0, http_client=http_client)
        self.assertEqual(client.rerank("问题", ["甲", "乙"]), [0.3, 0.9])
        http_client.close()

    def test_reranker_supports_sorted_text_response_with_duplicates(self) -> None:
        transport = httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "rerank_result": [
                        {"text": "相同", "score": 0.9},
                        {"text": "不同", "score": 0.5},
                        {"text": "相同", "score": 0.1},
                    ]
                },
            )
        )
        http_client = httpx.Client(base_url="http://test", transport=transport)
        client = RerankerClient(base_url="http://test", max_retries=0, http_client=http_client)
        self.assertEqual(client.rerank("问题", ["相同", "相同", "不同"]), [0.9, 0.1, 0.5])
        http_client.close()


if __name__ == "__main__":
    unittest.main()
