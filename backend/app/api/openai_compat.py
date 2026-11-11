from datetime import datetime, timezone
import logging
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from json_repair import loads as repair_json_loads
from pydantic import BaseModel, ConfigDict, model_validator
from sqlmodel import Session, select
import json

from app.api.deps import AuthContext, get_auth_context, get_db
from app.core.config import settings
from app.models import ChatModel, OrgProviderConfig, UsageEvent
from app.services.org_service import require_provider_enabled
from app.services.team_service import allowed_model_ids
from app.services.model_capabilities import (
    persist_responses_api_discovery,
    prefer_responses_api_for_model,
)
from app.services.providers.base import ChatToolSpec
from app.services.instance_providers import get_provider_for_org
from app.services.openai_completion_params import params_from_chat_request
from app.api import openai_compat_support as compat_support

router = APIRouter(prefix="/v1", tags=["openai-compat"])
logger = logging.getLogger(__name__)


class ChatMessagePayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: str
    content: str | list["ChatContentPart"] | None = None
    tool_calls: list["ChatCompletionToolCall"] | None = None
    tool_call_id: str | None = None
    name: str | None = None
    refusal: str | None = None


class ChatImageURL(BaseModel):
    model_config = ConfigDict(extra="allow")

    url: str
    detail: str | None = None


class ChatContentPart(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str
    text: str | None = None
    image_url: ChatImageURL | dict[str, Any] | None = None
    file: dict[str, Any] | None = None
    input_audio: dict[str, Any] | None = None


# Backward-compatible alias for tests and callers.
ChatInputTextPart = ChatContentPart


class ChatCompletionToolCallFunction(BaseModel):
    name: str
    arguments: str


class ChatCompletionToolCall(BaseModel):
    id: str
    type: str = "function"
    function: ChatCompletionToolCallFunction
    # Required for Gemini multi-turn tool roundtrips; clients should echo this field.
    thought_signature: str | None = None


class ToolFunction(BaseModel):
    name: str
    description: str | None = None
    parameters: dict | None = None


class ToolSpec(BaseModel):
    type: str
    function: ToolFunction | None = None
    # Accept OpenAI-style alternatives where function fields are top-level.
    name: str | None = None
    description: str | None = None
    parameters: dict | None = None

    @model_validator(mode="after")
    def _normalize_function(self) -> "ToolSpec":
        if self.type != "function":
            return self
        if self.function is None and self.name:
            self.function = ToolFunction(
                name=self.name,
                description=self.description,
                parameters=self.parameters,
            )
        if self.function is None:
            raise ValueError("Function tool spec is missing function definition")
        return self


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str
    messages: list[ChatMessagePayload]
    temperature: float | None = None
    top_p: float | None = None
    n: int | None = None
    stream: bool | None = False
    stream_options: dict[str, Any] | None = None
    stop: str | list[str] | None = None
    max_tokens: int | None = None
    max_completion_tokens: int | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    logit_bias: dict[str, int] | None = None
    logprobs: bool | None = None
    top_logprobs: int | None = None
    user: str | None = None
    seed: int | None = None
    tools: list[ToolSpec] | None = None
    tool_choice: object | None = None
    parallel_tool_calls: bool | None = None
    response_format: dict[str, Any] | None = None
    audio: dict[str, Any] | None = None
    service_tier: str | None = None
    store: bool | None = None
    metadata: dict[str, Any] | None = None
    prediction: dict[str, Any] | None = None
    reasoning_effort: str | None = None
    web_search_options: dict[str, Any] | None = None
    modalities: list[str] | None = None


class ChatCompletionChoice(BaseModel):
    model_config = ConfigDict(extra="allow")

    index: int
    message: ChatMessagePayload
    # Kept for backward compatibility with existing callers.
    tool_calls: list[ChatCompletionToolCall] | None = None
    finish_reason: str | None = None
    logprobs: Any | None = None


class ChatCompletionUsage(BaseModel):
    model_config = ConfigDict(extra="allow")

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    prompt_tokens_details: dict[str, Any] | None = None
    completion_tokens_details: dict[str, Any] | None = None


class ChatCompletionResponse(BaseModel):
    id: str
    object: str
    created: int
    model: str
    choices: list[ChatCompletionChoice]
    usage: ChatCompletionUsage


class ResponseInputText(BaseModel):
    type: str = "input_text"
    text: str | None = None


class ResponseInputMessage(BaseModel):
    type: str | None = None
    role: str | None = "user"
    content: str | list[ResponseInputText] | None = None
    call_id: str | None = None
    output: str | None = None


class ResponseCreateRequest(BaseModel):
    # OpenAI's Responses API includes many optional fields (e.g. stream, stream_options).
    # Allow passthrough for OpenAI-compatible callers like Continue.
    model_config = {"extra": "allow"}
    model: str
    input: Any
    temperature: float | None = None
    max_output_tokens: int | None = None
    tools: list[ToolSpec] | None = None
    tool_choice: object | None = None
    stream: bool | None = None


class ResponseOutputText(BaseModel):
    type: str = "output_text"
    text: str
    annotations: list[dict[str, Any]] = []


class ResponseOutputMessage(BaseModel):
    id: str = ""
    type: str = "message"
    status: str = "completed"
    role: str = "assistant"
    content: list[ResponseOutputText]


class ResponseOutputFunctionCall(BaseModel):
    id: str = ""
    type: str = "function_call"
    status: str = "completed"
    call_id: str
    name: str
    arguments: str


class ResponseUsage(BaseModel):
    input_tokens: int
    output_tokens: int
    total_tokens: int


class ResponseCreateResponse(BaseModel):
    id: str
    object: str = "response"
    created: int
    created_at: int
    status: str = "completed"
    model: str
    output: list[dict[str, Any]]
    output_text: str = ""
    error: dict[str, Any] | None = None
    incomplete_details: dict[str, Any] | None = None
    parallel_tool_calls: bool = True
    usage: ResponseUsage


def _response_content_to_text(content: str | list[ResponseInputText] | None) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    return "".join(
        (part.text or "")
        for part in content
        if part.type in {"input_text", "text", "output_text"}
    ).strip()


def _normalized_role(role: str) -> str:
    # `developer` is a Responses API role; map to system prompt semantics.
    return "system" if role == "developer" else role


def _chat_content_to_text(value: str | list[ChatContentPart] | None) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "".join(
            (part.text or "")
            for part in value
            if part.type in {"text", "input_text", "output_text"}
        ).strip()
    return ""


def _content_part_to_openai(part: ChatContentPart | dict[str, Any]) -> dict[str, Any] | None:
    if isinstance(part, ChatContentPart):
        raw = part.model_dump(mode="json", exclude_none=True)
    elif isinstance(part, dict):
        raw = dict(part)
    else:
        return None

    part_type = str(raw.get("type") or "").lower()
    if part_type in {"text", "input_text", "output_text"}:
        text = raw.get("text")
        if text is not None and str(text).strip():
            return {"type": "text", "text": str(text)}
        return None
    if part_type == "image_url":
        image_url = raw.get("image_url")
        if isinstance(image_url, dict) and str(image_url.get("url") or "").strip():
            return {"type": "image_url", "image_url": image_url}
        return None
    if part_type == "file" and isinstance(raw.get("file"), dict):
        return raw
    if part_type == "input_audio" and isinstance(raw.get("input_audio"), dict):
        return raw
    if part_type:
        return raw
    return None


def _message_has_content(value: str | list[ChatContentPart] | None) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return any(_content_part_to_openai(part) for part in value)
    return False


def _message_content_for_provider(
    value: str | list[ChatContentPart] | None,
) -> str | list[dict[str, Any]]:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = [
            converted
            for part in value
            if (converted := _content_part_to_openai(part)) is not None
        ]
        if not parts:
            return ""
        has_non_text = any(p.get("type") != "text" for p in parts)
        if not has_non_text and len(parts) == 1:
            return str(parts[0].get("text") or "")
        return parts
    return ""


def _response_input_part_to_chat(part: dict[str, Any]) -> ChatContentPart | None:
    part_type = str(part.get("type") or "").lower()
    if part_type in {"input_text", "text", "output_text"}:
        text = str(part.get("text") or "")
        if text.strip():
            return ChatContentPart(type="text", text=text)
        return None
    if part_type in {"input_image", "image_url"}:
        image_url = part.get("image_url")
        if isinstance(image_url, str) and image_url.strip():
            return ChatContentPart(
                type="image_url",
                image_url=ChatImageURL(url=image_url),
            )
        if isinstance(image_url, dict) and str(image_url.get("url") or "").strip():
            return ChatContentPart(type="image_url", image_url=image_url)
        url = part.get("url")
        if isinstance(url, str) and url.strip():
            return ChatContentPart(
                type="image_url",
                image_url=ChatImageURL(url=url),
            )
        return None
    if part_type == "file" and isinstance(part.get("file"), dict):
        return ChatContentPart(type="file", file=part["file"])
    text = str(part.get("text") or "").strip()
    if text:
        return ChatContentPart(type=part_type or "text", text=text)
    return ChatContentPart.model_validate(part)


def _provider_tool_calls(
    tool_calls: list[ChatCompletionToolCall] | None,
) -> list[dict[str, Any]]:
    provider_calls: list[dict[str, Any]] = []
    for call in tool_calls or []:
        try:
            parsed_arguments = repair_json_loads(call.function.arguments or "{}")
            if not isinstance(parsed_arguments, dict):
                parsed_arguments = {}
        except Exception:
            parsed_arguments = {}
        provider_call: dict[str, Any] = {
            "id": call.id,
            "name": call.function.name,
            "arguments": parsed_arguments,
        }
        if call.thought_signature:
            provider_call["thought_signature"] = call.thought_signature
        provider_calls.append(provider_call)
    return provider_calls


def _normalize_provider_messages(
    normalized_messages: list[ChatMessagePayload],
) -> tuple[list[dict[str, Any]], int, int]:
    provider_messages: list[dict[str, Any]] = []
    pending_tool_call_ids: set[str] = set()
    pending_tool_call_names: dict[str, str] = {}
    dropped_tool_messages = 0
    coerced_orphan_tool_messages = 0
    for message in normalized_messages:
        role = message.role
        if role == "assistant":
            payload_message: dict[str, Any] = {"role": "assistant"}
            content = _message_content_for_provider(message.content)
            if isinstance(content, str):
                if content:
                    payload_message["content"] = content
            elif content:
                payload_message["content"] = content
            tool_calls = _provider_tool_calls(message.tool_calls)
            if tool_calls:
                payload_message["tool_calls"] = tool_calls
                for call in tool_calls:
                    call_id = str(call.get("id") or "").strip()
                    call_name = str(call.get("name") or "").strip()
                    if call_id:
                        pending_tool_call_ids.add(call_id)
                    if call_id and call_name:
                        pending_tool_call_names[call_id] = call_name
            if payload_message.get("content") is not None or tool_calls:
                provider_messages.append(payload_message)
            continue
        if role == "tool":
            content_text = _chat_content_to_text(message.content)
            tool_call_id = (message.tool_call_id or "").strip()
            if not tool_call_id and len(pending_tool_call_ids) == 1:
                # Some clients omit tool_call_id when only one call is pending.
                tool_call_id = next(iter(pending_tool_call_ids))
            if not tool_call_id or tool_call_id not in pending_tool_call_ids:
                # Preserve context even if the tool linkage is malformed.
                if content_text:
                    provider_messages.append(
                        {
                            "role": "user",
                            "content": f"Tool output:\n{content_text}",
                        }
                    )
                    coerced_orphan_tool_messages += 1
                else:
                    dropped_tool_messages += 1
                continue
            tool_name = pending_tool_call_names.get(tool_call_id)
            tool_payload: dict[str, Any] = {
                "role": "tool",
                "content": content_text,
                "tool_call_id": tool_call_id,
            }
            if tool_name:
                tool_payload["name"] = tool_name
            provider_messages.append(tool_payload)
            pending_tool_call_ids.discard(tool_call_id)
            pending_tool_call_names.pop(tool_call_id, None)
            continue
        content = _message_content_for_provider(message.content)
        if isinstance(content, str) and not content:
            continue
        if isinstance(content, list) and not content:
            continue
        provider_messages.append({"role": role, "content": content})
    return provider_messages, dropped_tool_messages, coerced_orphan_tool_messages


def _coerce_responses_input(raw_input: Any) -> tuple[list[ChatMessagePayload], str]:
    if isinstance(raw_input, str):
        return [ChatMessagePayload(role="user", content=raw_input)], "string"
    if isinstance(raw_input, dict):
        raw_input = [raw_input]
    if not isinstance(raw_input, list):
        return [ChatMessagePayload(role="user", content=str(raw_input))], type(raw_input).__name__

    messages: list[ChatMessagePayload] = []
    for item in raw_input:
        if isinstance(item, str):
            messages.append(ChatMessagePayload(role="user", content=item))
            continue
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "message").lower()
        if item_type == "function_call_output":
            output_text = str(item.get("output") or "").strip()
            if not output_text:
                output_text = _response_content_to_text(item.get("content"))
            messages.append(
                ChatMessagePayload(
                    role="tool",
                    content=output_text,
                    tool_call_id=(item.get("call_id") or "").strip() or None,
                )
            )
            continue
        if item_type == "function_call":
            call_id = str(item.get("call_id") or item.get("id") or "").strip()
            tool_name = str(item.get("name") or "").strip()
            raw_arguments = item.get("arguments")
            if isinstance(raw_arguments, str):
                arguments = raw_arguments
            elif raw_arguments is None:
                arguments = "{}"
            else:
                arguments = json.dumps(raw_arguments, ensure_ascii=False, default=str)
            if call_id and tool_name:
                messages.append(
                    ChatMessagePayload(
                        role="assistant",
                        content="",
                        tool_calls=[
                            ChatCompletionToolCall(
                                id=call_id,
                                function=ChatCompletionToolCallFunction(
                                    name=tool_name,
                                    arguments=arguments,
                                ),
                            )
                        ],
                    )
                )
            continue
        role = _normalized_role(str(item.get("role") or "user"))
        content_value = item.get("content")
        if isinstance(content_value, str):
            messages.append(
                ChatMessagePayload(role=role, content=content_value.strip())
            )
            continue
        if isinstance(content_value, list):
            content_parts: list[ChatContentPart] = []
            for part in content_value:
                if isinstance(part, str):
                    if part.strip():
                        content_parts.append(ChatContentPart(type="text", text=part))
                    continue
                if not isinstance(part, dict):
                    continue
                converted = _response_input_part_to_chat(part)
                if converted is not None:
                    content_parts.append(converted)
            if content_parts:
                messages.append(ChatMessagePayload(role=role, content=content_parts))
            else:
                messages.append(ChatMessagePayload(role=role, content=""))
            continue
        if content_value is None:
            messages.append(ChatMessagePayload(role=role, content=""))
            continue
        messages.append(ChatMessagePayload(role=role, content=str(content_value).strip()))
    return messages, "array"


class LegacyCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str
    prompt: str | list[int] | list[str] | list[list[int]] | None = None
    suffix: str | None = None
    max_tokens: int | None = None
    max_completion_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    n: int | None = None
    stream: bool | None = False
    logprobs: int | None = None
    echo: bool | None = None
    stop: str | list[str] | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    best_of: int | None = None
    logit_bias: dict[str, int] | None = None
    user: str | None = None
    seed: int | None = None


class LegacyCompletionChoice(BaseModel):
    text: str
    index: int
    logprobs: Any | None = None
    finish_reason: str | None = None


class LegacyCompletionResponse(BaseModel):
    id: str
    object: str = "text_completion"
    created: int
    model: str
    choices: list[LegacyCompletionChoice]
    usage: ChatCompletionUsage


def _prompt_to_text(prompt: Any) -> str:
    if prompt is None:
        return ""
    if isinstance(prompt, str):
        return prompt
    if isinstance(prompt, list):
        if not prompt:
            return ""
        if all(isinstance(item, int) for item in prompt):
            return ""
        if all(isinstance(item, str) for item in prompt):
            return "".join(prompt)
        if all(isinstance(item, list) for item in prompt):
            return "".join(_prompt_to_text(item) for item in prompt)
    return str(prompt)


class EmbeddingRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str
    input: str | list[str]
    encoding_format: str | None = None
    dimensions: int | None = None
    user: str | None = None


class EmbeddingData(BaseModel):
    object: str = "embedding"
    embedding: list[float]
    index: int


class EmbeddingUsage(BaseModel):
    prompt_tokens: int
    total_tokens: int


class EmbeddingResponse(BaseModel):
    object: str = "list"
    data: list[EmbeddingData]
    model: str
    usage: EmbeddingUsage


class ModelListItem(BaseModel):
    id: str
    object: str = "model"
    created: int
    owned_by: str = "organization"


def _model_list_item(model: ChatModel) -> ModelListItem:
    # Stable per-model unix time (ChatModel has no created_at column).
    created_ts = (int.from_bytes(model.id.bytes[:4], "big") % 800_000_000) + 1_500_000_000
    return ModelListItem(
        id=model.model_name,
        created=created_ts,
        owned_by=model.provider or "organization",
    )


class ModelListResponse(BaseModel):
    object: str = "list"
    data: list[ModelListItem]


def _responses_sse_events(response_payload: dict[str, Any]) -> list[str]:
    """Minimal OpenAI Responses SSE for providers we emulate via chat completions."""
    response_id = str(response_payload.get("id") or f"resp_{uuid4().hex[:24]}")
    created_at = int(
        response_payload.get("created_at")
        or response_payload.get("created")
        or datetime.now(timezone.utc).timestamp()
    )
    model = str(response_payload.get("model") or "")
    output_text = str(response_payload.get("output_text") or "")
    in_progress = {
        "id": response_id,
        "object": "response",
        "created_at": created_at,
        "status": "in_progress",
        "model": model,
        "output": [],
    }
    events: list[str] = [
        f"event: response.created\ndata: {json.dumps({'type': 'response.created', 'response': in_progress}, ensure_ascii=False)}\n\n",
    ]
    if output_text:
        delta = {
            "type": "response.output_text.delta",
            "item_id": "msg_0",
            "output_index": 0,
            "content_index": 0,
            "delta": output_text,
        }
        events.append(
            f"event: response.output_text.delta\ndata: {json.dumps(delta, ensure_ascii=False)}\n\n"
        )
    completed = dict(response_payload)
    completed.setdefault("id", response_id)
    completed.setdefault("object", "response")
    completed.setdefault("created_at", created_at)
    completed.setdefault("created", created_at)
    completed["status"] = "completed"
    events.append(
        f"event: response.completed\ndata: {json.dumps({'type': 'response.completed', 'response': completed, 'sequence_number': 0}, ensure_ascii=False)}\n\n"
    )
    return events


def _response_payload_from_completion(
    completion: ChatCompletionResponse,
) -> dict[str, Any]:
    choice = completion.choices[0]
    output_text = choice.message.content or ""
    output_items: list[ResponseOutputMessage | ResponseOutputFunctionCall] = []
    if output_text:
        output_items.append(
            ResponseOutputMessage(
                id=f"msg_{uuid4().hex[:24]}",
                content=[ResponseOutputText(text=output_text)],
            )
        )
    response_tool_calls = choice.message.tool_calls or choice.tool_calls or []
    for tool_call in response_tool_calls:
        output_items.append(
            ResponseOutputFunctionCall(
                id=f"fc_{uuid4().hex[:24]}",
                call_id=tool_call.id,
                name=tool_call.function.name,
                arguments=tool_call.function.arguments,
            )
        )
    response_payload = ResponseCreateResponse(
        id=completion.id.replace("chatcmpl-", "resp_"),
        created=completion.created,
        created_at=completion.created,
        model=completion.model,
        output=[item.model_dump(mode="json") for item in output_items],
        output_text=output_text,
        usage=ResponseUsage(
            input_tokens=completion.usage.prompt_tokens,
            output_tokens=completion.usage.completion_tokens,
            total_tokens=completion.usage.total_tokens,
        ),
    )
    return response_payload.model_dump(mode="json")


def resolve_model(session: Session, model_ref: str) -> ChatModel:
    model = None
    try:
        model_uuid = UUID(model_ref)
        model = session.exec(select(ChatModel).where(ChatModel.id == model_uuid)).first()
    except ValueError:
        model = None

    if not model:
        model = session.exec(
            select(ChatModel).where(ChatModel.model_name == model_ref)
        ).first()

    if not model:
        model = session.exec(
            select(ChatModel).where(ChatModel.display_name == model_ref)
        ).first()

    if not model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Model not found"
        )
    return model


@router.get("/models", response_model=ModelListResponse)
def list_models(
    session: Session = Depends(get_db),
    auth: AuthContext = Depends(get_auth_context),
) -> ModelListResponse:
    enabled_model_ids = list(allowed_model_ids(session, auth.org_id, auth.user.id))
    if not enabled_model_ids:
        return ModelListResponse(data=[])

    disabled_providers = session.exec(
        select(OrgProviderConfig.provider).where(
            OrgProviderConfig.org_id == auth.org_id,
            OrgProviderConfig.is_enabled == False,
        )
    ).all()
    models_query = select(ChatModel).where(
        ChatModel.is_active == True, ChatModel.id.in_(enabled_model_ids)
    )
    if disabled_providers:
        models_query = models_query.where(ChatModel.provider.notin_(disabled_providers))
    models = session.exec(models_query).all()
    return ModelListResponse(data=[_model_list_item(model) for model in models])


@router.get("/models/{model_id}", response_model=ModelListItem)
def get_model(
    model_id: str,
    session: Session = Depends(get_db),
    auth: AuthContext = Depends(get_auth_context),
) -> ModelListItem:
    model = resolve_model(session, model_id)
    if model.id not in allowed_model_ids(session, auth.org_id, auth.user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Model is not enabled for this organization",
        )
    provider_config = session.exec(
        select(OrgProviderConfig).where(
            OrgProviderConfig.org_id == auth.org_id,
            OrgProviderConfig.provider == model.provider,
        )
    ).first()
    if provider_config is not None and provider_config.is_enabled is False:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Model not found",
        )
    return _model_list_item(model)


@router.post("/chat/completions", response_model=ChatCompletionResponse)
async def chat_completions(
    payload: ChatCompletionRequest,
    session: Session = Depends(get_db),
    auth: AuthContext = Depends(get_auth_context),
) -> ChatCompletionResponse | StreamingResponse:
    if not payload.messages:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one message is required",
        )

    normalized_messages = [
        ChatMessagePayload(
            role=_normalized_role(message.role),
            content=message.content,
            tool_calls=message.tool_calls,
            tool_call_id=message.tool_call_id,
        )
        for message in payload.messages
    ]
    allowed_roles = {"system", "user", "assistant", "tool"}
    invalid_roles = sorted({message.role for message in normalized_messages if message.role not in allowed_roles})
    if invalid_roles:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid message role(s): {', '.join(invalid_roles)}",
        )

    non_empty_messages = sum(
        1 for message in normalized_messages if _message_has_content(message.content)
    )
    if non_empty_messages == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one non-empty message content is required",
        )

    org_id = auth.org_id
    model = resolve_model(session, payload.model)
    if model.id not in allowed_model_ids(session, org_id, auth.user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Model is not enabled for this organization",
        )

    provider_config = require_provider_enabled(session, org_id, model.provider)
    prompt_cache_key = f"api:{org_id}:{auth.user.id}"
    provider = get_provider_for_org(
        session,
        org_id,
        model.provider,
        org_config=provider_config,
        reasoning_effort=model.reasoning_effort,
        prompt_cache_key=prompt_cache_key,
        prompt_cache_retention=settings.openai_prompt_cache_retention,
        prefer_responses_api=prefer_responses_api_for_model(model),
        openrouter_endpoint=model.openrouter_endpoint,
    )
    message_payload, dropped_tool_messages, coerced_orphan_tool_messages = (
        _normalize_provider_messages(normalized_messages)
    )
    completion_params = params_from_chat_request(payload)
    tool_specs = compat_support.chat_tools_from_specs(payload.tools or [])

    if payload.stream:

        async def event_stream():
            completion_id = f"chatcmpl-{uuid4().hex[:24]}"
            created = int(datetime.now(timezone.utc).timestamp())
            merged_stream_options = compat_support.merge_stream_options(
                completion_params, payload.stream_options
            )
            include_usage = compat_support.stream_include_usage(merged_stream_options)
            stream_params = dict(completion_params)
            stream_params["stream_options"] = compat_support.provider_stream_options(
                merged_stream_options
            )
            sent_role = False
            pending_tool_calls: dict[int, dict[str, str]] = {}
            final_usage = None
            finish_reason = "stop"
            tool_index_by_id: dict[str, int] = {}
            next_tool_index = 0
            streamed_tool_deltas = False

            if tool_specs and hasattr(provider, "chat_stream_with_tools"):
                stream_iter = provider.chat_stream_with_tools(
                    model.model_name,
                    message_payload,
                    tool_specs,
                    tool_choice=payload.tool_choice,
                    completion_params=stream_params,
                )
            elif hasattr(provider, "chat_stream"):
                stream_iter = provider.chat_stream(
                    model.model_name,
                    message_payload,
                    completion_params=stream_params,
                )
            else:
                if tool_specs:
                    response = await provider.chat_with_tools(
                        model.model_name,
                        message_payload,
                        tool_specs,
                        tool_choice=payload.tool_choice,
                        completion_params=completion_params,
                    )
                else:
                    response = await provider.chat(
                        model.model_name,
                        message_payload,
                        completion_params=completion_params,
                    )
                completion = compat_support.completion_from_chat_response(
                    response, model_name=model.model_name
                )
                compat_support.record_usage_event(
                    session,
                    org_id=org_id,
                    user_id=auth.user.id,
                    model=model,
                    usage=response.usage,
                )
                persist_responses_api_discovery(session, model, provider)
                choice = completion.choices[0]
                delta: dict[str, Any] = {"role": "assistant"}
                if choice.message.content:
                    delta["content"] = choice.message.content
                if choice.message.tool_calls:
                    delta["tool_calls"] = [
                        {
                            "index": i,
                            "id": call.id,
                            "type": call.type,
                            "function": {
                                "name": call.function.name,
                                "arguments": call.function.arguments,
                            },
                        }
                        for i, call in enumerate(choice.message.tool_calls)
                    ]
                yield compat_support.stream_chunk_sse(
                    completion_id=completion_id,
                    created=created,
                    model=model.model_name,
                    index=0,
                    delta=delta,
                )
                yield compat_support.stream_chunk_sse(
                    completion_id=completion_id,
                    created=created,
                    model=model.model_name,
                    index=0,
                    delta={},
                    finish_reason=choice.finish_reason,
                )
                if include_usage and completion.usage is not None:
                    yield compat_support.stream_chunk_sse(
                        completion_id=completion_id,
                        created=created,
                        model=model.model_name,
                        index=0,
                        delta={},
                        usage=completion.usage,
                        usage_only=True,
                    )
                yield "data: [DONE]\n\n"
                return

            async for chunk in stream_iter:
                if chunk.usage is not None:
                    final_usage = chunk.usage
                if chunk.finish_reason:
                    finish_reason = chunk.finish_reason
                delta: dict[str, Any] = {}
                if not sent_role:
                    delta["role"] = "assistant"
                    sent_role = True
                if chunk.content:
                    delta["content"] = chunk.content
                if chunk.reasoning_content:
                    delta["reasoning_content"] = chunk.reasoning_content
                if delta:
                    yield compat_support.stream_chunk_sse(
                        completion_id=completion_id,
                        created=created,
                        model=model.model_name,
                        index=0,
                        delta=delta,
                    )
                if chunk.tool_call_deltas:
                    streamed_tool_deltas = True
                    yield compat_support.stream_chunk_sse(
                        completion_id=completion_id,
                        created=created,
                        model=model.model_name,
                        index=0,
                        delta={
                            "tool_calls": compat_support.tool_call_deltas_to_sse(
                                chunk.tool_call_deltas
                            )
                        },
                    )
                elif chunk.tool_calls and not streamed_tool_deltas:
                    tool_deltas: list[dict[str, Any]] = []
                    for call in chunk.tool_calls:
                        index = tool_index_by_id.get(call.id)
                        if index is None:
                            index = next_tool_index
                            next_tool_index += 1
                            tool_index_by_id[call.id] = index
                        tool_deltas.append(
                            {
                                "index": index,
                                "id": call.id,
                                "type": "function",
                                "function": {
                                    "name": call.name,
                                    "arguments": json.dumps(
                                        call.arguments, ensure_ascii=False
                                    ),
                                },
                            }
                        )
                    yield compat_support.stream_chunk_sse(
                        completion_id=completion_id,
                        created=created,
                        model=model.model_name,
                        index=0,
                        delta={"tool_calls": tool_deltas},
                    )

            usage_api = compat_support.final_chunk_usage_from_stream(final_usage)
            if final_usage is not None:
                compat_support.record_usage_event(
                    session,
                    org_id=org_id,
                    user_id=auth.user.id,
                    model=model,
                    usage=final_usage,
                )
                persist_responses_api_discovery(session, model, provider)
            yield compat_support.stream_chunk_sse(
                completion_id=completion_id,
                created=created,
                model=model.model_name,
                index=0,
                delta={},
                finish_reason=finish_reason,
            )
            if include_usage and usage_api is not None:
                yield compat_support.stream_chunk_sse(
                    completion_id=completion_id,
                    created=created,
                    model=model.model_name,
                    index=0,
                    delta={},
                    usage=usage_api,
                    usage_only=True,
                )
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    if tool_specs:
        response = await provider.chat_with_tools(
            model.model_name,
            message_payload,
            tool_specs,
            tool_choice=payload.tool_choice,
            completion_params=completion_params,
        )
    else:
        response = await provider.chat(
            model.model_name,
            message_payload,
            completion_params=completion_params,
        )
    compat_support.record_usage_event(
        session,
        org_id=org_id,
        user_id=auth.user.id,
        model=model,
        usage=response.usage,
    )
    persist_responses_api_discovery(session, model, provider)
    return compat_support.completion_from_chat_response(
        response, model_name=model.model_name
    )


@router.post("/responses", response_model=None)
async def create_response(
    payload: ResponseCreateRequest,
    session: Session = Depends(get_db),
    auth: AuthContext = Depends(get_auth_context),
) -> Any:
    model = resolve_model(session, payload.model)
    if model.id not in allowed_model_ids(session, auth.org_id, auth.user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Model is not enabled for this organization",
        )
    provider_config = require_provider_enabled(session, auth.org_id, model.provider)

    # For OpenAI models, proxy to the Responses API directly so the wire
    # shape is 1:1 with what OpenAI returns — no SDK version dependency.
    if model.provider == "openai":
        import httpx as _httpx

        api_key = (
            (provider_config.api_key_override if provider_config else None)
            or settings.openai_api_key
        )
        base_url = (
            (provider_config.base_url_override if provider_config else None)
            or "https://api.openai.com/v1"
        )
        base_url = base_url.rstrip("/")
        request_payload = payload.model_dump(mode="json", exclude_none=True)
        request_payload["model"] = model.model_name

        if payload.stream:
            # Pass through OpenAI's SSE stream as-is.
            async def event_stream():
                async with _httpx.AsyncClient(timeout=120.0) as http_client:
                    async with http_client.stream(
                        "POST",
                        f"{base_url}/responses",
                        json=request_payload,
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                        },
                    ) as upstream_stream:
                        async for chunk in upstream_stream.aiter_bytes():
                            if chunk:
                                yield chunk

            return StreamingResponse(
                event_stream(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache"},
            )

        async with _httpx.AsyncClient(timeout=120.0) as http_client:
            upstream = await http_client.post(
                f"{base_url}/responses",
                json=request_payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
        if upstream.status_code != 200:
            logger.error(
                "openai_compat.responses upstream_error org_id=%s user_id=%s status=%s body=%s",
                auth.org_id,
                auth.user.id,
                upstream.status_code,
                upstream.text[:500],
            )
            raise HTTPException(
                status_code=upstream.status_code,
                detail=upstream.text[:2000],
            )
        response_dict: dict[str, Any] = upstream.json()

        created_at = int(response_dict.get("created_at") or datetime.now(timezone.utc).timestamp())
        usage_payload = response_dict.get("usage") or {}
        input_tokens = int(usage_payload.get("input_tokens") or 0)
        output_tokens = int(usage_payload.get("output_tokens") or 0)
        total_tokens = int(
            usage_payload.get("total_tokens") or (input_tokens + output_tokens)
        )
        usage_event = UsageEvent(
            org_id=auth.org_id,
            user_id=auth.user.id,
            chat_id=None,
            message_id=None,
            model_id=model.id,
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
            total_tokens=total_tokens,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=0,
            thinking_tokens=0,
        )
        session.add(usage_event)
        session.commit()

        output_items = response_dict.get("output")
        if not isinstance(output_items, list):
            output_items = []
        response_payload: dict[str, Any] = dict(response_dict)
        response_payload.setdefault("id", f"resp_{uuid4().hex[:24]}")
        response_payload.setdefault("object", "response")
        response_payload.setdefault("created_at", created_at)
        response_payload.setdefault("created", created_at)
        response_payload.setdefault("status", "completed")
        response_payload.setdefault("model", model.model_name)
        if not isinstance(response_payload.get("output"), list):
            response_payload["output"] = []
        if response_payload.get("output_text") is None:
            response_payload["output_text"] = ""
        if "usage" not in response_payload or not isinstance(response_payload["usage"], dict):
            response_payload["usage"] = {}
        response_payload["usage"].setdefault("input_tokens", input_tokens)
        response_payload["usage"].setdefault("output_tokens", output_tokens)
        response_payload["usage"].setdefault("total_tokens", total_tokens)

        return response_payload

    messages, input_type = _coerce_responses_input(payload.input)
    non_empty_messages = sum(
        1 for message in messages if _message_has_content(message.content)
    )
    if non_empty_messages == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request input contains no non-empty message content",
        )
    completion_payload = ChatCompletionRequest(
        model=payload.model,
        messages=messages,
        temperature=payload.temperature,
        top_p=getattr(payload, "top_p", None),
        max_tokens=payload.max_output_tokens,
        max_completion_tokens=payload.max_output_tokens,
        tools=payload.tools,
        tool_choice=payload.tool_choice,
        stop=getattr(payload, "stop", None),
        metadata=getattr(payload, "metadata", None),
        parallel_tool_calls=getattr(payload, "parallel_tool_calls", None),
        store=getattr(payload, "store", None),
        user=getattr(payload, "user", None),
        stream=payload.stream,
    )
    adapter_stream = payload.stream and model.provider != "openai"
    if adapter_stream:
        completion_payload = completion_payload.model_copy(update={"stream": False})
    completion = await chat_completions(
        completion_payload, session=session, auth=auth
    )
    if isinstance(completion, StreamingResponse):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unexpected streaming chat completion for responses adapter",
        )
    response_payload = _response_payload_from_completion(completion)
    if payload.stream:

        def event_stream():
            for event in _responses_sse_events(response_payload):
                yield event

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )
    return response_payload


@router.post("/completions", response_model=LegacyCompletionResponse)
async def legacy_completions(
    payload: LegacyCompletionRequest,
    session: Session = Depends(get_db),
    auth: AuthContext = Depends(get_auth_context),
) -> LegacyCompletionResponse | StreamingResponse:
    prompt_text = _prompt_to_text(payload.prompt)
    if not prompt_text.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="prompt is required",
        )
    messages = [ChatMessagePayload(role="user", content=prompt_text)]
    chat_payload = ChatCompletionRequest(
        model=payload.model,
        messages=messages,
        temperature=payload.temperature,
        top_p=payload.top_p,
        n=payload.n,
        stream=payload.stream,
        max_tokens=payload.max_tokens,
        max_completion_tokens=payload.max_completion_tokens,
        stop=payload.stop,
        presence_penalty=payload.presence_penalty,
        frequency_penalty=payload.frequency_penalty,
        logit_bias=payload.logit_bias,
        user=payload.user,
        seed=payload.seed,
    )
    completion = await chat_completions(chat_payload, session=session, auth=auth)
    if isinstance(completion, StreamingResponse):
        return StreamingResponse(
            compat_support.adapt_chat_completion_stream_to_text(completion),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )
    created = completion.created
    completion_id = completion.id.replace("chatcmpl-", "cmpl-")
    choices = [
        LegacyCompletionChoice(
            text=choice.message.content or "",
            index=choice.index,
            logprobs=choice.logprobs,
            finish_reason=choice.finish_reason,
        )
        for choice in completion.choices
    ]
    return LegacyCompletionResponse(
        id=completion_id,
        created=created,
        model=completion.model,
        choices=choices,
        usage=completion.usage,
    )


@router.post("/embeddings", response_model=EmbeddingResponse)
async def create_embeddings(
    payload: EmbeddingRequest,
    session: Session = Depends(get_db),
    auth: AuthContext = Depends(get_auth_context),
) -> EmbeddingResponse:
    model = resolve_model(session, payload.model)
    if model.provider not in {"openai", "azure", "openrouter"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Embeddings not supported for this provider",
        )
    provider_config = require_provider_enabled(session, auth.org_id, model.provider)
    api_key = provider_config.api_key_override if provider_config else None
    base_url = provider_config.base_url_override if provider_config else None
    endpoint = provider_config.endpoint_override if provider_config else None
    from openai import AsyncAzureOpenAI, AsyncOpenAI, OpenAIError

    if model.provider == "azure":
        client = AsyncAzureOpenAI(
            api_key=api_key or settings.azure_openai_api_key,
            azure_endpoint=endpoint or settings.azure_openai_endpoint,
            api_version=settings.azure_openai_api_version,
        )
    elif model.provider == "openrouter":
        client = AsyncOpenAI(
            api_key=api_key or settings.openrouter_api_key,
            base_url=base_url or "https://openrouter.ai/api/v1",
        )
    else:
        client = AsyncOpenAI(
            api_key=api_key or settings.openai_api_key,
            base_url=base_url,
        )
    inputs = payload.input if isinstance(payload.input, list) else [payload.input]
    embed_kwargs: dict[str, Any] = {
        "model": model.model_name,
        "input": inputs,
    }
    if payload.encoding_format is not None:
        embed_kwargs["encoding_format"] = payload.encoding_format
    if payload.dimensions is not None:
        embed_kwargs["dimensions"] = payload.dimensions
    if payload.user is not None:
        embed_kwargs["user"] = payload.user
    try:
        response = await client.embeddings.create(**embed_kwargs)
    except OpenAIError as exc:
        logger.warning(
            "openai_compat.embeddings upstream_error org_id=%s model=%s: %s",
            auth.org_id,
            model.model_name,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.exception(
            "openai_compat.embeddings_error org_id=%s model=%s",
            auth.org_id,
            model.model_name,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Embedding request failed",
        ) from exc
    data = [
        EmbeddingData(embedding=item.embedding, index=item.index)
        for item in response.data
    ]
    usage = getattr(response, "usage", None)
    prompt_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
    total_tokens = getattr(usage, "total_tokens", prompt_tokens) if usage else 0
    usage_event = UsageEvent(
        org_id=auth.org_id,
        user_id=auth.user.id,
        chat_id=None,
        message_id=None,
        model_id=model.id,
        prompt_tokens=prompt_tokens,
        completion_tokens=0,
        total_tokens=total_tokens,
        input_tokens=prompt_tokens,
        output_tokens=0,
        cached_tokens=0,
        thinking_tokens=0,
    )
    session.add(usage_event)
    session.commit()
    return EmbeddingResponse(
        data=data,
        model=model.model_name,
        usage=EmbeddingUsage(prompt_tokens=prompt_tokens, total_tokens=total_tokens),
    )
