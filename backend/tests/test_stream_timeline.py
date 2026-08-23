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


def test_build_stream_parts_attaches_code_outputs_to_running_code_action():
    events = [
        _event("activity", {"label": "Running code", "state": "start"}, 1),
        _event(
            "tool_event",
            {
                "type": "code_execution",
                "id": "exec-2",
                "code": "open('/outputs/chart.png','wb').write(b'x')",
                "output": {
                    "stdout": "",
                    "exit_code": 0,
                    "outputs": ["chart.png"],
                    "output_files": [
                        {
                            "file_name": "chart.png",
                            "content_type": "image/png",
                        }
                    ],
                },
            },
            2,
        ),
        _event(
            "tool_event",
            {
                "type": "tool_call",
                "id": "call:exec-2",
                "tool_name": "code_execution",
                "state": "end",
                "output": {
                    "status": "ok",
                    "attachments": [
                        {
                            "file_name": "chart.png",
                            "content_type": "image/png",
                            "data_base64": "abc",
                        }
                    ],
                },
            },
            3,
        ),
    ]

    parts, _thinking_steps = _build_stream_parts_from_events(events, message_content="")
    assert parts is not None
    assert len(parts) == 1
    assert parts[0]["label"] == "Running code"
    assert parts[0]["attachments"] == [
        {
            "file_name": "chart.png",
            "content_type": "image/png",
            "data_base64": "abc",
        }
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


def test_build_stream_parts_reasoning_updates_same_action_node():
    events = [
        _event(
            "tool_event",
            {"type": "reasoning", "id": "reasoning_0", "content": "Step one"},
            1,
        ),
        _event(
            "tool_event",
            {
                "type": "reasoning",
                "id": "reasoning_0",
                "content": "Step one\nStep two",
            },
            2,
        ),
        _event("delta", {"delta": "Final answer."}, 3),
    ]

    parts, _thinking_steps = _build_stream_parts_from_events(events, message_content="")
    assert parts is not None
    assert len(parts) == 2
    assert parts[0]["type"] == "action"
    assert parts[0]["label"] == "Thoughts"
    assert parts[0]["tool_event"]["content"] == "Step one\nStep two"
    assert parts[1] == {"type": "text", "text": "Final answer."}


def test_build_stream_parts_reasoning_keeps_separate_episodes():
    events = [
        _event(
            "tool_event",
            {"type": "reasoning", "id": "reasoning_0", "content": "First episode"},
            1,
        ),
        _event(
            "tool_event",
            {"type": "reasoning", "id": "reasoning_1", "content": "Second episode"},
            2,
        ),
    ]

    parts, _thinking_steps = _build_stream_parts_from_events(events, message_content="")
    assert parts is not None
    assert len(parts) == 2
    assert parts[0]["label"] == "Thoughts"
    assert parts[0]["tool_event"]["content"] == "First episode"
    assert parts[1]["label"] == "Thoughts"
    assert parts[1]["tool_event"]["content"] == "Second episode"


def test_build_stream_parts_maps_duplicate_generated_png_uniquely():
    """Reload rematch must not collapse every generate_image onto the last file."""
    message_attachments = [
        SimpleNamespace(
            id="att-1",
            file_name="generated.png",
            content_type="image/png",
            content_url="https://example.test/1.png",
            data_base64=None,
        ),
        SimpleNamespace(
            id="att-2",
            file_name="generated.png",
            content_type="image/png",
            content_url="https://example.test/2.png",
            data_base64=None,
        ),
        SimpleNamespace(
            id="att-3",
            file_name="generated.png",
            content_type="image/png",
            content_url="https://example.test/3.png",
            data_base64=None,
        ),
    ]
    events = []
    sequence = 0
    for call_id in ("call:img-1", "call:img-2", "call:img-3"):
        sequence += 1
        events.append(
            _event(
                "activity",
                {"label": "Generating image", "state": "start"},
                sequence,
            )
        )
        sequence += 1
        events.append(
            _event(
                "tool_event",
                {
                    "type": "tool_call",
                    "id": call_id,
                    "tool_name": "generate_image",
                    "state": "end",
                    "action_summary": "Generating image",
                    "output": {
                        "status": "ok",
                        "attachments": [
                            {
                                "file_name": "generated.png",
                                "content_type": "image/png",
                                # Distinct payload that would previously be discarded
                                # once rematch preferred the last content_url.
                                "data_base64": f"payload-for-{call_id}",
                            }
                        ],
                    },
                },
                sequence,
            )
        )

    parts, _thinking_steps = _build_stream_parts_from_events(
        events,
        message_content="Created three images.",
        message_attachments=message_attachments,
    )

    assert parts is not None
    action_parts = [part for part in parts if part.get("type") == "action"]
    assert len(action_parts) == 3
    assert [part["attachments"][0]["id"] for part in action_parts] == [
        "att-1",
        "att-2",
        "att-3",
    ]
    assert [part["attachments"][0]["content_url"] for part in action_parts] == [
        "https://example.test/1.png",
        "https://example.test/2.png",
        "https://example.test/3.png",
    ]
