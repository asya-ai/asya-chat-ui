from datetime import datetime, timezone

from app.services.system_prompts import (
    MAIN_SYSTEM_PROMPT,
    MEMORY_SYSTEM_PROMPT,
    attach_user_context_stamps,
    build_request_context_block,
    build_system_prompt_messages,
    provider_chat_message,
)


def test_system_prompt_is_stable_without_clock_or_memories() -> None:
    messages = build_system_prompt_messages(
        locale="en",
        enabled_tool_names=["web_search", "store_memory"],
    )

    assert messages[0] == {"role": "system", "content": MAIN_SYSTEM_PROMPT}
    contents = [msg["content"] for msg in messages]
    assert all("Current UTC time" not in text for text in contents)
    assert all("likes dark mode" not in text for text in contents)
    assert MEMORY_SYSTEM_PROMPT in contents
    assert any("web_search" in text for text in contents)


def test_request_context_block_freezes_time_from_created_at() -> None:
    at = datetime(2026, 9, 18, 15, 42, 59, tzinfo=timezone.utc)
    block = build_request_context_block(timezone="UTC", at=at, include_memories=False)

    assert "<current_time>" in block
    assert "2026-09-18" in block
    assert "15:42 UTC" in block
    # Floored to the minute — seconds must not appear in the stamp.
    assert "15:42:59" not in block


def test_request_context_block_includes_memories_when_requested() -> None:
    block = build_request_context_block(
        timezone="UTC",
        memories=[{"id": "m1", "content": "likes dark mode"}],
        include_memories=True,
    )

    assert "<memories>" in block
    assert "[m1] likes dark mode" in block


def test_attach_stamps_every_user_message_with_frozen_time() -> None:
    t1 = datetime(2026, 9, 18, 10, 0, 0)
    t2 = datetime(2026, 9, 18, 10, 5, 0)
    messages = [
        provider_chat_message("user", "first", created_at=t1),
        provider_chat_message("assistant", "ok"),
        provider_chat_message("user", "second", created_at=t2),
    ]

    updated = attach_user_context_stamps(
        messages,
        timezone="UTC",
        memories=[{"id": "m1", "content": "likes dark mode"}],
        include_memories=True,
    )

    assert updated[0]["content"].startswith("<context>")
    assert "10:00 UTC" in updated[0]["content"]
    assert "<memories>" not in updated[0]["content"]
    assert updated[0]["content"].endswith("first")
    assert "_created_at" not in updated[0]

    assert updated[1]["content"] == "ok"

    assert "10:05 UTC" in updated[2]["content"]
    assert "<memories>" in updated[2]["content"]
    assert "[m1] likes dark mode" in updated[2]["content"]
    assert updated[2]["content"].endswith("second")


def test_frozen_stamps_are_stable_across_reattach() -> None:
    t1 = datetime(2026, 9, 18, 10, 0, 30)
    messages = [provider_chat_message("user", "hello", created_at=t1)]
    first = attach_user_context_stamps(messages, timezone="UTC")
    # Reconstruct as a later request would: same created_at, raw content again.
    second = attach_user_context_stamps(
        [provider_chat_message("user", "hello", created_at=t1)],
        timezone="UTC",
    )
    assert first[0]["content"] == second[0]["content"]
    assert "10:00 UTC" in first[0]["content"]


def test_attach_context_prepends_text_part_for_multimodal_user_message() -> None:
    t1 = datetime(2026, 9, 18, 12, 0, 0, tzinfo=timezone.utc)
    messages = [
        provider_chat_message(
            "user",
            [
                {"type": "text", "text": "see this"},
                {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
            ],
            created_at=t1,
        )
    ]

    updated = attach_user_context_stamps(messages, timezone="UTC")
    content = updated[0]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert content[0]["text"].startswith("<context>")
    assert content[1]["text"] == "see this"
