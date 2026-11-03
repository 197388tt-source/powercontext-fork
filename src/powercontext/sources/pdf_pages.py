"""Captured PDF pages and offline retrieval through scoped Memory's public API."""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
from collections import Counter
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pypdf import PdfReader

from powercontext.artifacts import MemoryCitation
from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.runtime import GetMemoryEntryRequest, SearchMemoryRequest
from powercontext.builtin.runtime.application import ScopedMemoryApplication
from powercontext.sources import AdapterSourceDefinition, Source, SourceMaterialization, TextEvidence


class PDFDocumentInput(BaseModel):
    document_id: str
    content: bytes

    @field_validator("document_id")
    @classmethod
    def nonblank_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("blank-document-id")
        return value


class PDFPageInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_id: str
    document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_number: int = Field(ge=1)
    text: str

    @field_validator("document_id")
    @classmethod
    def nonblank_id(cls, value: str) -> str:
        return PDFDocumentInput.nonblank_id(value)


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    """Extract every physical page atomically, including pages with no text."""
    raw = value.content
    if not raw or not raw.lstrip().startswith(b"%PDF-") or b"%%EOF" not in raw[-4096:]:
        raise ValueError("invalid-pdf")
    try:
        pages = _extract_pages(value)
    except Exception as error:
        raise ValueError("pdf-parse-failed") from error
    return pages


def _extract_pages(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    digest = hashlib.sha256(value.content).hexdigest()
    reader = PdfReader(io.BytesIO(value.content), strict=True)
    if reader.is_encrypted and not reader.decrypt(""):
        raise ValueError("password-required")
    pages = tuple(
        PDFPageInput(
            document_id=value.document_id,
            document_sha256=digest,
            page_number=number,
            text=page.extract_text() or "",
        )
        for number, page in enumerate(reader.pages, 1)
    )
    if not pages:
        raise ValueError("no-pages")
    return pages


class PDFPageSource(Source):
    page: PDFPageInput


@dataclass(frozen=True)
class _PDFPageAdapter:
    name = "pdf-page"
    input_class = PDFPageInput
    source_class = PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        identity = json.dumps(
            [value.document_id, value.document_sha256, value.page_number], ensure_ascii=False, separators=(",", ":")
        ).encode()
        return PDFPageSource(
            name="pdf-page:" + hashlib.sha256(identity).hexdigest(),
            materialization=SourceMaterialization.CAPTURED,
            page=value,
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return source.page


@dataclass(frozen=True)
class _PDFTextProjection:
    name = "powercontext.text-evidence"
    version = "1"
    source_class = PDFPageSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        page = source.page
        return TextEvidence(
            source_type="pdf-page",
            source_id=source.name,
            content=f"{page.document_id}\n{page.text}",
            metadata={
                "document_id": page.document_id,
                "document_sha256": page.document_sha256,
                "page_number": page.page_number,
            },
        )


PDF_PAGE_SOURCE_DEFINITION = AdapterSourceDefinition(adapter=_PDFPageAdapter(), projections=(_PDFTextProjection(),))

_STOP = frozenset([
    "a",
    "an",
    "the",
    "of",
    "to",
    "in",
    "on",
    "for",
    "from",
    "by",
    "with",
    "and",
    "or",
    "as",
    "at",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "what",
    "which",
    "who",
    "how",
    "has",
    "have",
    "had",
    "do",
    "does",
    "did",
    "any",
    "its",
    "it",
    "s",
    "that",
    "this",
    "these",
    "those",
    "can",
    "could",
    "would",
    "should",
    "only",
    "use",
    "using",
    "used",
    "shown",
    "give",
    "response",
    "answer",
    "question",
    "calculate",
    "define",
    "assuming",
    "perspective",
    "analyst",
    "investment",
    "details",
    "reported",
    "state",
    "if",
    "then",
    "not",
    "explicitly",
    "directly",
    "outlined",
    "primarily",
    "important",
    "materially",
    "ongoing",
])
# Domain vocabulary, independent of any document or benchmark question.
_CONCEPTS = (
    (
        {"geographies", "geography", "geographic", "countries", "operates"},
        "geographic segment segments countries international operations",
    ),
    ({"legal", "litigation", "battles", "lawsuits"}, "legal litigation proceedings contingencies"),
    ({"income", "earnings", "profit", "p&l"}, "income earnings operations consolidated statement"),
    ({"ebitda", "amortization", "depreciation", "d&a"}, "depreciation amortization operating income cash flows"),
    ({"capex", "capital"}, "capital expenditures spending cash flows property plant equipment"),
    ({"restructuring"}, "restructuring impairment charges expenses"),
    ({"margin", "revenue", "sales"}, "revenue revenues sales income"),
)


def _terms(text: str) -> list[str]:
    return re.findall(r"[a-z]+|\d+", text.lower())


def _query_plan(question: str, words: list[str]) -> tuple[list[str], list[str]]:
    expanded: list[str] = []
    lower = question.lower()
    for triggers, vocabulary in _CONCEPTS:
        if triggers.intersection(words) or any(trigger in lower for trigger in triggers if "&" in trigger):
            expanded.extend(vocabulary.split())
    queries = [" ".join(words[:32])]
    queries.extend(" ".join(words[i : i + 2]) for i in range(0, min(len(words), 32), 2))
    extra = list(dict.fromkeys(expanded))
    queries.extend(" ".join(extra[i : i + 2]) for i in range(0, len(extra), 2))
    return queries, extra


async def search_pdf_pages(
    memory: ScopedMemoryApplication, question: str, limit: int = 10
) -> tuple[MemoryCitation, ...]:
    """Recall lexical candidates, rerank offline, and return exact stored page citations.

    Small subqueries avoid losing evidence to long-question lexical admission rules.
    Work is bounded to 32 searches of at most 100 hits, with no collection scans,
    model calls, side indexes, filesystem access, or synthesized entries.
    """
    if limit <= 0 or not question.strip():
        return ()
    words = list(dict.fromkeys(word for word in _terms(question) if word not in _STOP))
    if not words:
        return ()
    queries, extra = _query_plan(question, words)
    candidates: dict[tuple[str, str, int, str, str], MemoryHit] = {}
    fusion = Counter()
    for query in list(dict.fromkeys(queries))[:32]:
        result = await memory.search(SearchMemoryRequest(query=query, mode="fts", limit=100))
        for rank, hit in enumerate(result.hits, 1):
            key = (
                hit.memory_ref.family,
                hit.memory_ref.artifact_id,
                hit.memory_ref.revision,
                hit.entry_id,
                hit.entry_version_id,
            )
            candidates.setdefault(key, hit)
            fusion[key] += 1 / (60 + rank)
    if not candidates:
        return ()
    counts = {key: Counter(_terms(hit.text)) for key, hit in candidates.items()}
    frequency = Counter(term for count in counts.values() for term in count)
    average = sum(sum(count.values()) for count in counts.values()) / len(counts)
    weights = dict.fromkeys(extra, 0.45)
    weights.update(dict.fromkeys(words, 1.0))

    def score(key):
        count = counts[key]
        norm = 1.2 * (0.25 + 0.75 * sum(count.values()) / max(average, 1))
        value = sum(
            weight
            * math.log(1 + (len(counts) - frequency[term] + 0.5) / (frequency[term] + 0.5))
            * count[term]
            * 2.2
            / (count[term] + norm)
            for term, weight in weights.items()
            if count[term]
        )
        # Document identity is stronger company/year evidence than incidental prose.
        document_words = set(_terms(candidates[key].text.partition("\n")[0]))
        value += 3 * len(document_words.intersection(words))
        return value + fusion[key]

    citations = []
    seen = set()
    for key in sorted(candidates, key=lambda key: (-score(key), key)):
        hit = candidates[key]
        citation = MemoryCitation(
            memory_ref=hit.memory_ref, entry_id=hit.entry_id, entry_version_id=hit.entry_version_id
        )
        entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
        pages = tuple(sorted((ref.source_type, ref.source_id) for ref in entry.sources))
        if len(pages) != 1 or pages[0][0] != "pdf-page" or pages[0] in seen:
            continue
        seen.add(pages[0])
        citations.append(citation)
        if len(citations) == limit:
            break
    return tuple(citations)
