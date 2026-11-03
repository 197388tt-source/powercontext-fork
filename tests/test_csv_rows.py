"""Additional CSV contract regressions beyond the public workload."""

import asyncio

import pytest

from powercontext.sources import SourceDefinitionRegistry
from powercontext.sources.csv_rows import (
    CSV_ROW_SOURCE_DEFINITION,
    CSVDocumentInput,
    CSVRowInput,
    csv_row_source_id,
    parse_csv_document,
)


def test_exact_identity_length_and_unicode_encoding():
    assert len(csv_row_source_id("d", "a" * 250)) == 256
    with pytest.raises(ValueError):
        csv_row_source_id("d", "a" * 251)
    assert csv_row_source_id("文档", " a:b/ ") == "csv:%E6%96%87%E6%A1%A3:%20a%3Ab%2F%20"
    assert csv_row_source_id("a:b", "c") != csv_row_source_id("a", "b:c")


@pytest.mark.parametrize(
    "content",
    [
        b" id ,id,note\n a ,a,\n",
        b' id ,id,note\r\n a ,a,"x\ry\r\nz"\r\n',
    ],
)
def test_distinct_space_headers_and_empty_strings(content):
    (row,) = parse_csv_document(CSVDocumentInput(document_id="d", primary_key=" id ", content=content))
    assert row.columns == (" id ", "id", "note")
    assert row.values[:2] == (" a ", "a")
    assert row.primary_key_value == " a "


@pytest.mark.parametrize(
    "content",
    [
        b"\nid,name\n1,a\n",
        b'id,name\n1,a\n2,"x"tail\n',
        b"id,name\n1,a\n   ,b\n",
        b"id,name\n1,a\n2,b,extra\n",
        b"id,name\n1,a\n\xff,b\n",
        b'id,name\n1,a\n"",b\n',
        b"id,name\n1,a\n , \n",
    ],
)
def test_invalid_late_record_raises_instead_of_returning_partial_rows(content):
    with pytest.raises(ValueError):
        parse_csv_document(CSVDocumentInput(document_id="d", primary_key="id", content=content))


def test_direct_resolve_validates_row_consistency():
    registry = SourceDefinitionRegistry((CSV_ROW_SOURCE_DEFINITION,))
    row = CSVRowInput(document_id="d", primary_key="id", primary_key_value="other", columns=("id",), values=("key",))
    with pytest.raises(ValueError):
        asyncio.run(registry.resolve(row))
