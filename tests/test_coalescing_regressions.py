"""Additional public-contract regressions for concurrent embeddings."""

import asyncio

import pytest

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.inference import EmbeddingResult, InferenceUsage
from powercontext.builtin.inference.coalescing import CoalescingEmbeddingModel


def test_late_waiter_without_cache_and_multibatch_usage():
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        calls = []

        class Provider:
            profile = EmbeddingProfile(profile_id="test", model="offline", dimension=1)

            async def embed(self, texts):
                calls.append(texts)
                started.set()
                await release.wait()
                return EmbeddingResult(
                    vectors=tuple((float(len(text)),) for text in texts),
                    usage=InferenceUsage(requests=2, input_tokens=7, output_tokens=3),
                )

        model = CoalescingEmbeddingModel(Provider(), max_batch_size=1, coalesce_ms=0, cache_size=0)
        first = asyncio.create_task(model.embed(("a", "bb", "a")))
        await started.wait()
        late = asyncio.create_task(model.embed(("a", "a")))
        await asyncio.sleep(0)
        release.set()
        a, b = await asyncio.gather(first, late)
        assert a.vectors == ((1.0,), (2.0,), (1.0,))
        assert a.usage == InferenceUsage(requests=4, input_tokens=14, output_tokens=6)
        assert b.usage == InferenceUsage(requests=2, input_tokens=7, output_tokens=3)
        assert len(calls) == 2
        await model.embed(("a",))
        assert len(calls) == 3

    asyncio.run(scenario())


def test_failed_batch_does_not_poison_independent_work():
    async def scenario():
        class Provider:
            profile = EmbeddingProfile(profile_id="test", model="offline", dimension=1)

            async def embed(self, texts):
                if texts == ("bad",):
                    message = "batch failed"
                    raise RuntimeError(message)
                return EmbeddingResult(vectors=((1.0,),))

        model = CoalescingEmbeddingModel(Provider(), max_batch_size=1, coalesce_ms=0)
        failed, good = await asyncio.gather(model.embed(("bad",)), model.embed(("good",)), return_exceptions=True)
        assert isinstance(failed, RuntimeError)
        assert good.vectors == ((1.0,),)
        with pytest.raises(RuntimeError):
            await model.embed(("bad",))
        assert (await model.embed(("good",))).usage.requests == 0

    asyncio.run(scenario())
