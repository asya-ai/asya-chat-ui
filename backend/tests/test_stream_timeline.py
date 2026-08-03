from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from app.api.chats import _build_stream_parts_from_events


def _event(event_type: str, payload: dict, sequence: int = 1):
    return SimpleNamespace(
        id=uuid4(),
        event_type=event_type,
        payload_json=payload,
        sequence=sequence,
    )


def test_build_stream_parts_interleaves_text_and_actions():
    events = [
        _event("delta", {"delta": "I'll search next.\n\n"}, 1),
        _event("activity", {"label": "Thinking", "state": "start"}, 2),
        _event(
            "activity",
            {"label": "Looking up official Python website", "state": "start"},
            3,
        ),
        _event("delta", {"delta": "Result: found it."}, 4),
        _event(
            "tool_event",
            {
                "type": "tool_call",
                "tool_name": "generate_image",
                "state": "end",
                "action_summary": "Generating image",
                "output": {
                    "attachments": [
                        {
                            "file_name": "robot.png",
                            "content_type": "image/png",
                            "data_base64": "abc",
                        }
                    ]
                },
            },
            5,
        ),
    ]

    parts, thinking_steps = _build_stream_parts_from_events(
        events,
        message_content="I'll search next.\n\nResult: found it.",
    )

    assert thinking_steps == ["Looking up official Python website"]
    assert parts == [
        {"type": "text", "text": "I'll search next.\n\n"},
        {"type": "action", "label": "Looking up official Python website"},
        {"type": "text", "text": "Result: found it."},
        {
            "type": "action",
            "label": "Generating image",
            "attachments": [
                {
                    "file_name": "robot.png",
                    "content_type": "image/png",
                    "data_base64": "abc",
                }
            ],
        },
    ]


def test_build_stream_parts_inserts_content_when_only_actions_exist():
    events = [
        _event("activity", {"label": "Generating image", "state": "start"}, 1),
    ]

    parts, thinking_steps = _build_stream_parts_from_events(
        events,
        message_content="Done.",
    )

    assert thinking_steps == ["Generating image"]
    assert parts == [
        {"type": "text", "text": "Done."},
        {"type": "action", "label": "Generating image"},
    ]
