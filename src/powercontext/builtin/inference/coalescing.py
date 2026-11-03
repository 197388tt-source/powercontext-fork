"""Coalesce concurrent document embeddings and cache successful vectors."""

from __future__ import annotations

import asyncio
import math
from collections import OrderedDict
from dataclasses import dataclass

from powercontext.builtin.inference.models import EmbeddingResult, EmbeddingVector, InferenceUsage
from powercontext.builtin.inference.protocols import EmbeddingModel


@dataclass(eq=False)
class _Batch:
    usage: InferenceUsage


@dataclass
class _Outcome:
    vector: EmbeddingVector = ()
    batch: _Batch | None = None
    error: BaseException | None = None


class CoalescingEmbeddingModel:
    """Share pending embeddings on one event loop, with a bounded successful-result LRU."""

    def __init__(
        self,
        model: EmbeddingModel,
        *,
        max_batch_size: int = 64,
        coalesce_ms: float = 5.0,
        cache_size: int = 1024,
    ) -> None:
        if not isinstance(max_batch_size, int) or max_batch_size < 1:
            message = "max_batch_size must be a positive integer"
            raise ValueError(message)
        if not isinstance(cache_size, int) or cache_size < 0:
            message = "cache_size must be a nonnegative integer"
            raise ValueError(message)
        if not math.isfinite(coalesce_ms) or coalesce_ms < 0:
            message = "coalesce_ms must be finite and nonnegative"
            raise ValueError(message)
        self.profile = model.profile
        self._model = model
        self._max_batch_size = max_batch_size
        self._delay = coalesce_ms / 1000
        self._cache_size = cache_size
        self._cache: OrderedDict[str, EmbeddingVector] = OrderedDict()
        self._pending: dict[str, asyncio.Future[_Outcome]] = {}
        self._queued: list[str] = []
        self._flush_task: asyncio.Task[None] | None = None
        self._workers: set[asyncio.Task[None]] = set()

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        """Return vectors in input order and usage for each distinct batch awaited."""
        positions: list[EmbeddingVector | asyncio.Future[_Outcome]] = []
        for text in texts:
            if text in self._cache:
                self._cache.move_to_end(text)
                positions.append(self._cache[text])
            else:
                future = self._pending.get(text)
                if future is None:
                    future = asyncio.get_running_loop().create_future()
                    self._pending[text] = future
                    self._queued.append(text)
                positions.append(future)
        if self._queued and self._flush_task is None:
            self._flush_task = asyncio.create_task(self._flush())
        vectors: list[EmbeddingVector] = []
        batches: set[_Batch] = set()
        for position in positions:
            if isinstance(position, asyncio.Future):
                outcome = await asyncio.shield(position)
                if outcome.error is not None:
                    raise outcome.error
                vectors.append(outcome.vector)
                if outcome.batch is not None:
                    batches.add(outcome.batch)
            else:
                vectors.append(position)
        usages = [batch.usage for batch in batches]
        return EmbeddingResult(
            vectors=tuple(vectors),
            usage=InferenceUsage(
                requests=sum(usage.requests for usage in usages),
                input_tokens=self._tokens(usages, "input_tokens"),
                output_tokens=self._tokens(usages, "output_tokens"),
            ),
        )

    @staticmethod
    def _tokens(usages: list[InferenceUsage], field: str) -> int | None:
        values = [getattr(usage, field) for usage in usages]
        known = [value for value in values if value is not None]
        return sum(known) if known else None

    async def _flush(self) -> None:
        try:
            await asyncio.sleep(self._delay)
            queued, self._queued = self._queued, []
            for offset in range(0, len(queued), self._max_batch_size):
                texts = tuple(queued[offset : offset + self._max_batch_size])
                task = asyncio.create_task(self._run_batch(texts))
                self._workers.add(task)
                task.add_done_callback(self._workers.discard)
        finally:
            self._flush_task = None

    async def _run_batch(self, texts: tuple[str, ...]) -> None:
        try:
            result = await self._model.embed(texts)
            if len(result.vectors) != len(texts):
                message = "Embedding vector count does not match text count"
                raise ValueError(message)  # noqa: TRY301 - validate before resolving any waiter
        except BaseException as error:
            # Resolve outcomes as values: abandoned/cancelled callers cannot leave
            # unobserved Future exceptions, and other batches continue independently.
            for text in texts:
                self._pending.pop(text).set_result(_Outcome(error=error))
            return
        batch = _Batch(result.usage)
        for text, vector in zip(texts, result.vectors, strict=True):
            if self._cache_size:
                self._cache[text] = vector
                self._cache.move_to_end(text)
                while len(self._cache) > self._cache_size:
                    self._cache.popitem(last=False)
            self._pending.pop(text).set_result(_Outcome(vector=vector, batch=batch))
