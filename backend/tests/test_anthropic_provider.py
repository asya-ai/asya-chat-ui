from types import SimpleNamespace

from app.services.providers.anthropic_provider import _tool_payload, _usage_from_anthropic
from app.services.providers.base import ChatToolSpec


def test_usage_from_anthropic_includes_cache_tokens():
    usage = SimpleNamespace(
        input_tokens=10,
        output_tokens=5,
        cache_read_input_tokens=30,
        cache_creation_input_tokens=20,
    )

    normalized = _usage_from_anthropic(usage)

    assert normalized.prompt_tokens == 60
    assert normalized.completion_tokens == 5
    assert normalized.total_tokens == 65
    assert normalized.input_tokens == 30
    assert normalized.output_tokens == 5
    assert normalized.cached_tokens == 30


def test_tool_payload_marks_last_tool_cacheable():
    tools = [
        ChatToolSpec(
            name="first_tool",
            description="First",
            parameters={"type": "object", "properties": {}},
        ),
        ChatToolSpec(
            name="second_tool",
            description="Second",
            parameters={"type": "object", "properties": {}},
        ),
    ]

    payload = _tool_payload(tools)

    assert "cache_control" not in payload[0]
    assert payload[1]["cache_control"] == {"type": "ephemeral"}
