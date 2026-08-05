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
            "activity",
            {"label": "Generating image", "state": "start"},
            5,
        ),
        _event(
            "tool_event",
            {
                "type": "tool_call",
                "id": "call:img-1",
                "tool_name": "generate_image",
                "state": "end",
                "action_summary": "Generating image",
                "output": {
                    "status": "ok",
                    "attachments": [
                        {
                            "file_name": "robot.png",
                            "content_type": "image/png",
                            "data_base64": "abc",
                        }
                    ]
                },
            },
            6,
        ),
    ]

    parts, thinking_steps = _build_stream_parts_from_events(
        events,
        message_content="I'll search next.\n\nResult: found it.",
    )

    assert thinking_steps == ["Looking up official Python website", "Generating image"]
    assert parts[0] == {"type": "text", "text": "I'll search next.\n\n"}
    assert parts[1] == {"type": "action", "label": "Looking up official Python website"}
    assert parts[2] == {"type": "text", "text": "Result: found it."}
    assert parts[3]["type"] == "action"
    assert parts[3]["label"] == "Generating image"
    assert parts[3]["attachments"] == [
        {
            "file_name": "robot.png",
            "content_type": "image/png",
            "data_base64": "abc",
        }
    ]
    assert parts[3]["tool_event"]["id"] == "call:img-1"


def test_build_stream_parts_keeps_specialized_tool_event_over_generic_tool_call():
    events = [
        _event("activity", {"label": "Running code (plot)", "state": "start"}, 1),
        _event(
            "tool_event",
            {
                "type": "code_execution",
                "id": "exec-1",
                "code": "print(1)",
                "output": {"stdout": "1\n", "exit_code": 0},
            },
            2,
        ),
        _event(
            "tool_event",
            {
                "type": "tool_call",
                "id": "call:exec-1",
                "tool_name": "code_execution",
                "state": "end",
                "action_summary": "Running code (plot)",
                "output": {"status": "ok", "result_preview": "ok"},
            },
            3,
        ),
    ]

    parts, _thinking_steps = _build_stream_parts_from_events(events, message_content="")
    assert parts is not None
    assert parts[0]["label"] == "Running code (plot)"
    assert parts[0]["tool_event"]["type"] == "code_execution"
    assert parts[0]["tool_event"]["id"] == "exec-1"


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
