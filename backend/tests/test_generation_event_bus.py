from __future__ import annotations

from uuid import uuid4

from app.api.chats import _event_payload_from_notification
from app.services.generation_event_bus import generation_channel


def test_generation_channel_is_namespaced():
    task_id = uuid4()
    assert generation_channel(task_id) == f"chatui:gen:{task_id}"


def test_event_payload_from_notification_wraps_activity_and_tools():
    task_id = uuid4()
    activity = _event_payload_from_notification(
        task_id,
        {
            "sequence": 1,
            "event_type": "activity",
            "payload": {"label": "Thinking", "state": "start"},
        },
    )
    assert activity == {
        "activity": {"label": "Thinking", "state": "start"},
        "task_id": str(task_id),
    }

    tool_event = _event_payload_from_notification(
        task_id,
        {
            "sequence": 2,
            "event_type": "tool_event",
            "payload": {"type": "tool_call", "id": "call:1"},
        },
    )
    assert tool_event == {
        "tool_event": {"type": "tool_call", "id": "call:1"},
        "task_id": str(task_id),
    }

    delta = _event_payload_from_notification(
        task_id,
        {
            "sequence": 3,
            "event_type": "delta",
            "payload": {"delta": "hello"},
        },
    )
    assert delta == {"delta": "hello", "task_id": str(task_id)}
