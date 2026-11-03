# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at https://www.apache.org/licenses/LICENSE-2.0
"""Offline query-sensitive, byte-bounded selection of literal Memory evidence."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from powercontext.artifacts import ArtifactAddress
from powercontext.builtin.runtime.errors import PreparedContextInvariantError
from powercontext.builtin.runtime.models import PrepareContextRequest, PreparedContext
from powercontext.builtin.runtime.prepared_context import (
    MemoryEntryAddress,
    PreparedContextBuild,
    PreparedMemoryCandidates,
    PreparedMemoryFragment,
)
from powercontext.builtin.runtime.prepared_text import TRUST_POLICY

_STOP = frozenset([
    "a",
    "an",
    "the",
    "of",
    "to",
    "in",
    "on",
    "at",
    "by",
    "for",
    "from",
    "with",
    "as",
    "and",
    "or",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "this",
    "that",
    "these",
    "those",
    "it",
    "its",
    "we",
    "our",
    "their",
    "they",
    "has",
    "have",
    "had",
    "do",
    "does",
    "did",
    "can",
    "could",
    "would",
    "should",
    "may",
    "might",
    "than",
    "which",
    "who",
    "whom",
    "how",
    "whether",
    "not",
    "no",
    "into",
    "through",
    "using",
])
_WORD = re.compile(r"[a-z]+|\d+|[\u3400-\u9fff]", re.IGNORECASE)
_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])|(?<=[。！？])|\n+")  # noqa: RUF001


def _terms(text: str) -> Counter[str]:
    result: Counter[str] = Counter()
    for word in _WORD.findall(text.lower()):
        if word in _STOP:
            continue
        # Light morphological normalization, used only for ranking, never for output.
        for suffix, replacement in (("ies", "y"), ("ing", ""), ("ed", ""), ("s", "")):
            if len(word) > len(suffix) + 3 and word.endswith(suffix):
                word = word[: -len(suffix)] + replacement
                break
        result[word] += 1
    return result


@dataclass(frozen=True)
class _Sentence:
    document: int
    start: int
    end: int
    score: float


def _render(fragments: Sequence[PreparedMemoryFragment]) -> str:
    items = []
    for fragment in fragments:
        address = fragment.address
        items.append({
            "citation": {
                "scope_id": address.memory.scope_id,
                "memory_ref": address.memory.artifact.model_dump(mode="json"),
                "entry_id": address.entry_id,
                "entry_version_id": address.entry_version_id,
            },
            "content": fragment.text,
            "start": fragment.start,
            "end": fragment.end,
        })
    payload = json.dumps({"trust": "untrusted_history", "items": items}, ensure_ascii=False, separators=(",", ":"))
    return (
        TRUST_POLICY
        + "\n\nBEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\n"
        + payload
        + "\nEND_POWERCONTEXT_PREPARED_CONTEXT_V1"
    )


def select_evidence(
    request: PrepareContextRequest,
    candidates: Sequence[PreparedMemoryCandidates],
    candidate_limit: int,
) -> PreparedContextBuild:
    """Select literal evidence without I/O; measure every packing as complete UTF-8 output."""
    if sum(len(group.hits) for group in candidates) > candidate_limit:
        raise PreparedContextInvariantError("memory-candidate-limit")
    documents = _documents(candidates)
    query = _terms(request.query)
    ranked, weights = _rank_sentences(documents, query)
    fragments = _pack(request, ranked, documents)
    if not fragments:
        fragments = _shortened(request, ranked, documents, query, weights)
    if not fragments:
        return PreparedContextBuild(context=PreparedContext(status="empty", content=None, content_bytes=0), origins=())
    content = _render(fragments)
    return PreparedContextBuild(
        context=PreparedContext(status="ready", content=content, content_bytes=len(content.encode("utf-8"))),
        origins=tuple(fragment.address for fragment in fragments),
        fragments=fragments,
    )


def _documents(candidates: Sequence[PreparedMemoryCandidates]) -> list[tuple[MemoryEntryAddress, str]]:
    documents: list[tuple[MemoryEntryAddress, str]] = []
    seen = set()
    for group in candidates:
        if group.hits and group.memory_ref is None:
            raise PreparedContextInvariantError("memory-ref-missing")
        for hit in group.hits:
            if hit.memory_ref != group.memory_ref:
                raise PreparedContextInvariantError("memory-ref-mismatch")
            identity = (
                group.scope_id,
                hit.memory_ref.family,
                hit.memory_ref.artifact_id,
                hit.memory_ref.revision,
                hit.entry_id,
                hit.entry_version_id,
            )
            if identity in seen or not hit.text.strip() or not hit.entry_id.strip() or not hit.entry_version_id.strip():
                continue
            seen.add(identity)
            documents.append((
                MemoryEntryAddress(
                    memory=ArtifactAddress(scope_id=group.scope_id, artifact=hit.memory_ref),
                    entry_id=hit.entry_id,
                    entry_version_id=hit.entry_version_id,
                ),
                hit.text,
            ))
    return documents


def _rank_sentences(
    documents: Sequence[tuple[MemoryEntryAddress, str]],
    query: Counter[str],
) -> tuple[list[_Sentence], dict[str, float]]:
    raw: list[tuple[int, int, int, Counter[str]]] = []
    for doc, (_, text) in enumerate(documents):
        start = 0
        for end in [match.end() for match in _BOUNDARY.finditer(text)] + [len(text)]:
            if end > start and text[start:end].strip():
                raw.append((doc, start, end, _terms(text[start:end])))
            start = end
    frequencies: Counter[str] = Counter()
    for _, _, _, terms in raw:
        frequencies.update(terms.keys())
    weights = {term: math.log(1 + (len(raw) + 1) / (frequencies[term] + 1)) for term in query}
    average = sum(sum(terms.values()) for _, _, _, terms in raw) / max(1, len(raw))
    ranked: list[_Sentence] = []
    for doc, start, end, terms in raw:
        length_norm = 0.35 + 0.65 * sum(terms.values()) / max(1, average)
        score = sum(
            weights[t] * min(query[t], 2) * (2.2 * terms[t]) / (terms[t] + 1.2 * length_norm) for t in query if terms[t]
        )
        if score > 0:
            ranked.append(_Sentence(doc, start, end, score))
    ranked.sort(key=lambda sentence: (-sentence.score, sentence.document, sentence.start))
    # Collapse identical repeated sentences within each entry without confusing scope identity.
    unique: list[_Sentence] = []
    texts = set()
    for sentence in ranked:
        key = (sentence.document, documents[sentence.document][1][sentence.start : sentence.end].strip())
        if key not in texts:
            texts.add(key)
            unique.append(sentence)
    return unique[:40], weights


def _fragments_for(
    selection: tuple[int, ...],
    ranked: Sequence[_Sentence],
    documents: Sequence[tuple[MemoryEntryAddress, str]],
) -> tuple[PreparedMemoryFragment, ...]:
    spans: list[tuple[int, int, int]] = []
    for sentence in sorted((ranked[i] for i in selection), key=lambda s: (s.document, s.start)):
        if spans and spans[-1][0] == sentence.document and sentence.start <= spans[-1][2]:
            doc, left, right = spans.pop()
            spans.append((doc, left, max(right, sentence.end)))
        else:
            spans.append((sentence.document, sentence.start, sentence.end))
    return tuple(
        PreparedMemoryFragment(documents[d][0], left, right, documents[d][1][left:right]) for d, left, right in spans
    )


def _pack(
    request: PrepareContextRequest,
    ranked: Sequence[_Sentence],
    documents: Sequence[tuple[MemoryEntryAddress, str]],
) -> tuple[PreparedMemoryFragment, ...]:
    # A bounded knapsack approximation: keep competing high-value packings instead of
    # committing to the first hit or a single value/size greedy ordering.
    beam: list[tuple[float, tuple[int, ...]]] = [(0.0, ())]
    for index, sentence in enumerate(ranked):
        expanded = list(beam)
        for score, selection in beam:
            proposed = (*selection, index)
            fragments = _fragments_for(proposed, ranked, documents)
            if len(_render(fragments).encode("utf-8")) <= request.max_bytes:
                expanded.append((score + sentence.score, proposed))
        expanded.sort(key=lambda state: (-state[0], state[1]))
        beam = expanded[:48]
    return _fragments_for(beam[0][1], ranked, documents)


def _shortened(
    request: PrepareContextRequest,
    ranked: Sequence[_Sentence],
    documents: Sequence[tuple[MemoryEntryAddress, str]],
    query: Counter[str],
    weights: dict[str, float],
) -> tuple[PreparedMemoryFragment, ...]:
    """When whole sentences do not fit, return a literal query-centred subspan."""
    best: tuple[float, PreparedMemoryFragment] | None = None
    for sentence in ranked:
        address, text = documents[sentence.document]
        matches = [
            m for m in _WORD.finditer(text, sentence.start, sentence.end) if _terms(m.group()).keys() & query.keys()
        ]
        for match in matches[:32]:
            for width in (32, 64, 96, 160, 320, 640, 1280):
                left = max(sentence.start, match.start() - width // 3)
                right = min(sentence.end, left + width)
                fragment = PreparedMemoryFragment(address, left, right, text[left:right])
                if len(_render((fragment,)).encode("utf-8")) > request.max_bytes:
                    continue
                score = sum(weights[t] * min(n, query[t]) for t, n in _terms(fragment.text).items() if t in query)
                if best is None or score > best[0] or (score == best[0] and len(fragment.text) > len(best[1].text)):
                    best = (score, fragment)
    return (best[1],) if best is not None else ()
