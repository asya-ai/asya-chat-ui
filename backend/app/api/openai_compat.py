from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session, select

from app.api.deps import AuthContext, get_auth_context, get_db
from app.core.config import settings
from app.models import Chat, ChatMessage, ChatModel, OrgModel, OrgProviderConfig, UsageEvent
from app.services.org_service import require_provider_enabled
from app.services.providers.base import ChatToolSpec
from app.services.providers.registry import get_provider

router = APIRouter(prefix="/v1", tags=["openai-compat"])


class ChatMessagePayload(BaseModel):
    role: str
    content: str


class ToolFunction(BaseModel):
    name: str
    description: str | None = None
    parameters: dict | None = None


class ToolSpec(BaseModel):
    type: str
    function: ToolFunction


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[ChatMessagePayload]
    temperature: float | None = None
    max_tokens: int | None = None
    stream: bool | None = False
    user: str | None = None
    tools: list[ToolSpec] | None = None
    tool_choice: object | None = None


class ChatCompletionChoice(BaseModel):
    index: int
    message: ChatMessagePayload
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
    text: str


class ResponseInputMessage(BaseModel):
    role: str = "user"
    content: list[ResponseInputText]


class ResponseCreateRequest(BaseModel):
    model: str
    input: str | list[ResponseInputMessage]
    temperature: float | None = None
    max_output_tokens: int | None = None
    tools: list[ToolSpec] | None = None
    tool_choice: object | None = None


class ResponseOutputText(BaseModel):
    type: str = "output_text"
    text: str


class ResponseOutputMessage(BaseModel):
    type: str = "message"
    role: str = "assistant"
    content: list[ResponseOutputText]


class ResponseUsage(BaseModel):
    input_tokens: int
    output_tokens: int
    total_tokens: int


class ResponseCreateResponse(BaseModel):
    id: str
    object: str = "response"
    created: int
    model: str
    output: list[ResponseOutputMessage]
    usage: ResponseUsage


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
    enabled_model_ids = session.exec(
        select(OrgModel.model_id).where(
            OrgModel.org_id == auth.org_id, OrgModel.is_enabled == True
        )
    ).all()
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
    return ModelListResponse(data=[ModelListItem(id=str(model.id)) for model in models])


@router.post("/chat/completions", response_model=ChatCompletionResponse)
async def chat_completions(
    payload: ChatCompletionRequest,
    session: Session = Depends(get_db),
    auth: AuthContext = Depends(get_auth_context),
) -> ChatCompletionResponse:
    if payload.stream:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Streaming is not supported",
        )

    org_id = auth.org_id
    model = resolve_model(session, payload.model)
    enabled = session.exec(
        select(OrgModel).where(
            OrgModel.org_id == org_id,
            OrgModel.model_id == model.id,
            OrgModel.is_enabled == True,
        )
    ).first()
    if not enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Model is not enabled for this organization",
        )

    chat = Chat(
        org_id=org_id,
        user_id=auth.user.id,
        model_id=model.id,
        title="OpenAI API session",
    )
    session.add(chat)
    session.commit()
    session.refresh(chat)

    for message in payload.messages:
        session.add(
            ChatMessage(
                chat_id=chat.id,
                role=message.role,
                content=message.content,
                status="done",
            )
        )
    session.commit()

    provider_config = require_provider_enabled(session, org_id, model.provider)
    prompt_cache_key = f"chat:{chat.id}"
    provider = get_provider(
        model.provider,
        api_key=provider_config.api_key_override if provider_config else None,
        base_url=provider_config.base_url_override if provider_config else None,
        endpoint=provider_config.endpoint_override if provider_config else None,
        reasoning_effort=model.reasoning_effort,
        prompt_cache_key=prompt_cache_key,
        prompt_cache_retention=settings.openai_prompt_cache_retention,
    )
    message_payload = [message.model_dump() for message in payload.messages]
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

    assistant_message = ChatMessage(
        chat_id=chat.id,
        role="assistant",
        content=response.content,
        status="done",
    )
    session.add(assistant_message)
    session.commit()

    usage_event = UsageEvent(
        org_id=org_id,
        user_id=auth.user.id,
        chat_id=chat.id,
        message_id=assistant_message.id,
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

    return ChatCompletionResponse(
        id=f"chatcmpl-{chat.id}",
        object="chat.completion",
        created=int(datetime.now(timezone.utc).timestamp()),
        model=str(model.id),
        choices=[
            ChatCompletionChoice(
                index=0,
                message=ChatMessagePayload(role="assistant", content=response.content),
                finish_reason="stop",
            )
        ],
        usage=ChatCompletionUsage(
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
            total_tokens=response.usage.total_tokens,
        ),
    )


@router.post("/responses", response_model=ResponseCreateResponse)
async def create_response(
    payload: ResponseCreateRequest,
    session: Session = Depends(get_db),
    auth: AuthContext = Depends(get_auth_context),
) -> ResponseCreateResponse:
    if isinstance(payload.input, str):
        messages = [ChatMessagePayload(role="user", content=payload.input)]
    else:
        messages = [
            ChatMessagePayload(
                role=item.role,
                content="".join(
                    part.text for part in item.content if part.type == "input_text"
                ).strip(),
            )
            for item in payload.input
        ]
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
    output_text = completion.choices[0].message.content
    return ResponseCreateResponse(
        id=completion.id.replace("chatcmpl", "resp"),
        created=completion.created,
        model=completion.model,
        output=[ResponseOutputMessage(content=[ResponseOutputText(text=output_text)])],
        usage=ResponseUsage(
            input_tokens=completion.usage.prompt_tokens,
            output_tokens=completion.usage.completion_tokens,
            total_tokens=completion.usage.total_tokens,
        ),
    )


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
        model=str(model.id),
        usage=EmbeddingUsage(prompt_tokens=prompt_tokens, total_tokens=total_tokens),
    )
