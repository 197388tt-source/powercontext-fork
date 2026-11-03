"""Additional HTTP reranking boundary and cross-batch regressions."""

import asyncio
import json

import httpx
import pytest

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory.http_reranker import HTTPMemoryReranker
from powercontext.builtin.artifacts.memory.models import MemoryHit


def _hits(count):
    return tuple(
        MemoryHit(
            memory_ref=ArtifactRef(family="memory", artifact_id="m", revision=1),
            entry_id=f"e{i}",
            entry_version_id=f"v{i}",
            text=f"text {i}",
            score=1.0,
            matched_by=("fts",),
        )
        for i in range(1, count + 1)
    )


def test_later_batches_win_and_ties_use_global_rank():
    seen = []

    def handler(request):
        body = json.loads(request.content)
        ranks = [c["rank"] for c in body["candidates"]]
        seen.extend(ranks)
        return httpx.Response(200, json={"scores": [{"rank": r, "score": 0 if r < 8 else 1} for r in reversed(ranks)]})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await HTTPMemoryReranker("https://test.invalid", client=client, batch_size=7).rerank(
                "q",
                _hits(173),
                10,
            )
            assert result.selected_ranks == tuple(range(8, 18))
            assert result.usage.requests == 25
            assert seen == list(range(1, 174))
            assert not client.is_closed

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "payload",
    [
        {},
        [],
        {"scores": {}},
        {"scores": []},
        {"scores": [{"rank": True, "score": 1}]},
        {"scores": [{"rank": 1.0, "score": 1}]},
        {"scores": [{"rank": 1, "score": True}]},
        {"scores": [{"rank": 1, "score": "0.9"}]},
        {"scores": [{"rank": 1, "score": None}]},
    ],
)
def test_strict_response_types(payload):
    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
        ) as client:
            with pytest.raises(ValueError):
                await HTTPMemoryReranker("https://test.invalid", client=client).rerank("q", _hits(1), 1)

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [302, 400, 500])
def test_http_errors(status):
    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(status, json={"scores": []}))
        ) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await HTTPMemoryReranker("https://test.invalid", client=client).rerank("q", _hits(1), 1)

    asyncio.run(scenario())


def test_invalid_json():
    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"not json"))
        ) as client:
            with pytest.raises(ValueError):
                await HTTPMemoryReranker("https://test.invalid", client=client).rerank("q", _hits(1), 1)

    asyncio.run(scenario())
