"""Tests for Matrix human_id extraction and downstream request passthrough."""

from types import SimpleNamespace

import pytest

import matrix.channel as matrix_channel
from matrix.channel import MatrixChannel


if not hasattr(matrix_channel, "ContentType"):
    matrix_channel.ContentType = SimpleNamespace(TEXT="text")

if not hasattr(matrix_channel, "TextContent"):
    class _TextContent:  # pylint: disable=too-few-public-methods
        def __init__(self, *, type, text):
            self.type = type
            self.text = text

    matrix_channel.TextContent = _TextContent


def _make_channel(user_id: str = "@bot:hs.local") -> MatrixChannel:
    """Build a bare channel instance for unit-testing isolated methods."""
    ch = MatrixChannel.__new__(MatrixChannel)
    ch._user_id = user_id
    ch._client = None
    return ch


async def _noop_async(*_args, **_kwargs) -> None:
    return None


@pytest.mark.asyncio
async def test_on_room_event_dm_payload_includes_normalized_human_id():
    ch = _make_channel()
    captured: list[dict] = []
    ch._enqueue = captured.append
    ch._check_allowed = lambda *_args: True
    ch._is_dm_room = lambda *_args: _async_return(True)
    ch._send_read_receipt = _noop_async
    ch._send_typing = _noop_async

    room = SimpleNamespace(room_id="!dm:hs.local")
    event = SimpleNamespace(
        sender=" alice:hs.local ",
        body="hello",
        event_id="$evt-dm",
        server_timestamp=123,
    )

    await ch._on_room_event(room, event)

    assert len(captured) == 1
    payload = captured[0]
    assert payload["human_id"] == "@alice:hs.local"
    assert payload["meta"]["human_id"] == "@alice:hs.local"
    assert payload["meta"]["room_id"] == "!dm:hs.local"
    assert payload["content_parts"][0].text == "hello"


@pytest.mark.asyncio
async def test_on_room_event_group_payload_keeps_room_target_and_human_id():
    ch = _make_channel()
    captured: list[dict] = []
    cleared: list[str] = []
    ch._enqueue = captured.append
    ch._check_allowed = lambda *_args: True
    ch._is_dm_room = lambda *_args: _async_return(False)
    ch._send_read_receipt = _noop_async
    ch._send_typing = _noop_async
    ch._require_mention = lambda *_args: False
    ch._get_display_name = lambda *_args: "Alice"
    ch._apply_history_to_parts = lambda _room_id, parts: parts
    ch._clear_history = cleared.append

    room = SimpleNamespace(room_id="!group:hs.local")
    event = SimpleNamespace(
        sender="@alice:hs.local",
        body="hello team",
        event_id="$evt-group",
        server_timestamp=456,
    )

    await ch._on_room_event(room, event)

    assert len(captured) == 1
    payload = captured[0]
    assert payload["human_id"] == "@alice:hs.local"
    assert payload["meta"]["human_id"] == "@alice:hs.local"
    assert payload["meta"]["room_id"] == "!group:hs.local"
    assert payload["content_parts"][0].text == "Alice: hello team"
    assert cleared == ["!group:hs.local"]


def test_build_agent_request_from_native_uses_human_id_but_reply_handle_uses_room():
    ch = _make_channel()
    captured: dict[str, object] = {}

    def _build_request(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            user_id=kwargs["sender_id"],
            session_id=kwargs["session_id"],
            channel_meta=kwargs["channel_meta"],
        )

    ch.build_agent_request_from_user_content = _build_request

    req = ch.build_agent_request_from_native(
        {
            "sender_id": " alice:hs.local ",
            "content_parts": [],
            "meta": {
                "room_id": "!room:hs.local",
                "sender_id": " alice:hs.local ",
            },
        }
    )

    assert captured["sender_id"] == "@alice:hs.local"
    assert captured["session_id"] == "matrix:!room:hs.local"
    assert captured["channel_meta"]["human_id"] == "@alice:hs.local"
    assert req.human_id == "@alice:hs.local"
    assert ch.get_to_handle_from_request(req) == "!room:hs.local"


async def _async_return(value):
    return value
