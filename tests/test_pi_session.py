"""Additional offline recovery and full-payload regression tests."""

import json

import pytest

from powercontext.sources import SourceDefinitionRegistry
from powercontext.sources.connectors import ConnectorBinding, ConnectorLifecycle
from powercontext.sources.pi_session import (
    PI_EVENT_SOURCE_DEFINITION,
    PiSessionConnector,
    PiSessionInput,
    parse_pi_session,
)


def archive(records):
    return "\n".join(
        json.dumps(x, ensure_ascii=False) for x in [{"type": "session", "version": 3, "id": "会话"}, *records]
    ).encode()


async def test_large_tool_result_roundtrip():
    text = "完整工具输出\n" * 40000
    records = [
        {
            "type": "message",
            "id": "result",
            "parentId": None,
            "message": {"role": "toolResult", "toolName": "read", "toolCallId": "call", "content": text},
        }
    ]
    (event,) = parse_pi_session(PiSessionInput(content=archive(records)))
    registry = SourceDefinitionRegistry((PI_EVENT_SOURCE_DEFINITION,))
    source = await registry.resolve(event)
    assert (await registry.read(source)).record == records[0]
    assert (await registry.read(source)).content == "toolResult: [tool_result read call] " + text


async def test_sink_failure_keeps_checkpoint(tmp_path):
    class Store:
        value = None

        async def load(self, binding):
            return self.value

        async def save(self, binding, checkpoint, *, expected):
            assert self.value == expected
            self.value = checkpoint

    class Sink:
        async def submit(self, *args):
            message = "simulated storage failure"
            raise OSError(message)

    records = [{"type": "message", "id": "root", "parentId": None, "message": {"role": "user", "content": "hello"}}]
    path = tmp_path / "pi.jsonl"
    path.write_bytes(archive(records))
    store = Store()
    binding = ConnectorBinding(
        scope_id="scope", binding_id="pi", connector_name="pi-session-history", connector_version="1"
    )
    with pytest.raises(OSError):
        await ConnectorLifecycle(sink=Sink(), checkpoints=store).run(PiSessionConnector(path), binding)
    assert store.value is None


@pytest.mark.parametrize(
    "record",
    [
        {"type": "model_change", "id": "a"},
        {"type": "model_change", "id": "a", "parentId": "later"},
    ],
)
def test_invalid_parent(record):
    with pytest.raises(ValueError):
        parse_pi_session(PiSessionInput(content=archive([record])))
