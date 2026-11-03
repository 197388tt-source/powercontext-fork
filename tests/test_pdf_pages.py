"""PDF boundary contracts and exact citations over a real restarted Runtime."""

import hashlib
import io

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from powercontext.builtin.runtime import GetMemoryEntryRequest, open_builtin_runtime
from powercontext.sources import SourceDefinitionRegistry, project_source_for_transport
from powercontext.sources.pdf_pages import (
    PDF_PAGE_SOURCE_DEFINITION,
    PDFDocumentInput,
    PDFPageInput,
    parse_pdf_document,
    search_pdf_pages,
)


def _pdf(*texts, password=None):
    writer = PdfWriter()
    for text in texts:
        page = writer.add_blank_page(width=200, height=200)
        font = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        })
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})
        })
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 10 100 Td ({text}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    if password is not None:
        writer.encrypt(user_password=password, owner_password=password)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_page_boundaries_and_complete_byte_digest():
    raw = _pdf("Revenue 123 million", "", "Operating income 45 million")
    pages = parse_pdf_document(PDFDocumentInput(document_id="company", content=raw))
    assert [page.text.strip() for page in pages] == ["Revenue 123 million", "", "Operating income 45 million"]
    assert [page.page_number for page in pages] == [1, 2, 3]
    assert all(page.document_sha256 == hashlib.sha256(raw).hexdigest() for page in pages)


@pytest.mark.parametrize("document_id", ["", " \t\n"])
def test_blank_document_identity_rejected(document_id):
    with pytest.raises(ValueError):
        PDFDocumentInput(document_id=document_id, content=b"data")


def test_password_and_late_corruption_fail():
    with pytest.raises(ValueError):
        parse_pdf_document(PDFDocumentInput(document_id="locked", content=_pdf("text", password=__name__)))
    raw = _pdf("first", "second")
    # Preserve header and trailer while making a later page's content unreadable.
    before, after = raw.rsplit(b"endstream", 1)
    raw = before + b"badstream" + after
    with pytest.raises(ValueError):
        parse_pdf_document(PDFDocumentInput(document_id="broken", content=raw))


async def test_long_identity_and_revision_names_are_stable():
    registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
    page = PDFPageInput(document_id="公司/:" * 500, document_sha256="a" * 64, page_number=1, text="one")
    source = await registry.resolve(page)
    assert len(source.name) <= 256
    assert await registry.read(source) == page
    assert source == await registry.resolve(page)
    assert source.name != (await registry.resolve(page.model_copy(update={"document_sha256": "b" * 64}))).name
    assert source.name != (await registry.resolve(page.model_copy(update={"page_number": 2}))).name


async def test_restarted_memory_recall_limits_and_exact_pages(tmp_path):
    from selftest_support.source_acceptance import persist

    registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
    pages = parse_pdf_document(
        PDFDocumentInput(
            document_id="EXAMPLE_2025",
            content=_pdf("Legal proceedings and litigation", "Revenue and sales", "Cash flows"),
        )
    )
    observations = [project_source_for_transport(registry, await registry.resolve(page)) for page in pages]
    config, scope_id, stored = await persist(tmp_path, PDF_PAGE_SOURCE_DEFINITION, observations)
    async with open_builtin_runtime(config) as runtime:
        memory = runtime.memory.for_scope(scope_id)
        assert await search_pdf_pages(memory, "legal", limit=0) == ()
        assert await search_pdf_pages(memory, "  ") == ()
        assert await search_pdf_pages(memory, "zzzzzzunmatched") == ()
        citations = await search_pdf_pages(memory, "Are there legal battles?", limit=1)
        assert len(citations) == 1
        entry = (await memory.get(GetMemoryEntryRequest(citation=citations[0]))).entry
        assert entry.text == "EXAMPLE_2025\nLegal proceedings and litigation"
        assert entry.sources[0].source_id == observations[0].name
        assert entry.sources[0].source_id in stored
