"""Map OpenAI-compatible client request fields onto provider payloads."""

from __future__ import annotations

from typing import Any


# Wired on ChatCompletionRequest; not re-merged onto provider payloads here.
CHAT_COMPLETION_RESERVED_KEYS = frozenset(
    {
        "model",
        "messages",
        "tools",
        "tool_choice",
        "stream",
        "stream_options",
    }
)

OPENAI_CHAT_FORWARD_KEYS = frozenset(
    {
        "temperature",
        "top_p",
        "n",
        "stop",
        "max_tokens",
        "max_completion_tokens",
        "presence_penalty",
        "frequency_penalty",
        "logit_bias",
        "user",
        "seed",
        "logprobs",
        "top_logprobs",
        "response_format",
        "audio",
        "parallel_tool_calls",
        "service_tier",
        "store",
        "metadata",
        "prediction",
        "reasoning_effort",
        "web_search_options",
        "modalities",
    }
)


def params_from_mapping(
    data: dict[str, Any],
    *,
    reserved: frozenset[str] = CHAT_COMPLETION_RESERVED_KEYS,
) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for key, value in data.items():
        if key in reserved or value is None:
            continue
        params[key] = value
    return params


def params_from_chat_request(model: Any) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        data = model.model_dump(mode="json", exclude_none=True)
    elif isinstance(model, dict):
        data = {k: v for k, v in model.items() if v is not None}
    else:
        return {}
    return params_from_mapping(data)


def _resolved_reasoning_effort(
    completion_params: dict[str, Any] | None,
    default_reasoning_effort: str | None,
) -> str | None:
    if completion_params and completion_params.get("reasoning_effort") not in {
        None,
        "",
    }:
        effort = str(completion_params["reasoning_effort"]).strip()
        return effort or None
    if default_reasoning_effort and default_reasoning_effort != "none":
        return default_reasoning_effort
    return None


def merge_openai_chat_completion_payload(
    payload: dict[str, Any],
    completion_params: dict[str, Any] | None,
    *,
    default_reasoning_effort: str | None,
) -> None:
    """Mutate an OpenAI chat.completions request payload."""
    params = completion_params or {}
    for key, value in params.items():
        if key in OPENAI_CHAT_FORWARD_KEYS and key != "reasoning_effort":
            payload[key] = value
        elif key not in CHAT_COMPLETION_RESERVED_KEYS and key not in payload:
            payload[key] = value
    reasoning = _resolved_reasoning_effort(params, default_reasoning_effort)
    if reasoning:
        payload["reasoning_effort"] = reasoning
    elif "reasoning_effort" in payload and not reasoning:
        payload.pop("reasoning_effort", None)


def merge_openai_responses_payload(
    payload: dict[str, Any],
    completion_params: dict[str, Any] | None,
    *,
    default_reasoning_effort: str | None,
) -> None:
    """Mutate an OpenAI /v1/responses request payload."""
    params = completion_params or {}
    if params.get("temperature") is not None:
        payload["temperature"] = params["temperature"]
    if params.get("top_p") is not None:
        payload["top_p"] = params["top_p"]
    stop = params.get("stop")
    if stop is not None:
        payload["stop"] = stop
    if params.get("metadata") is not None:
        payload["metadata"] = params["metadata"]
    if params.get("parallel_tool_calls") is not None:
        payload["parallel_tool_calls"] = params["parallel_tool_calls"]
    if params.get("store") is not None:
        payload["store"] = params["store"]
    if params.get("user") is not None:
        payload["user"] = params["user"]
    max_out = params.get("max_completion_tokens")
    if max_out is None:
        max_out = params.get("max_tokens")
    if max_out is not None:
        payload["max_output_tokens"] = max_out

    effort = _resolved_reasoning_effort(params, default_reasoning_effort)
    if effort:
        existing = payload.get("reasoning")
        if isinstance(existing, dict):
            payload["reasoning"] = {**existing, "effort": effort}
        else:
            payload["reasoning"] = {"effort": effort}


def anthropic_generation_kwargs(
    completion_params: dict[str, Any] | None,
    *,
    default_max_tokens: int,
) -> dict[str, Any]:
    params = completion_params or {}
    out: dict[str, Any] = {}
    max_tokens = params.get("max_completion_tokens") or params.get("max_tokens")
    if max_tokens is not None:
        out["max_tokens"] = int(max_tokens)
    else:
        out["max_tokens"] = default_max_tokens
    if params.get("temperature") is not None:
        out["temperature"] = params["temperature"]
    if params.get("top_p") is not None:
        out["top_p"] = params["top_p"]
    if params.get("stop") is not None:
        out["stop_sequences"] = (
            [params["stop"]] if isinstance(params["stop"], str) else params["stop"]
        )
    return out


def gemini_generation_kwargs(completion_params: dict[str, Any] | None) -> dict[str, Any]:
    params = completion_params or {}
    out: dict[str, Any] = {}
    if params.get("temperature") is not None:
        out["temperature"] = params["temperature"]
    if params.get("top_p") is not None:
        out["top_p"] = params["top_p"]
    max_out = params.get("max_completion_tokens") or params.get("max_tokens")
    if max_out is not None:
        out["max_output_tokens"] = int(max_out)
    return out
