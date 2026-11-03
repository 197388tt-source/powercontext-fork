"""Lossless offline Pi v3 branch evidence and durable incremental capture."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, JsonValue

from powercontext.sources.connectors import ConnectorRunCompletion, ConnectorRunSession, ConnectorRunStatus
from powercontext.sources.definitions import SourceDefinition, SourceProjection
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence


class PiSessionInput(BaseModel):
    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


class PiEventSource(Source):
    event: PiEventInput


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _identity(session_id: str, event_id: str) -> str:
    return "pi:" + hashlib.sha256(_json([session_id, event_id]).encode()).hexdigest()


def _archive(content: bytes) -> tuple[str, dict[str, dict[str, Any]], str | None]:  # noqa: C901
    try:
        records = [json.loads(line) for line in content.decode("utf-8-sig").splitlines() if line.strip()]
        if not records or not isinstance(records[0], dict):
            message = "missing session header"
            raise ValueError(message)
        header = records[0]
        if header.get("type") != "session" or type(header.get("version")) is not int or header["version"] != 3:
            message = "expected Pi session version 3"
            raise ValueError(message)
        sid = header.get("id")
        if not isinstance(sid, str) or not sid.strip():
            message = "missing session identity"
            raise ValueError(message)
        nodes = {}
        leaf = None
        for record in records[1:]:
            if not isinstance(record, dict):
                message = "expected object record"
                raise ValueError(message)  # noqa: TRY004 - public contract requires ValueError
            eid = record.get("id")
            if not isinstance(eid, str) or not eid.strip() or eid in nodes:
                message = "invalid or duplicate event identity"
                raise ValueError(message)
            if "parentId" not in record:
                message = "missing parentId"
                raise ValueError(message)
            parent = record["parentId"]
            if parent is not None and (not isinstance(parent, str) or parent not in nodes):
                message = "parent must refer to an earlier node"
                raise ValueError(message)
            _json(record)
            nodes[eid] = record
            leaf = eid
    except (UnicodeError, TypeError, KeyError, RecursionError) as error:
        message = "invalid Pi archive"
        raise ValueError(message) from error
    else:
        return sid, nodes, leaf


def _body(content: object, *, tools: bool = False) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(str(block.get("text")))
        elif tools and block.get("type") == "toolCall":
            parts.append(
                f"[tool_call {block.get('name', '')} {block.get('id', '')}] {_json(block.get('arguments', {}))}"
            )
    return "\n".join(parts)


def _event(sid: str, record: dict[str, Any]) -> PiEventInput | None:  # noqa: C901
    kind = record.get("type")
    if kind == "message":
        message = record.get("message")
        if not isinstance(message, dict):
            message = "invalid message"
            raise ValueError(message)
        role = message.get("role")
        if not isinstance(role, str):
            message = "invalid role"
            raise ValueError(message)
        body = _body(message.get("content"), tools=True)
        if role == "toolResult":
            if body:
                body = f"[tool_result {message.get('toolName', '')} {message.get('toolCallId', '')}] {body}"
        elif role == "bashExecution":
            command, output = message.get("command", ""), message.get("output", "")
            body = f"$ {command}\n{output}" if command or output else ""
    elif kind in {"compaction", "branch_summary"}:
        role, body = kind, record.get("summary", "")
    elif kind == "custom_message":
        role, body = "custom", _body(record.get("content"))
    else:
        return None
    if not isinstance(body, str):
        message = "invalid visible text"
        raise ValueError(message)  # noqa: TRY004 - public contract requires ValueError
    if not body:
        return None
    return PiEventInput(
        session_id=sid,
        event_id=record["id"],
        parent_id=record["parentId"],
        event_type=kind,
        role=role,
        content=f"{role}: {body}",
        record=record,
    )


def _branch(sid: str, nodes: dict[str, dict[str, Any]], leaf: str | None) -> tuple[PiEventInput, ...]:
    if leaf is not None and leaf not in nodes:
        message = "missing selected leaf"
        raise ValueError(message)
    chain = []
    while leaf is not None:
        record = nodes[leaf]
        event = _event(sid, record)
        if event is not None:
            chain.append(event)
        leaf = record["parentId"]
    return tuple(reversed(chain))


def parse_pi_session(value: PiSessionInput) -> tuple[PiEventInput, ...]:
    sid, nodes, last = _archive(value.content)
    return _branch(sid, nodes, value.leaf_id if value.leaf_id is not None else last)


class _PiProjection:
    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        event = source.event
        return TextEvidence(
            source_type="pi-event",
            source_id=source.name,
            content=event.content,
            metadata=event.model_dump(include={"session_id", "event_id", "parent_id", "event_type", "role"}),
        )


class _PiDefinition:
    name = "pi-event"
    version = "1"
    input_class = PiEventInput
    source_class = PiEventSource
    projections: tuple[SourceProjection[PiEventSource], ...] = (_PiProjection(),)

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        return PiEventSource(
            name=_identity(value.session_id, value.event_id),
            materialization=SourceMaterialization.CAPTURED,
            event=value.model_copy(deep=True),
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return source.event.model_copy(deep=True)


PI_EVENT_SOURCE_DEFINITION: SourceDefinition[PiEventInput, PiEventSource, PiEventInput] = _PiDefinition()


class PiSessionConnector:
    name = "pi-session-history"
    version = "1"
    source_definitions = frozenset({"pi-event"})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self.path = Path(path)
        self.leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        sid, nodes, last = _archive(self.path.read_bytes())
        events = _branch(sid, nodes, self.leaf_id if self.leaf_id is not None else last)
        previous = session.checkpoint
        if previous is not None and not isinstance(previous, dict):
            message = "invalid checkpoint"
            raise ValueError(message)
        imported = dict(previous or {})
        # Check all retained branches before any submission, including unselected history.
        for eid, record in nodes.items():
            identity = _identity(sid, eid)
            if identity in imported and imported[identity] != hashlib.sha256(_json(record).encode()).hexdigest():
                message = "previously imported event was rewritten"
                raise ValueError(message)
        for event in events:
            identity = _identity(sid, event.event_id)
            if identity not in imported:
                await session.submit(identity, "pi-event", event)
                imported[identity] = hashlib.sha256(_json(event.record).encode()).hexdigest()
        return ConnectorRunCompletion(status=ConnectorRunStatus.COMPLETE, checkpoint=imported)
