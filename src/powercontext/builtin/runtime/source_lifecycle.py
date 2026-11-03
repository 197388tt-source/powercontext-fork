# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Persistent evidence dependency graphs and recoverable native state projections."""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator
from sqlalchemy import Column, MetaData, String, Table, Text, insert, select, update

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.runtime.models import GetMemoryEntryRequest
from powercontext.builtin.sources import validate_scope_id
from powercontext.sources import SourceRef


class SourceLifecycleError(ValueError):
    """Reject an invalid dependency registration or lifecycle operation."""

    def __init__(self, issue: str) -> None:
        super().__init__(issue.replace("-", " "))


class SourceLifecycleEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    node_id: StrictStr
    reason: StrictStr
    revision: StrictInt = Field(gt=0)
    sequence: StrictInt = Field(gt=0)
    available: StrictBool

    @field_validator("node_id", "reason")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise SourceLifecycleError("must-contain-non-whitespace-text")
        return value


_metadata = MetaData()
_graphs = Table(
    "source_lifecycle_graphs",
    _metadata,
    Column("scope_id", String(255), primary_key=True),
    Column("graph", Text, nullable=False),
)


class SourceLifecycles:
    def __init__(self, runtime, database):
        self.runtime = runtime
        self.database = database
        self.topic_search = runtime._topic_memory_search

    async def initialize(self):
        async with self.database.transaction() as connection:
            await connection.run_sync(_metadata.create_all)

    def for_scope(self, scope_id):
        return ScopedSourceLifecycle(self, validate_scope_id(scope_id))

    async def load(self, scope_id):
        async with self.database.transaction() as connection:
            value = await connection.scalar(select(_graphs.c.graph).where(_graphs.c.scope_id == scope_id))
        return {} if value is None else json.loads(value)

    async def save(self, scope_id, graph):
        async with self.database.transaction() as connection:
            exists = await connection.scalar(select(_graphs.c.scope_id).where(_graphs.c.scope_id == scope_id))
            values = {"graph": json.dumps(graph, ensure_ascii=False)}
            if exists is None:
                await connection.execute(insert(_graphs).values(scope_id=scope_id, **values))
            else:
                await connection.execute(update(_graphs).where(_graphs.c.scope_id == scope_id).values(**values))

    async def search_topics(self, scope_id, *args, **kwargs):
        result = await self.topic_search(scope_id, *args, **kwargs)
        return await self.filter_topics(scope_id, result)

    async def filter_topics(self, scope_id, result):
        graph = await self.load(scope_id)
        unavailable = {
            n["reference"]["artifact_id"] for n in graph.values() if n["kind"] == "topic" and not n["available"]
        }
        return result.model_copy(
            update={"hits": tuple(hit for hit in result.hits if hit.artifact_ref.artifact_id not in unavailable)}
        )


class ScopedSourceLifecycle:
    def __init__(self, owner, scope_id):
        self.owner = owner
        self.runtime = owner.runtime
        self.scope_id = scope_id

    async def register_source(self, node_id, source_ref, *, revision):
        if type(revision) is not int or revision < 1:
            raise SourceLifecycleError("revision-must-be-a-positive-integer")
        await self._register(node_id, source_ref, "source", (), None, revision)

    async def register_memory(self, node_id, citation, *, parents, policy):
        await self._register(node_id, citation, "memory", parents, policy, None)

    async def register_topic(self, node_id, artifact_ref, *, parents, policy):
        await self._register(node_id, artifact_ref, "topic", parents, policy, None)

    async def _register(self, node_id, reference, kind, parents, policy, revision):  # noqa: C901
        if not isinstance(node_id, str) or not node_id.strip():
            raise SourceLifecycleError("node_id-must-be-nonblank")
        expected = {"source": SourceRef, "memory": MemoryCitation, "topic": ArtifactRef}[kind]
        if not isinstance(reference, expected):
            raise SourceLifecycleError("invalid-reference-type")
        graph = await self.owner.load(self.scope_id)
        parents = list(parents)
        binding = {
            "kind": kind,
            "reference": reference.model_dump(mode="json"),
            "parents": parents,
            "policy": policy,
            "revision": revision,
        }
        if node_id in graph:
            if any(graph[node_id][key] != value for key, value in binding.items()):
                raise SourceLifecycleError("conflicting-node-binding")
            await self._project(graph)
            return
        if kind != "source" and (
            policy not in ("all", "any")
            or not parents
            or any(not isinstance(p, str) or p not in graph or p == node_id for p in parents)
            or len(set(parents)) != len(parents)
        ):
            raise SourceLifecycleError("invalid-dependency-graph")
        if kind == "topic" and reference.family != "topic-memory":
            raise SourceLifecycleError("not-a-Topic-Memory-reference")
        try:
            if kind == "source":
                await self.runtime.records.for_scope(self.scope_id).get_source(
                    reference.source_type, reference.source_id
                )
            elif kind == "memory":
                await self.runtime.memory.for_scope(self.scope_id).get(GetMemoryEntryRequest(citation=reference))
            else:
                await self.runtime.records.for_scope(self.scope_id).get_artifact_revision(
                    reference.family, reference.artifact_id, reference.revision
                )
        except SourceLifecycleError:
            raise
        except Exception as error:
            raise SourceLifecycleError("reference-is-not-readable") from error
        graph[node_id] = dict(**binding, available=True, event=None)
        self._evaluate(graph)
        # Persist intent before native writes: a retry can finish a partial projection.
        await self.owner.save(self.scope_id, graph)
        await self._project(graph)

    @staticmethod
    def _evaluate(graph):
        # Parents must preexist, so insertion order is a topological ordering.
        for node in graph.values():
            if node["kind"] != "source":
                states = [graph[parent]["available"] for parent in node["parents"]]
                node["available"] = all(states) if node["policy"] == "all" else any(states)

    async def apply(self, event):
        event = SourceLifecycleEvent.model_validate(event)
        graph = await self.owner.load(self.scope_id)
        node = graph.get(event.node_id)
        if node is None or node["kind"] != "source" or node["revision"] != event.revision:
            raise SourceLifecycleError("unknown-Source-or-lifecycle-revision-mismatch")
        previous = node["event"]
        payload = event.model_dump(mode="json")
        if previous is not None and (
            event.sequence < previous["sequence"] or (event.sequence == previous["sequence"] and payload != previous)
        ):
            raise SourceLifecycleError("stale-or-conflicting-event-sequence")
        node["event"] = payload
        node["available"] = event.available
        self._evaluate(graph)
        await self.owner.save(self.scope_id, graph)
        await self._project(graph)
        return sorted(key for key, value in graph.items() if value["available"])

    async def _project(self, graph):
        # Compare actual native state on every attempt, including event replays.
        # Native transitions preserve entry body versions and no-op on equal state.
        async with self.runtime._context(self.scope_id) as context, self.runtime._locked(self.scope_id):
            service = context.artifacts.memory
            memories = [n for n in graph.values() if n["kind"] == "memory"]
            if not memories:
                return
            current = await service.head(context.artifacts.memory_artifact_id)
            for node in memories:
                citation = MemoryCitation.model_validate(node["reference"])
                entries = await service.entries(current)
                entry = next(e for e in entries if e.entry_id == citation.entry_id)
                if entry.entry_version_id != citation.entry_version_id:
                    raise SourceLifecycleError("registered-Memory-body-changed")
                operation = service.reactivate if node["available"] else service.forget
                current = await operation(current, entries=(entry,), reason="source lifecycle")

    async def resolve(self, node_id, *, purpose):
        graph = await self.owner.load(self.scope_id)
        node = graph.get(node_id)
        if node is None or purpose not in ("generation", "audit"):
            raise SourceLifecycleError("unknown-node-or-purpose")
        if purpose == "generation" and not node["available"]:
            raise SourceLifecycleError("node-is-unavailable-for-generation")
        model = {"source": SourceRef, "memory": MemoryCitation, "topic": ArtifactRef}[node["kind"]]
        return model.model_validate(node["reference"])
