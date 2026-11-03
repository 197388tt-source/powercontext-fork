"""Portable, canonical context packs with verified prefix deltas."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass

PACK_SCHEMA = "powercontext.context-pack.v1"
DELTA_SCHEMA = "powercontext.context-pack-delta.v1"


class ContextPackError(ValueError):
    """Invalid context pack, chunk or delta."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)


def _json(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _items(items: tuple[bytes, ...]) -> tuple[bytes, ...]:
    if not isinstance(items, tuple):
        raise ContextPackError("chunks-must-be-a-bytes-tuple")
    for item in items:
        if not isinstance(item, bytes):
            raise ContextPackError("chunk-must-be-bytes")
        try:
            item.decode("utf-8")
        except UnicodeError as error:
            raise ContextPackError("invalid-UTF-8-chunk") from error
    return items


def _encode(items: tuple[bytes, ...]) -> list[str]:
    return [base64.b64encode(item).decode("ascii") for item in _items(items)]


def _decode(values: object) -> tuple[bytes, ...]:
    if not isinstance(values, list):
        raise ContextPackError("chunks-must-be-an-array")
    result = []
    for value in values:
        if not isinstance(value, str):
            raise ContextPackError("chunk-must-be-Base64-text")
        try:
            item = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ContextPackError("invalid-Base64") from error
        if base64.b64encode(item).decode("ascii") != value:
            raise ContextPackError("noncanonical-Base64")
        result.append(item)
    return _items(tuple(result))


def _digest(value: object) -> None:
    if not isinstance(value, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise ContextPackError("invalid-digest")


def _prefix(value: object) -> None:
    if type(value) is not int or value < 0:
        raise ContextPackError("prefix-must-be-a-nonnegative-integer")


def _read(data: bytes, schema: str, fields: set[str]) -> dict:
    def pairs(entries):
        result = {}
        for key, value in entries:
            if key in result:
                raise ContextPackError("duplicate-field")
            result[key] = value
        return result

    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeError, ValueError, AttributeError) as error:
        raise ContextPackError("invalid-JSON") from error
    if not isinstance(value, dict) or set(value) != fields or value.get("schema") != schema:
        raise ContextPackError("invalid-schema-or-fields")
    return value


@dataclass(frozen=True)
class ContextPack:
    items: tuple[bytes, ...]
    digest: str
    byte_count: int

    @classmethod
    def build(cls, items: tuple[bytes, ...]) -> ContextPack:
        unsigned = {"schema": PACK_SCHEMA, "items": _encode(items)}
        digest = "sha256:" + hashlib.sha256(_json(unsigned)).hexdigest()
        size = 0
        while True:
            actual = len(_json({**unsigned, "digest": digest, "byte_count": size}))
            if actual == size:
                return cls(items, digest, size)
            size = actual

    @classmethod
    def from_content(cls, content: str | None, *, chunk_bytes: int = 512) -> ContextPack:
        if type(chunk_bytes) is not int or chunk_bytes <= 0:
            raise ContextPackError("chunk_bytes-must-be-a-positive-integer")
        if content is None:
            content = ""
        if not isinstance(content, str):
            raise ContextPackError("content-must-be-text")
        try:
            raw = content.encode("utf-8")
        except UnicodeError as error:
            raise ContextPackError("invalid-UTF-8-content") from error
        chunks = []
        start = 0
        while start < len(raw):
            end = min(start + chunk_bytes, len(raw))
            while end < len(raw) and end > start and raw[end] & 0xC0 == 0x80:
                end -= 1
            if end == start:
                raise ContextPackError("UTF-8-character-exceeds-chunk-limit")
            chunks.append(raw[start:end])
            start = end
        return cls.build(tuple(chunks))

    def to_bytes(self) -> bytes:
        return _json({
            "schema": PACK_SCHEMA,
            "items": _encode(self.items),
            "digest": self.digest,
            "byte_count": self.byte_count,
        })

    @classmethod
    def from_bytes(cls, data: bytes) -> ContextPack:
        value = _read(data, PACK_SCHEMA, {"schema", "items", "digest", "byte_count"})
        pack = cls.build(_decode(value["items"]))
        if type(value["byte_count"]) is not int or value["byte_count"] != pack.byte_count:
            raise ContextPackError("incorrect-byte_count")
        if value["digest"] != pack.digest:
            raise ContextPackError("incorrect-pack-digest")
        return pack


@dataclass(frozen=True)
class ContextPackDelta:
    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack) -> ContextPackDelta:
        base = ContextPack.from_bytes(base.to_bytes())
        target = ContextPack.from_bytes(target.to_bytes())
        prefix = 0
        for left, right in zip(base.items, target.items, strict=False):
            if left != right:
                break
            prefix += 1
        return cls(base.digest, target.digest, prefix, target.items[prefix:])

    def to_bytes(self) -> bytes:
        _digest(self.base_digest)
        _digest(self.target_digest)
        _prefix(self.prefix_items)
        return _json({
            "schema": DELTA_SCHEMA,
            "base_digest": self.base_digest,
            "target_digest": self.target_digest,
            "prefix_items": self.prefix_items,
            "appended": _encode(self.appended),
        })

    @property
    def byte_count(self) -> int:
        return len(self.to_bytes())

    @classmethod
    def from_bytes(cls, data: bytes) -> ContextPackDelta:
        value = _read(data, DELTA_SCHEMA, {"schema", "base_digest", "target_digest", "prefix_items", "appended"})
        _digest(value["base_digest"])
        _digest(value["target_digest"])
        _prefix(value["prefix_items"])
        return cls(value["base_digest"], value["target_digest"], value["prefix_items"], _decode(value["appended"]))


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta) -> ContextPack:
    base = ContextPack.from_bytes(base.to_bytes())
    delta = ContextPackDelta.from_bytes(delta.to_bytes())
    if base.digest != delta.base_digest:
        raise ContextPackError("base-digest-mismatch")
    if delta.prefix_items > len(base.items):
        raise ContextPackError("prefix-exceeds-base-length")
    target = ContextPack.build(base.items[: delta.prefix_items] + delta.appended)
    if target.digest != delta.target_digest:
        raise ContextPackError("target-digest-mismatch")
    return target
