from __future__ import annotations

from app.api.openai_compat import ChatCompletionRequest
from app.services.openai_completion_params import (
    anthropic_generation_kwargs,
    merge_openai_chat_completion_payload,
    merge_openai_responses_payload,
    params_from_chat_request,
)


def test_params_from_chat_request_includes_sampling_fields() -> None:
    payload = ChatCompletionRequest.model_validate(
        {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "hi"}],
            "temperature": 0.2,
            "max_completion_tokens": 500,
            "top_p": 0.9,
            "seed": 7,
        }
    )
    params = params_from_chat_request(payload)
    assert params["temperature"] == 0.2
    assert params["max_completion_tokens"] == 500
    assert params["top_p"] == 0.9
    assert params["seed"] == 7
    assert "messages" not in params
    assert "model" not in params


def test_merge_openai_responses_payload_maps_max_output_tokens() -> None:
    payload: dict = {"model": "gpt-4o", "input": []}
    merge_openai_responses_payload(
        payload,
        {"max_completion_tokens": 800, "temperature": 0.4, "user": "u-1"},
        default_reasoning_effort=None,
    )
    assert payload["max_output_tokens"] == 800
    assert payload["temperature"] == 0.4
    assert payload["user"] == "u-1"


def test_anthropic_generation_kwargs_maps_stop_sequences() -> None:
    kwargs = anthropic_generation_kwargs(
        {"max_tokens": 100, "stop": ["```", "END"]},
        default_max_tokens=16_384,
    )
    assert kwargs["max_tokens"] == 100
    assert kwargs["stop_sequences"] == ["```", "END"]


def test_merge_openai_chat_completion_payload_client_reasoning() -> None:
    target: dict = {"model": "gpt-4o", "messages": []}
    merge_openai_chat_completion_payload(
        target,
        {"temperature": 1.0, "reasoning_effort": "high"},
        default_reasoning_effort="medium",
    )
    assert target["temperature"] == 1.0
    assert target["reasoning_effort"] == "high"
