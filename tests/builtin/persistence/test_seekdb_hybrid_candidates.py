"""Real seekdb regression checks for the hybrid candidate budget and native plans."""

from __future__ import annotations

import hashlib
import math
from uuid import uuid4

import pytest
from sqlalchemy import event

from powercontext.builtin.artifacts.memory import EmbeddingProfile, MemoryEntryInput, MemorySearchRequest
from powercontext.builtin.inference import EmbeddingResult
from powercontext.builtin.persistence.memory_index import CompositeMemoryIndex
from powercontext.builtin.persistence.seekdb import SeekDBConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.tags import MemoryEntryTagTarget, TagFilter

PROFILE = EmbeddingProfile(
    profile_id="hybrid-regression", model="deterministic", dimension=384, distance="l2", normalization="unit"
)


def _vector(text: str) -> tuple[float, ...]:
    values = tuple(float(b) - 127.5 for b in hashlib.shake_256(text.encode()).digest(384))
    norm = math.sqrt(sum(value * value for value in values))
    return tuple(value / norm for value in values)


class _Embedding:
    profile = PROFILE

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        return EmbeddingResult(vectors=tuple(_vector(text) for text in texts))


@pytest.mark.asyncio
async def test_same_database_candidates_limits_and_filtered_native_plan(tmp_path, monkeypatch) -> None:
    async with open_builtin_contexts(
        BuiltinConfig(database=SeekDBConfig(path=tmp_path / "db")), embedding_model=_Embedding()
    ) as contexts:
        memory = (await contexts.get("regression")).artifacts.memory
        # The default service reserves a single Memory ID per scope. Create two
        # independent artifacts to exercise artifact filtering in the same scope.
        monkeypatch.setattr(memory, "_id_factory", lambda prefix: f"{prefix}_{uuid4().hex}")
        heads = []
        for suffix in ("first", "second"):
            head = await memory.remember(
                memory=None,
                entries=tuple(MemoryEntryInput(kind="fact", text=f"Alpha record {i} {suffix}.") for i in range(12)),
                mode="append",
            )
            assert head is not None
            heads.append(head)
        for entry in await memory.entries(heads[0]):
            target = MemoryEntryTagTarget(artifact_id=heads[0].artifact_id, entry_id=entry.entry_id)
            tags = await contexts.records.get_tags("regression", target)
            await contexts.records.replace_tags("regression", target, ("selected", "reviewed"), expected_etag=tags.etag)
        baseline = CompositeMemoryIndex(*contexts.index.indexes)
        for tag_filter in (
            None,
            TagFilter(tags=("selected", "reviewed")),
            TagFilter(tags=("selected", "absent"), match="any"),
        ):
            request = MemorySearchRequest(
                query="alpha",
                analyzed_query="alpha",
                memories=(heads[0].as_ref(),),
                candidate_limit=3,
                mode="hybrid",
                query_vector=_vector("alpha"),
                embedding_profile=PROFILE,
                tag_filter=tag_filter,
            )
            captured = []

            def record(_conn, _cursor, statement, parameters, _context, _executemany, captured=captured):
                if "AGAINST" in statement.upper() or "l2_distance(" in statement.lower():
                    captured.append((statement, parameters))

            async with contexts.database.transaction() as connection:
                expected = await baseline.search(connection, "regression", request)
                event.listen(connection.sync_connection, "before_cursor_execute", record)
                try:
                    actual = await contexts.index.search(connection, "regression", request)
                finally:
                    event.remove(connection.sync_connection, "before_cursor_execute", record)
                assert len(captured) == 1
                assert len(actual.fts) == len(actual.vector) == 3
                assert actual == expected
                assert actual == await contexts.index.search(connection, "regression", request)
                statement, parameters = captured[0]
                plan = "\n".join(
                    str(row[0]) for row in (await connection.exec_driver_sql("EXPLAIN " + statement, parameters)).all()
                )
                assert "TEXT RETRIEVAL SCAN" in plan
                assert "VECTOR INDEX" in plan
