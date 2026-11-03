"""Independent 40-round QMSum measurement; not the organizer's hidden workload.

Run: PYTHONPATH=src .venv/bin/python competition/measure_incremental.py /path/ES2004a.json
"""

import asyncio
import gzip
import hashlib
import json
import sys
from pathlib import Path

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.runtime import BuiltinConfig, CaptureSource, PrepareContextRequest, open_builtin_runtime
from powercontext.builtin.runtime.context_pack import ContextPack, ContextPackDelta, apply_context_pack_delta
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.sources import ContentSource


class Pipeline:
    async def extract(self, request):
        return tuple(
            MemoryEntryInput(kind="conversation-turn", text=s.content, sources=(s,))
            for s in request.sources
            if isinstance(s, ContentSource)
        )


async def main():
    raw = Path(sys.argv[1]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != "911e56db0d8482f1e730bae0a8fcf0ab0095c94abb000ba0516e975fa6e48b31":
        raise ValueError("QMSum source hash mismatch")
    turns = [
        f"{t['speaker']}: {t['content']}"
        for t in json.loads(raw)["meeting_transcripts"]
        if 120 <= len(t["content"].encode()) <= 2000
        and any(word in t["content"].lower() for word in ("remote", "design", "control"))
    ][:9]
    if len(turns) != 9:
        raise ValueError("Not enough real meeting turns")
    totals = dict(raw_full_bytes=0, incremental_bytes=0, full_pack_bytes=0, gzip_bytes=0)
    distinct = set()
    rows = []
    base = None
    async with open_builtin_runtime(BuiltinConfig(), candidate_pipeline=Pipeline()) as runtime:
        scope = await runtime.scopes.create(
            ScopeDraft(
                title="QMSum measurement", summary="Independent benchmark", idempotency_key="incremental-benchmark"
            )
        )
        sources = runtime.sources.for_scope(scope.scope_id)
        memory = runtime.memory.for_scope(scope.scope_id)
        context = runtime.context.for_scope(scope.scope_id)

        async def capture(index):
            await sources.capture(CaptureSource(source_id=f"turn-{index}", content=turns[index], metadata={}))
            while (await memory.flush()).processed:
                pass

        for index in range(8):
            await capture(index)
        for round_index in range(40):
            if round_index == 10:
                await capture(8)
            # Explicit measurement choices, never used by product code.
            query = ("remote", "control", "design", "remote")[round_index // 10]
            request = PrepareContextRequest(query=query, max_bytes=8000)
            expected = await context.prepare(request)
            body = (expected.content or "").encode()
            if not body:
                raise ValueError(f"Empty context in round {round_index}")
            transmitted = await context.prepare_incremental(request, base_pack=base)
            wire = transmitted.to_bytes()
            rebuilt = (
                ContextPack.from_bytes(wire)
                if base is None
                else apply_context_pack_delta(base, ContextPackDelta.from_bytes(wire))
            )
            assert b"".join(rebuilt.items) == body
            base = rebuilt
            distinct.add(body)
            full = len(ContextPack.from_content(expected.content).to_bytes())
            gz = len(gzip.compress(body, compresslevel=9, mtime=0))
            for key, value in zip(totals, (len(body), len(wire), full, gz), strict=True):
                totals[key] += value
            rows.append(dict(round=round_index + 1, query=query, raw=len(body), wire=len(wire), full=full, gzip=gz))
    result = dict(
        description="Independent workload; not official hidden acceptance",
        rounds=40,
        initial_real_turns=8,
        distinct_contexts=len(distinct),
        **totals,
        raw_saving=1 - totals["incremental_bytes"] / totals["raw_full_bytes"],
        full_pack_saving=1 - totals["incremental_bytes"] / totals["full_pack_bytes"],
        gzip_saving=1 - totals["incremental_bytes"] / totals["gzip_bytes"],
        rows=rows,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    assert len(distinct) >= 4
    assert result["raw_saving"] >= 0.45


asyncio.run(main())
