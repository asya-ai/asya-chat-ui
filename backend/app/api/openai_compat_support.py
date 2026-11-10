from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from json_repair import loads as repair_json_loads
from sqlmodel import Session

from app.models import ChatModel, UsageEvent
from app.services.providers.base import ChatResponse, ChatToolSpec, ChatUsage


def usage_to_api(usage: ChatUsage) -> Any:
    from app.api.openai_compat import ChatCompletionUsage

    prompt_details = (
        {"cached_tokens": usage.cached_tokens} if usage.cached_tokens else None
    )
    completion_details = (
        {"reasoning_tokens": usage.thinking_tokens} if usage.thinking_tokens else None
    )
    wire_total = usage.prompt_tokens + usage.completion_tokens
    if wire_total == 0 and usage.total_tokens:
        wire_total = usage.total_tokens
    return ChatCompletionUsage(
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=wire_total,
        prompt_tokens_details=prompt_details,
        completion_tokens_details=completion_details,
    )


def stream_include_usage(stream_options: dict[str, Any] | None) -> bool:
    return bool(stream_options and stream_options.get("include_usage"))


def record_usage_event(
    session: Session,
    *,
    org_id: UUID,
    user_id: UUID,
    model: ChatModel,
    usage: ChatUsage,
) -> None:
    event = UsageEvent(
        org_id=org_id,
        user_id=user_id,
        chat_id=None,
        message_id=None,
        model_id=model.id,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cached_tokens=usage.cached_tokens,
        thinking_tokens=usage.thinking_tokens,
    )
    session.add(event)
    session.commit()


def _tool_calls_from_openai_message(message: Any) -> list[Any] | None:
    from app.api.openai_compat import (
        ChatCompletionToolCall,
        ChatCompletionToolCallFunction,
    )

    raw_calls = getattr(message, "tool_calls", None) or []
    if not raw_calls:
        return None
    parsed: list[ChatCompletionToolCall] = []
    for call in raw_calls:
        function = getattr(call, "function", None)
        if function is None:
            continue
        parsed.append(
            ChatCompletionToolCall(
                id=call.id,
                function=ChatCompletionToolCallFunction(
                    name=function.name,
                    arguments=function.arguments or "{}",
                ),
            )
        )
    return parsed or None


def completion_from_raw(
    raw: Any,
    *,
    model_name: str,
) -> Any:
    from app.api.openai_compat import (
        ChatCompletionChoice,
        ChatCompletionResponse,
        ChatCompletionUsage,
        ChatMessagePayload,
    )

    created = int(getattr(raw, "created", None) or datetime.now(timezone.utc).timestamp())
    completion_id = getattr(raw, "id", None) or f"chatcmpl-{uuid4().hex[:24]}"
    usage_obj = getattr(raw, "usage", None)
    cached = thinking = 0
    if usage_obj is not None:
        prompt_tokens = getattr(usage_obj, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage_obj, "completion_tokens", 0) or 0
        total_tokens = getattr(usage_obj, "total_tokens", 0) or (
            prompt_tokens + completion_tokens
        )
    else:
        prompt_tokens = completion_tokens = total_tokens = 0
    usage = ChatCompletionUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )
    choices: list[ChatCompletionChoice] = []
    for index, choice in enumerate(getattr(raw, "choices", []) or []):
        message = choice.message
        tool_calls = _tool_calls_from_openai_message(message)
        message_payload = ChatMessagePayload(
            role="assistant",
            content=getattr(message, "content", None),
            tool_calls=tool_calls,
        )
        choices.append(
            ChatCompletionChoice(
                index=index,
                message=message_payload,
                tool_calls=tool_calls,
                finish_reason=getattr(choice, "finish_reason", None) or "stop",
                logprobs=getattr(choice, "logprobs", None),
            )
        )
    return ChatCompletionResponse(
        id=completion_id,
        object=getattr(raw, "object", None) or "chat.completion",
        created=created,
        model=model_name,
        choices=choices,
        usage=usage,
    )


def completion_from_chat_response(
    response: ChatResponse,
    *,
    model_name: str,
) -> Any:
    from app.api.openai_compat import (
        ChatCompletionChoice,
        ChatCompletionResponse,
        ChatCompletionToolCall,
        ChatCompletionToolCallFunction,
        ChatMessagePayload,
    )

    if response.raw_completion is not None:
        return completion_from_raw(response.raw_completion, model_name=model_name)
    tool_calls = None
    if response.tool_calls:
        tool_calls = [
            ChatCompletionToolCall(
                id=call.id,
                function=ChatCompletionToolCallFunction(
                    name=call.name,
                    arguments=json.dumps(call.arguments, ensure_ascii=False),
                ),
                thought_signature=call.thought_signature,
            )
            for call in response.tool_calls
        ]
    finish_reason = response.finish_reason or ("tool_calls" if tool_calls else "stop")
    message = ChatMessagePayload(
        role="assistant",
        content=response.content,
        tool_calls=tool_calls,
    )
    return ChatCompletionResponse(
        id=f"chatcmpl-{uuid4().hex[:24]}",
        object="chat.completion",
        created=int(datetime.now(timezone.utc).timestamp()),
        model=model_name,
        choices=[
            ChatCompletionChoice(
                index=0,
                message=message,
                tool_calls=tool_calls,
                finish_reason=finish_reason,
            )
        ],
        usage=usage_to_api(response.usage),
    )


def stream_chunk_sse(
    *,
    completion_id: str,
    created: int,
    model: str,
    index: int,
    delta: dict[str, Any],
    finish_reason: str | None = None,
    usage: Any | None = None,
    usage_only: bool = False,
) -> str:
    body: dict[str, Any] = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [],
    }
    if usage_only:
        if usage is not None:
            body["usage"] = usage.model_dump(exclude_none=True)
    else:
        body["choices"] = [
            {
                "index": index,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ]
    return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"


def tool_calls_to_stream_delta(
    tool_calls: list[Any],
) -> list[dict[str, Any]]:
    deltas: list[dict[str, Any]] = []
    for call in tool_calls:
        deltas.append(
            {
                "index": getattr(call, "index", 0) or 0,
                "id": getattr(call, "id", None),
                "type": getattr(call, "type", None) or "function",
                "function": {
                    "name": getattr(getattr(call, "function", None), "name", None),
                    "arguments": getattr(
                        getattr(call, "function", None), "arguments", None
                    ),
                },
            }
        )
    return deltas


def chat_tools_from_specs(tools: list[Any]) -> list[ChatToolSpec]:
    specs: list[ChatToolSpec] = []
    for tool in tools or []:
        if tool.type != "function" or not tool.function:
            continue
        specs.append(
            ChatToolSpec(
                name=tool.function.name,
                description=tool.function.description or "",
                parameters=tool.function.parameters or {},
            )
        )
    return specs


def merge_stream_options(
    completion_params: dict[str, Any] | None,
    stream_options: dict[str, Any] | None,
) -> dict[str, Any] | None:
    merged: dict[str, Any] = {}
    if completion_params and completion_params.get("stream_options"):
        merged.update(completion_params["stream_options"])
    if stream_options:
        merged.update(stream_options)
    return merged or None


def provider_stream_options(
    client_stream_options: dict[str, Any] | None,
) -> dict[str, Any]:
    merged = dict(client_stream_options or {})
    merged.setdefault("include_usage", True)
    return merged


def tool_call_deltas_to_sse(tool_call_deltas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for delta in tool_call_deltas:
        entry: dict[str, Any] = {
            "index": delta.get("index", 0) or 0,
            "type": delta.get("type") or "function",
        }
        if delta.get("id"):
            entry["id"] = delta["id"]
        function = delta.get("function") or {}
        fn: dict[str, Any] = {}
        if function.get("name"):
            fn["name"] = function["name"]
        if function.get("arguments"):
            fn["arguments"] = function["arguments"]
        if fn:
            entry["function"] = fn
        out.append(entry)
    return out


def openai_error_body(
    message: str,
    *,
    error_type: str = "invalid_request_error",
    param: str | None = None,
    code: str | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"message": message, "type": error_type}
    if param is not None:
        error["param"] = param
    if code is not None:
        error["code"] = code
    return {"error": error}


def _rewrite_chat_sse_payload_to_text_completion(data: dict[str, Any]) -> dict[str, Any]:
    data = dict(data)
    data["object"] = "text_completion"
    chunk_id = data.get("id")
    if isinstance(chunk_id, str) and chunk_id.startswith("chatcmpl-"):
        data["id"] = f"cmpl-{chunk_id[len('chatcmpl-'):]}"
    for choice in data.get("choices") or []:
        delta = dict(choice.get("delta") or {})
        text = delta.pop("content", None)
        delta.pop("role", None)
        delta.pop("tool_calls", None)
        delta.pop("reasoning_content", None)
        if text is not None:
            delta["text"] = text
        choice["delta"] = delta
    return data


async def adapt_chat_completion_stream_to_text(stream: Any) -> Any:
    async for chunk in stream.body_iterator:
        if isinstance(chunk, bytes):
            text = chunk.decode("utf-8")
        else:
            text = str(chunk)
        out: list[str] = []
        for line in text.split("\n"):
            if not line.startswith("data: "):
                if line:
                    out.append(line)
                continue
            payload = line[6:].strip()
            if payload == "[DONE]":
                out.append(line)
                continue
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                out.append(line)
                continue
            out.append(
                "data: "
                + json.dumps(
                    _rewrite_chat_sse_payload_to_text_completion(data),
                    ensure_ascii=False,
                )
            )
        if out:
            yield "\n".join(out) + ("\n" if text.endswith("\n") else "")


def parse_stream_tool_calls(
    pending: dict[int, dict[str, str]],
    tool_calls: list[Any] | None,
) -> list[Any]:
    from app.api.openai_compat import (
        ChatCompletionToolCall,
        ChatCompletionToolCallFunction,
    )

    if tool_calls:
        for call in tool_calls:
            index = getattr(call, "index", 0) or 0
            entry = pending.setdefault(
                index, {"id": "", "name": "", "arguments": ""}
            )
            if getattr(call, "id", None):
                entry["id"] = call.id
            function = getattr(call, "function", None)
            if function is not None:
                if getattr(function, "name", None):
                    entry["name"] = function.name
                if getattr(function, "arguments", None):
                    entry["arguments"] += function.arguments

    parsed: list[ChatCompletionToolCall] = []
    for index in sorted(pending):
        entry = pending[index]
        arguments: dict[str, Any] = {}
        if entry["arguments"]:
            try:
                repaired = repair_json_loads(entry["arguments"])
                arguments = repaired if isinstance(repaired, dict) else {}
            except Exception:
                arguments = {}
        parsed.append(
            ChatCompletionToolCall(
                id=entry["id"] or f"call_{index}",
                function=ChatCompletionToolCallFunction(
                    name=entry["name"],
                    arguments=json.dumps(arguments, ensure_ascii=False),
                ),
            )
        )
    return parsed


def final_chunk_usage_from_stream(usage: ChatUsage | None) -> Any | None:
    if usage is None:
        return None
    return usage_to_api(usage)
