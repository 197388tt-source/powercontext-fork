"""Evidence selection contracts beyond the public smoke tests."""

import json

import pytest
from pydantic import ValidationError

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import MemoryHit
from powercontext.builtin.runtime import PrepareContextRequest
from powercontext.builtin.runtime.prepared_context import PreparedContextBuilder, PreparedMemoryCandidates
from powercontext.builtin.runtime.prepared_text import TRUST_POLICY


def _group(text, scope="scope-a", revision=3):
    ref = ArtifactRef(family="memory", artifact_id="memory", revision=revision)
    return PreparedMemoryCandidates(
        scope_id=scope,
        memory_ref=ref,
        hits=(
            MemoryHit(
                memory_ref=ref, entry_id="entry", entry_version_id="version", text=text, score=1.0, matched_by=("fts",)
            ),
        ),
    )


def _build(groups, query="cobalt sensor", budget=1800, **options):
    return PreparedContextBuilder().build_scopes_result(
        request=PrepareContextRequest(query=query, max_bytes=budget, evidence_selection=True, **options),
        current_scope_id="scope-a",
        memory_candidates=groups,
    )


def _check(result, groups, budget):
    content = result.context.content or ""
    assert result.context.content_bytes == len(content.encode("utf-8")) <= budget
    assert len(result.fragments) == len(result.origins)
    if not content:
        assert not result.fragments
        return
    assert content.startswith(TRUST_POLICY)
    assert "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1" in content
    assert content.endswith("END_POWERCONTEXT_PREPARED_CONTEXT_V1")
    payload = json.loads(content.splitlines()[-2])
    assert payload["trust"] == "untrusted_history"
    assert len(payload["items"]) == len(result.fragments)
    for item, fragment, origin in zip(payload["items"], result.fragments, result.origins, strict=True):
        group = next(g for g in groups if g.scope_id == fragment.address.memory.scope_id)
        text = group.hits[0].text
        assert type(fragment.start) is int and type(fragment.end) is int
        assert 0 <= fragment.start < fragment.end <= len(text)
        assert fragment.text == text[fragment.start : fragment.end] == item["content"]
        assert origin == fragment.address
        assert item["start"] == fragment.start and item["end"] == fragment.end
        assert item["citation"] == {
            "scope_id": group.scope_id,
            "memory_ref": group.memory_ref.model_dump(mode="json"),
            "entry_id": "entry",
            "entry_version_id": "version",
        }


@pytest.mark.parametrize("budget", [512, 650, 800, 1200, 1400, 1800, 32768])
def test_complete_output_budget_escaping_and_unicode(budget):
    groups = (_group('Notes. 海洋 cobalt sensor says "温度\\记录".\n' * 12),)
    _check(_build(groups, budget=budget), groups, budget)


def test_identity_keeps_scope_and_revision_even_for_identical_text():
    groups = (
        _group("A cobalt sensor is calibrated.", "scope-a", 3),
        _group("A cobalt sensor is calibrated.", "scope-b", 7),
    )
    result = _build(groups, budget=3000)
    _check(result, groups, 3000)
    assert {f.address.memory.scope_id for f in result.fragments} == {"scope-a", "scope-b"}
    assert {f.address.memory.artifact.revision for f in result.fragments} == {3, 7}


def test_disjoint_evidence_survives_and_query_changes_selection():
    text = "The cobalt sensor records 37 degrees. " + "Administrative material. " * 30
    text += "The cobalt sensor resets at 42 degrees. "
    text += "The amber valve opens at 19 degrees."
    groups = (_group(text),)
    result = _build(groups, budget=1400)
    _check(result, groups, 1400)
    assert len(result.fragments) == 2
    assert "37" in result.fragments[0].text and "42" in result.fragments[1].text
    other = _build(groups, query="amber valve", budget=1400)
    assert any("19" in f.text for f in other.fragments)
    assert result.fragments != other.fragments


def test_adjacent_sentences_merge_without_losing_source_characters():
    text = "Cobalt sensors measure heat. Cobalt sensors measure light."
    result = _build((_group(text),))
    assert len(result.fragments) == 1
    assert result.fragments[0].text == text


def test_oversized_sentence_uses_query_centred_literal_subspan():
    groups = (_group("unrelated " * 200 + "cobalt sensor " + "background " * 200),)
    result = _build(groups, budget=1000)
    _check(result, groups, 1000)
    assert result.fragments and result.fragments[0].start > 0
    assert "cobalt sensor" in result.fragments[0].text


def test_no_overlap_and_empty_candidates_return_legal_empty_context():
    assert _build((_group("Nothing relevant here."),)).context.status == "empty"
    assert _build(()).fragments == ()


def test_flag_is_strict_and_default_is_off():
    assert PrepareContextRequest(query="sensor").evidence_selection is False
    with pytest.raises(ValidationError):
        PrepareContextRequest.model_validate({"query": "sensor", "evidence_selection": 1})


def test_budget_probe_matches_evidence_delivery():
    groups = (_group("General material. " * 100 + "The cobalt sensor is calibrated."),)
    request = PrepareContextRequest(query="cobalt", max_bytes=1200, evidence_selection=True)
    builder = PreparedContextBuilder()
    result = builder.build_scopes_result(request=request, current_scope_id="scope-a", memory_candidates=groups)
    probe = builder.probe_budget(request=request, current_scope_id="scope-a", memory_candidates=groups)
    assert probe.delivered_items == len(result.fragments)
    assert probe.unused_bytes == 1200 - result.context.content_bytes
