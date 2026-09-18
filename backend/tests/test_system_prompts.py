from app.services.system_prompts import MAIN_SYSTEM_PROMPT, build_system_prompt_messages


def test_system_prompt_keeps_stable_prefix_before_dynamic_clock() -> None:
    messages = build_system_prompt_messages(
        locale="en",
        timezone="UTC",
        enabled_tool_names=["web_search", "store_memory"],
        memories=[{"id": "m1", "content": "likes dark mode"}],
    )

    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == MAIN_SYSTEM_PROMPT
    assert "Current time" not in messages[0]["content"]

    assert messages[-1]["role"] == "system"
    assert "Current time" in messages[-1]["content"]

    contents = [msg["content"] for msg in messages]
    memory_idx = next(i for i, text in enumerate(contents) if "likes dark mode" in text)
    time_idx = len(contents) - 1
    assert memory_idx < time_idx
    # Tool guidance stays in the stable middle of the prefix (before memories/clock).
    assert any("web" in text.lower() or "search" in text.lower() for text in contents[1:memory_idx])
