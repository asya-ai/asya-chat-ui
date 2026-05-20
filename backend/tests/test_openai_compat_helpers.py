from __future__ import annotations

from app.api.openai_compat import (
    ChatCompletionToolCall,
    ChatCompletionToolCallFunction,
    ChatInputTextPart,
    ChatMessagePayload,
    _chat_content_to_text,
    _coerce_responses_input,
    _normalize_provider_messages,
    _provider_tool_calls,
)


def test_chat_content_to_text_joins_supported_parts_only() -> None:
    parts = [
        ChatInputTextPart(type="text", text="hello "),
        ChatInputTextPart(type="input_text", text="world"),
        ChatInputTextPart(type="image", text="ignored"),
    ]
    assert _chat_content_to_text(parts) == "hello world"


def test_provider_tool_calls_handles_invalid_json_arguments() -> None:
    calls = [
        ChatCompletionToolCall(
            id="call-1",
            function=ChatCompletionToolCallFunction(name="weather", arguments="{not-json"),
        )
    ]
    assert _provider_tool_calls(calls) == [
        {"id": "call-1", "name": "weather", "arguments": {}}
    ]


def test_normalize_provider_messages_coerces_orphan_tool_output() -> None:
    messages = [
        ChatMessagePayload(
            role="assistant",
            content="",
            tool_calls=[
                ChatCompletionToolCall(
                    id="call-1",
                    function=ChatCompletionToolCallFunction(
                        name="weather",
                        arguments='{"city":"Riga"}',
                    ),
                )
            ],
        ),
        ChatMessagePayload(role="tool", content="sunny"),
    ]

    provider_messages, dropped, coerced = _normalize_provider_messages(messages)

    assert provider_messages == [
        {
            "role": "assistant",
            "tool_calls": [
                {"id": "call-1", "name": "weather", "arguments": {"city": "Riga"}}
            ],
        },
        {"role": "tool", "content": "sunny", "tool_call_id": "call-1"},
    ]
    assert dropped == 0
    assert coerced == 0


def test_normalize_provider_messages_drops_empty_orphan_tool_message() -> None:
    messages = [ChatMessagePayload(role="tool", content="")]
    provider_messages, dropped, coerced = _normalize_provider_messages(messages)
    assert provider_messages == []
    assert dropped == 1
    assert coerced == 0


def test_coerce_responses_input_maps_function_call_roundtrip() -> None:
    raw_input = [
        {"type": "message", "role": "user", "content": "Ping"},
        {
            "type": "function_call",
            "id": "call-123",
            "name": "search",
            "arguments": {"q": "hello"},
        },
        {
            "type": "function_call_output",
            "call_id": "call-123",
            "output": "tool result",
        },
    ]

    messages, input_type = _coerce_responses_input(raw_input)

    assert input_type == "array"
    assert len(messages) == 3
    assert messages[0].role == "user"
    assert messages[0].content == "Ping"
    assert messages[1].role == "assistant"
    assert messages[1].tool_calls and messages[1].tool_calls[0].id == "call-123"
    assert messages[2].role == "tool"
    assert messages[2].tool_call_id == "call-123"
    assert messages[2].content == "tool result"
