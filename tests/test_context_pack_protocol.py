"""Additional malformed-wire and UTF-8 boundary regression checks."""

import json

import pytest

from powercontext.builtin.runtime.context_pack import ContextPack, ContextPackDelta, ContextPackError


@pytest.mark.parametrize("content", [None, "", "海洋🙂abc\n\x00é"])
def test_chunk_boundaries(content):
    for limit in (4, 5, 7, 512):
        pack = ContextPack.from_content(content, chunk_bytes=limit)
        assert b"".join(pack.items) == (content or "").encode()
        assert all(len(item) <= limit and item.decode() for item in pack.items)
        assert ContextPack.from_bytes(pack.to_bytes()) == pack


@pytest.mark.parametrize(
    "field,value", [("items", ["%%%"]), ("items", ["/w=="]), ("items", ["YR=="]), ("byte_count", True), ("extra", 0)]
)
def test_pack_rejects_invalid_wire(field, value):
    payload = json.loads(ContextPack.build((b"a",)).to_bytes())
    payload[field] = value
    with pytest.raises(ContextPackError):
        ContextPack.from_bytes(json.dumps(payload).encode())


@pytest.mark.parametrize("data", [b"[]", b"null", b"\xff", b'{"schema":1,"schema":2}'])
def test_decoders_normalize_invalid_json(data):
    for cls in (ContextPack, ContextPackDelta):
        with pytest.raises(ContextPackError):
            cls.from_bytes(data)
