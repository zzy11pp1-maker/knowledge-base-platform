"""真实提供商连通与流式验收；只输出统计，绝不打印密钥或提供商错误正文。"""

import argparse
import asyncio
import json
import time

from app.core.config import Settings
from app.integrations.llm_client import CompatibleLLMClient


async def run(env_file):
    settings = Settings(_env_file=env_file)
    client = CompatibleLLMClient(settings)
    health = await client.health()
    if not health["ok"]:
        raise RuntimeError("提供商或配置模型不可用")
    started = time.monotonic()
    chunks = []
    first = None
    async for chunk in client.stream(
        [
            {"role": "system", "content": "仅根据资料回答，并使用给定来源标记。不要输出思考过程。"},
            {
                "role": "user",
                "content": "资料[S1]：验收测试项目代号为 Cedar-28。问题：项目代号是什么？一句话回答并引用[S1]。",
            },
        ]
    ):
        if first is None:
            first = time.monotonic() - started
        chunks.append(chunk)
    answer = "".join(chunks)
    checks = {
        "provider_available": True,
        "model_available": True,
        "multiple_deltas": len(chunks) > 1,
        "grounded_answer": "Cedar-28" in answer,
        "citation_label": "[S1]" in answer,
    }
    print(
        json.dumps(
            {
                "checks": checks,
                "delta_count": len(chunks),
                "first_delta_seconds": round(first, 3),
                "total_seconds": round(time.monotonic() - started, 3),
                "passed": sum(checks.values()),
                "failed": sum(not v for v in checks.values()),
                "skipped": 0,
            },
            ensure_ascii=False,
        )
    )
    return all(checks.values())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args()
    try:
        success = asyncio.run(run(args.env_file))
    except Exception as exc:
        print(json.dumps({"passed": 0, "failed": 1, "skipped": 0, "error_type": type(exc).__name__}))
        success = False
    raise SystemExit(0 if success else 1)
