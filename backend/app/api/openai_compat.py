from datetime import datetime, timezone
import logging
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from json_repair import loads as repair_json_loads
from pydantic import BaseModel, model_validator
from sqlmodel import Session, select
import json

from app.api.deps import AuthContext, get_auth_context, get_db
from app.core.config import settings
from app.models import ChatModel, OrgProviderConfig, UsageEvent
from app.services.org_service import require_provider_enabled
from app.services.team_service import allowed_model_ids
from app.services.model_capabilities import persist_responses_api_discovery
from app.services.providers.base import ChatToolSpec
from app.services.providers.registry import get_provider

router = APIRouter(prefix="/v1", tags=["openai-compat"])
logger = logging.getLogger(__name__)


class ChatMessagePayload(BaseModel):
    role: str
    content: str | list["ChatInputTextPart"] | None = None
    tool_calls: list["ChatCompletionToolCall"] | None = None
    tool_call_id: str | None = None


class ChatInputTextPart(BaseModel):
    type: str
    text: str | None = None


class ChatCompletionToolCallFunction(BaseModel):
    name: str
    arguments: str


class ChatCompletionToolCall(BaseModel):
    id: str
    type: str = "function"
    function: ChatCompletionToolCallFunction


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
    model: str
    messages: list[ChatMessagePayload]
    temperature: float | None = None
    max_tokens: int | None = None
    max_completion_tokens: int | None = None
    stream: bool | None = False
    user: str | None = None
    tools: list[ToolSpec] | None = None
    tool_choice: object | None = None


class ChatCompletionChoice(BaseModel):
    index: int
    message: ChatMessagePayload
    # Kept for backward compatibility with existing callers.
    tool_calls: list[ChatCompletionToolCall] | None = None
    finish_reason: str


class ChatCompletionUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


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


def _chat_content_to_text(value: str | list[ChatInputTextPart] | None) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "".join(
            (part.text or "")
            for part in value
            if part.type in {"text", "input_text", "output_text"}
        ).strip()
    return ""


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
        provider_calls.append(
            {
                "id": call.id,
                "name": call.function.name,
                "arguments": parsed_arguments,
            }
        )
    return provider_calls


def _normalize_provider_messages(
    normalized_messages: list[ChatMessagePayload],
) -> tuple[list[dict[str, Any]], int, int]:
    provider_messages: list[dict[str, Any]] = []
    pending_tool_call_ids: set[str] = set()
    dropped_tool_messages = 0
    coerced_orphan_tool_messages = 0
    for message in normalized_messages:
        role = message.role
        if role == "assistant":
            payload_message: dict[str, Any] = {"role": "assistant"}
            content_text = _chat_content_to_text(message.content)
            if content_text:
                payload_message["content"] = content_text
            tool_calls = _provider_tool_calls(message.tool_calls)
            if tool_calls:
                payload_message["tool_calls"] = tool_calls
                pending_tool_call_ids.update(
                    str(call.get("id") or "") for call in tool_calls if call.get("id")
                )
            if content_text or tool_calls:
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
            provider_messages.append(
                {
                    "role": "tool",
                    "content": content_text,
                    "tool_call_id": tool_call_id,
                }
            )
            pending_tool_call_ids.discard(tool_call_id)
            continue
        provider_messages.append(
            {
                "role": role,
                "content": _chat_content_to_text(message.content),
            }
        )
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
            text = content_value.strip()
        elif isinstance(content_value, list):
            text_parts: list[str] = []
            for part in content_value:
                if isinstance(part, str):
                    text_parts.append(part)
                    continue
                if not isinstance(part, dict):
                    continue
                part_type = str(part.get("type") or "").lower()
                if part_type in {"input_text", "text", "output_text"}:
                    text_parts.append(str(part.get("text") or ""))
            text = "".join(text_parts).strip()
        elif content_value is None:
            text = ""
        else:
            text = str(content_value).strip()
        messages.append(ChatMessagePayload(role=role, content=text))
    return messages, "array"


class EmbeddingRequest(BaseModel):
    model: str
    input: str | list[str]


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
    owned_by: str = "organization"


class ModelListResponse(BaseModel):
    object: str = "list"
    data: list[ModelListItem]


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
    return ModelListResponse(data=[ModelListItem(id=model.model_name) for model in models])


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
        1 for message in normalized_messages if _chat_content_to_text(message.content)
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
    provider = get_provider(
        model.provider,
        api_key=provider_config.api_key_override if provider_config else None,
        base_url=provider_config.base_url_override if provider_config else None,
        endpoint=provider_config.endpoint_override if provider_config else None,
        reasoning_effort=model.reasoning_effort,
        prompt_cache_key=prompt_cache_key,
        prompt_cache_retention=settings.openai_prompt_cache_retention,
        prefer_responses_api=model.uses_responses_api is True,
    )
    message_payload, dropped_tool_messages, coerced_orphan_tool_messages = (
        _normalize_provider_messages(
        normalized_messages
        )
    )
    if payload.tools:
        tools = [
            ChatToolSpec(
                name=tool.function.name,
                description=tool.function.description or "",
                parameters=tool.function.parameters or {},
            )
            for tool in payload.tools
            if tool.type == "function"
        ]
        response = await provider.chat_with_tools(
            model.model_name,
            message_payload,
            tools,
            tool_choice=payload.tool_choice,
        )
    else:
        response = await provider.chat(
            model.model_name,
            message_payload,
        )
    usage_event = UsageEvent(
        org_id=org_id,
        user_id=auth.user.id,
        chat_id=None,
        message_id=None,
        model_id=model.id,
        prompt_tokens=response.usage.prompt_tokens,
        completion_tokens=response.usage.completion_tokens,
        total_tokens=response.usage.total_tokens,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        cached_tokens=response.usage.cached_tokens,
        thinking_tokens=response.usage.thinking_tokens,
    )
    session.add(usage_event)
    session.commit()
    persist_responses_api_discovery(session, model, provider)
    tool_calls = [
        ChatCompletionToolCall(
            id=call.id,
            function=ChatCompletionToolCallFunction(
                name=call.name,
                arguments=json.dumps(call.arguments, ensure_ascii=False),
            ),
        )
        for call in (response.tool_calls or [])
    ]
    finish_reason = (
        response.finish_reason
        or ("tool_calls" if tool_calls else "stop")
    )

    completion_response = ChatCompletionResponse(
        id=f"chatcmpl-{uuid4().hex[:24]}",
        object="chat.completion",
        created=int(datetime.now(timezone.utc).timestamp()),
        model=model.model_name,
        choices=[
            ChatCompletionChoice(
                index=0,
                message=ChatMessagePayload(
                    role="assistant",
                    content=response.content,
                    tool_calls=tool_calls or None,
                ),
                tool_calls=tool_calls or None,
                finish_reason=finish_reason,
            )
        ],
        usage=ChatCompletionUsage(
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
            total_tokens=response.usage.total_tokens,
        ),
    )
    if payload.stream:
        choice = completion_response.choices[0]
        delta: dict[str, Any] = {"role": "assistant"}
        if choice.message.content:
            delta["content"] = choice.message.content
        if choice.message.tool_calls:
            delta["tool_calls"] = [
                {
                    "id": tool_call.id,
                    "type": tool_call.type,
                    "function": {
                        "name": tool_call.function.name,
                        "arguments": tool_call.function.arguments,
                    },
                }
                for tool_call in choice.message.tool_calls
            ]
        first_chunk = {
            "id": completion_response.id,
            "object": "chat.completion.chunk",
            "created": completion_response.created,
            "model": completion_response.model,
            "choices": [
                {
                    "index": 0,
                    "delta": delta,
                    "finish_reason": None,
                }
            ],
        }
        final_chunk = {
            "id": completion_response.id,
            "object": "chat.completion.chunk",
            "created": completion_response.created,
            "model": completion_response.model,
            "choices": [
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": choice.finish_reason,
                }
            ],
        }

        def event_stream():
            yield f"data: {json.dumps(first_chunk, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps(final_chunk, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")
    return completion_response


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
        1 for message in messages if _chat_content_to_text(message.content)
    )
    if non_empty_messages == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request input contains no non-empty text content",
        )
    completion_payload = ChatCompletionRequest(
        model=payload.model,
        messages=messages,
        temperature=payload.temperature,
        max_tokens=payload.max_output_tokens,
        tools=payload.tools,
        tool_choice=payload.tool_choice,
    )
    completion = await chat_completions(
        completion_payload, session=session, auth=auth
    )
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
                # Keep OpenAI-like output item IDs (`fc_...`) and preserve
                # model call linkage in `call_id`.
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


@router.post("/embeddings", response_model=EmbeddingResponse)
async def create_embeddings(
    payload: EmbeddingRequest,
    session: Session = Depends(get_db),
    auth: AuthContext = Depends(get_auth_context),
) -> EmbeddingResponse:
    model = resolve_model(session, payload.model)
    if model.provider not in {"openai", "azure"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Embeddings not supported for this provider",
        )
    provider_config = require_provider_enabled(session, auth.org_id, model.provider)
    api_key = provider_config.api_key_override if provider_config else None
    base_url = provider_config.base_url_override if provider_config else None
    endpoint = provider_config.endpoint_override if provider_config else None
    from openai import AsyncAzureOpenAI, AsyncOpenAI

    if model.provider == "azure":
        client = AsyncAzureOpenAI(
            api_key=api_key or settings.azure_openai_api_key,
            azure_endpoint=endpoint or settings.azure_openai_endpoint,
            api_version=settings.azure_openai_api_version,
        )
    else:
        client = AsyncOpenAI(
            api_key=api_key or settings.openai_api_key,
            base_url=base_url,
        )
    inputs = payload.input if isinstance(payload.input, list) else [payload.input]
    response = await client.embeddings.create(model=model.model_name, input=inputs)
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
