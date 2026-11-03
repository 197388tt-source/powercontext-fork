"""Lifecycle recovery and identity regressions through runtime interfaces."""

import asyncio

import pytest

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.artifacts.memory.service import MemoryService
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, CaptureSource, RememberMemoryRequest, open_builtin_runtime
from powercontext.builtin.runtime.source_lifecycle import SourceLifecycleError, SourceLifecycleEvent
from powercontext.builtin.scope import ScopeDraft


def test_interrupted_projection_retries_after_restart_without_extra_revisions(tmp_path, monkeypatch):
    async def run():
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'retry.db'}"))
        event = SourceLifecycleEvent(node_id="root", revision=1, sequence=1, available=False, reason="withdraw")
        async with open_builtin_runtime(config) as runtime:
            scope = (
                await runtime.scopes.create(ScopeDraft(title="retry", summary="retry", idempotency_key="retry"))
            ).scope_id
            service = runtime.lifecycle.for_scope(scope)
            source = await runtime.sources.for_scope(scope).capture(
                CaptureSource(source_id="root", content="evidence", metadata={})
            )
            await service.register_source("root", source.source_ref, revision=1)
            for number in range(2):
                result = await runtime.memory.for_scope(scope).remember(
                    RememberMemoryRequest(entries=(MemoryEntryInput(kind="fact", text=f"fact{number}"),))
                )
                await service.register_memory(f"fact{number}", result.entry.citation, parents=("root",), policy="all")
            original = MemoryService.forget
            calls = 0

            async def failing(self, *args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError  # Simulate a write failure after the first committed state transition.
                return await original(self, *args, **kwargs)

            with monkeypatch.context() as patch:
                patch.setattr(MemoryService, "forget", failing)
                with pytest.raises(OSError):
                    await service.apply(event)
        async with open_builtin_runtime(config) as runtime:
            service = runtime.lifecycle.for_scope(scope)
            assert await service.apply(event) == []
            page = await runtime.memory.for_scope(scope).list(include_inactive=True)
            assert all(entry.state != "active" for entry in page.entries)
            before = page.memory_ref
            assert await service.apply(event) == []
            assert (await runtime.memory.for_scope(scope).list(include_inactive=True)).memory_ref == before
            with pytest.raises(SourceLifecycleError):
                await service.apply(event.model_copy(update={"reason": "conflict"}))

    asyncio.run(run())


def test_scopes_and_registration_validation():
    async def run():
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            services = []
            for title in ("first", "second"):
                scope = (
                    await runtime.scopes.create(ScopeDraft(title=title, summary=title, idempotency_key=title))
                ).scope_id
                service = runtime.lifecycle.for_scope(scope)
                source = await runtime.sources.for_scope(scope).capture(
                    CaptureSource(source_id="root", content=title, metadata={})
                )
                await service.register_source("root", source.source_ref, revision=1)
                await service.register_source("root", source.source_ref, revision=1)
                result = await runtime.memory.for_scope(scope).remember(
                    RememberMemoryRequest(entries=(MemoryEntryInput(kind="fact", text=title),))
                )
                for parents in ((), ("missing",), ("root", "root"), ("fact",)):
                    with pytest.raises(SourceLifecycleError):
                        await service.register_memory("fact", result.entry.citation, parents=parents, policy="all")
                await service.register_memory("fact", result.entry.citation, parents=("root",), policy="all")
                services.append(service)
            await services[0].apply(
                SourceLifecycleEvent(node_id="root", revision=1, sequence=1, available=False, reason="withdraw")
            )
            with pytest.raises(SourceLifecycleError):
                await services[0].resolve("fact", purpose="generation")
            assert await services[1].resolve("fact", purpose="generation")
            with pytest.raises(SourceLifecycleError):
                await services[1].resolve("fact", purpose="unknown")

    asyncio.run(run())
