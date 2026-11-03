"""HTTP scoring adapter with identity-preserving global Memory ranking."""

from __future__ import annotations

import math
from typing import cast

import httpx

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision
from powercontext.builtin.inference import InferenceUsage


class HTTPMemoryReranker:
    """Score every candidate in bounded batches and select globally by score."""

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = 32,
        policy_id: str = "http-cross-encoder-v1",
    ) -> None:
        if type(batch_size) is not int or batch_size <= 0:
            raise ValueError("batch_size must be a positive integer")  # noqa: TRY003
        self.policy_id = policy_id
        self._endpoint = endpoint
        self._client = client
        self._batch_size = batch_size

    async def rerank(self, query: str, candidates: tuple[MemoryHit, ...], limit: int, /) -> MemoryRerankDecision:
        """Return original one-based ranks; propagate HTTP and transport errors."""
        if not candidates or type(limit) is not int or not 1 <= limit <= len(candidates):
            raise ValueError("require nonempty candidates and 1 <= limit <= candidate count")  # noqa: TRY003
        scores: dict[int, float | int] = {}
        requests = 0
        for start in range(0, len(candidates), self._batch_size):
            batch = [
                {"rank": index + 1, "text": candidates[index].text}
                for index in range(start, min(start + self._batch_size, len(candidates)))
            ]
            response = await self._client.post(self._endpoint, json={"query": query, "candidates": batch})
            requests += 1
            response.raise_for_status()
            payload = response.json()
            expected = set(range(start + 1, start + len(batch) + 1))
            scores.update(_validate_scores(payload, expected))
        selected = sorted(scores, key=lambda rank: (-scores[rank], rank))[:limit]
        return MemoryRerankDecision(selected_ranks=tuple(selected), usage=InferenceUsage(requests=requests))


def _validate_scores(payload: object, expected: set[int]) -> dict[int, float | int]:
    if not isinstance(payload, dict) or set(payload) != {"scores"}:
        raise ValueError("response must contain exactly a scores array")  # noqa: TRY003
    records = cast("dict[str, object]", payload)["scores"]
    if not isinstance(records, list):
        raise ValueError("scores must be an array")  # noqa: TRY003, TRY004
    scores: dict[int, float | int] = {}
    for item in records:
        if not isinstance(item, dict) or set(item) != {"rank", "score"}:
            raise ValueError("each score must contain exactly rank and score")  # noqa: TRY003
        record = cast("dict[str, object]", item)
        rank, score = record["rank"], record["score"]
        if type(rank) is not int or rank not in expected or rank in scores:
            raise ValueError("response ranks must cover this batch exactly once")  # noqa: TRY003
        if type(score) not in (int, float):
            raise ValueError("score must be a finite number")  # noqa: TRY003
        score = cast("float | int", score)
        try:
            finite = math.isfinite(score)
        except OverflowError as error:
            raise ValueError("score must be finite") from error  # noqa: TRY003
        if not finite:
            raise ValueError("score must be finite")  # noqa: TRY003
        scores[rank] = score
    if set(scores) != expected:
        raise ValueError("response is missing candidate ranks")  # noqa: TRY003
    return scores


__all__ = ["HTTPMemoryReranker"]
