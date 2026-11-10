from __future__ import annotations

from app.api import openai_compat_support as compat_support
from app.api.openai_compat import (
    ChatCompletionRequest,
    ChatCompletionToolCall,
    ChatCompletionToolCallFunction,
    ChatContentPart,
    ChatInputTextPart,
    ChatMessagePayload,
    _chat_content_to_text,
    _coerce_responses_input,
    _content_part_to_openai,
    _message_content_for_provider,
    _message_has_content,
    _normalize_provider_messages,
    _provider_tool_calls,
    _responses_sse_events,
)


def test_chat_content_to_text_joins_supported_parts_only() -> None:
    parts = [
        ChatInputTextPart(type="text", text="hello "),
        ChatInputTextPart(type="input_text", text="world"),
        ChatInputTextPart(type="image", text="ignored"),
    ]
    assert _chat_content_to_text(parts) == "hello world"


def test_content_part_to_openai_image_url() -> None:
    part = ChatContentPart(
        type="image_url",
        image_url={"url": "data:image/png;base64,YWJj", "detail": "high"},
    )
    assert _content_part_to_openai(part) == {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,YWJj", "detail": "high"},
    }


def test_content_part_to_openai_file_passthrough() -> None:
    part = {
        "type": "file",
        "file": {"file_data": "Zm9v", "filename": "invoice.pdf"},
    }
    assert _content_part_to_openai(part) == part


def test_message_has_content_image_without_text() -> None:
    parts = [
        ChatContentPart(
            type="image_url",
            image_url={"url": "https://example.com/a.png"},
        )
    ]
    assert _message_has_content(parts) is True
    assert _chat_content_to_text(parts) == ""


def test_message_content_for_provider_multimodal_openai_shape() -> None:
    parts = [
        ChatContentPart(type="text", text="Describe the image."),
        ChatContentPart(
            type="image_url",
            image_url={"url": "data:image/png;base64,abc"},
        ),
    ]
    assert _message_content_for_provider(parts) == [
        {"type": "text", "text": "Describe the image."},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
    ]


def test_message_content_for_provider_single_text_stays_string() -> None:
    assert _message_content_for_provider("hello") == "hello"
    assert _message_content_for_provider(
        [ChatContentPart(type="text", text="hello")]
    ) == "hello"


def test_normalize_provider_messages_forwards_image_url_to_provider() -> None:
    messages = [
        ChatMessagePayload(
            role="user",
            content=[
                ChatContentPart(type="text", text="Describe the image."),
                ChatContentPart(
                    type="image_url",
                    image_url={"url": "data:image/png;base64,abc"},
                ),
            ],
        )
    ]
    provider_messages, dropped, coerced = _normalize_provider_messages(messages)
    assert dropped == 0
    assert coerced == 0
    assert provider_messages == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe the image."},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,abc"},
                },
            ],
        }
    ]


def test_chat_completion_request_openai_multimodal_json() -> None:
    payload = ChatCompletionRequest.model_validate(
        {
            "model": "anthropic/claude-sonnet-4",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Describe the image."},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/png;base64,iVBORw0KGgo",
                                "detail": "auto",
                            },
                        },
                    ],
                }
            ],
        }
    )
    assert payload.messages[0].content is not None
    assert isinstance(payload.messages[0].content, list)
    assert payload.messages[0].content[1].type == "image_url"
    assert payload.messages[0].content[1].image_url is not None
    assert payload.messages[0].content[1].image_url.url.startswith("data:image/png")


def test_coerce_responses_input_preserves_input_image() -> None:
    raw_input = [
        {
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": "see this"},
                {
                    "type": "input_image",
                    "image_url": "https://example.com/a.png",
                },
            ],
        }
    ]
    messages, input_type = _coerce_responses_input(raw_input)
    assert input_type == "array"
    assert len(messages) == 1
    content = messages[0].content
    assert isinstance(content, list)
    assert content[0].type == "text"
    assert content[0].text == "see this"
    assert content[1].type == "image_url"
    assert content[1].image_url is not None
    assert content[1].image_url.url == "https://example.com/a.png"
    provider_messages, _, _ = _normalize_provider_messages(messages)
    assert provider_messages[0]["content"][1]["type"] == "image_url"


def test_usage_to_api_exposes_cached_and_reasoning_details() -> None:
    from app.services.providers.base import ChatUsage

    api_usage = compat_support.usage_to_api(
        ChatUsage(
            prompt_tokens=100,
            completion_tokens=20,
            total_tokens=261,
            input_tokens=90,
            output_tokens=20,
            cached_tokens=10,
            thinking_tokens=5,
        )
    )
    assert api_usage.total_tokens == 120
    assert api_usage.prompt_tokens_details == {"cached_tokens": 10}
    assert api_usage.completion_tokens_details == {"reasoning_tokens": 5}


def test_stream_include_usage_respects_client_flag() -> None:
    assert compat_support.stream_include_usage(None) is False
    assert compat_support.stream_include_usage({}) is False
    assert compat_support.stream_include_usage({"include_usage": True}) is True


def test_provider_stream_options_always_requests_upstream_usage() -> None:
    assert compat_support.provider_stream_options(None) == {"include_usage": True}
    assert compat_support.provider_stream_options({"include_usage": False}) == {
        "include_usage": False
    }


def test_normalize_provider_messages_developer_role_becomes_system_in_request() -> None:
    from app.api.openai_compat import _normalized_role

    assert _normalized_role("developer") == "system"


def test_chat_completion_request_accepts_file_content_part() -> None:
    payload = ChatCompletionRequest.model_validate(
        {
            "model": "gpt-4o",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "file",
                            "file": {
                                "filename": "invoice.pdf",
                                "file_data": "JVBERi0x",
                            },
                        }
                    ],
                }
            ],
        }
    )
    assert payload.messages[0].content is not None
    assert isinstance(payload.messages[0].content, list)
    assert payload.messages[0].content[0].type == "file"
    assert payload.messages[0].content[0].file is not None


def test_message_content_for_provider_file_part() -> None:
    parts = [
        ChatContentPart(
            type="file",
            file={"filename": "invoice.pdf", "file_data": "abc"},
        )
    ]
    assert _message_content_for_provider(parts) == [
        {"type": "file", "file": {"filename": "invoice.pdf", "file_data": "abc"}}
    ]


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


def test_provider_tool_calls_repairs_malformed_json_arguments() -> None:
    calls = [
        ChatCompletionToolCall(
            id="call-1",
            function=ChatCompletionToolCallFunction(
                name="weather",
                arguments='{"city":"Riga",}',
            ),
        )
    ]
    assert _provider_tool_calls(calls) == [
        {"id": "call-1", "name": "weather", "arguments": {"city": "Riga"}}
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
        {
            "role": "tool",
            "content": "sunny",
            "tool_call_id": "call-1",
            "name": "weather",
        },
    ]
    assert dropped == 0
    assert coerced == 0


def test_normalize_provider_messages_drops_empty_orphan_tool_message() -> None:
    messages = [ChatMessagePayload(role="tool", content="")]
    provider_messages, dropped, coerced = _normalize_provider_messages(messages)
    assert provider_messages == []
    assert dropped == 1
    assert coerced == 0


def test_provider_tool_calls_preserves_thought_signature() -> None:
    calls = [
        ChatCompletionToolCall(
            id="call-1",
            function=ChatCompletionToolCallFunction(name="weather", arguments="{}"),
            thought_signature="sig-abc",
        )
    ]
    assert _provider_tool_calls(calls) == [
        {
            "id": "call-1",
            "name": "weather",
            "arguments": {},
            "thought_signature": "sig-abc",
        }
    ]


def test_responses_sse_events_emits_created_delta_completed() -> None:
    payload = {
        "id": "resp_test",
        "model": "gemini-flash",
        "created_at": 123,
        "output_text": "Hi",
        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
    }
    events = _responses_sse_events(payload)
    joined = "".join(events)
    assert "event: response.created" in joined
    assert "response.output_text.delta" in joined
    assert "event: response.completed" in joined
    assert '"delta": "Hi"' in joined


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
