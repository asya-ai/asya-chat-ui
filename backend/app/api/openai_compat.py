from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Header, status
from pydantic import BaseModel
from sqlmodel import Session, select

from app.api.deps import get_current_user, get_db
from app.core.config import settings
from app.models import (
    Chat,
    ChatMessage,
    ChatModel,
    OrgMembership,
    OrgModel,
    OrgProviderConfig,
    UsageEvent,
    User,
)
from app.services.org_service import require_provider_enabled
from app.services.providers.registry import get_provider

router = APIRouter(prefix="/v1", tags=["openai-compat"])


class ChatMessagePayload(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[ChatMessagePayload]
    temperature: float | None = None
    max_tokens: int | None = None
    stream: bool | None = False
    user: str | None = None


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


class ModelListItem(BaseModel):
    id: str
    object: str = "model"
    owned_by: str = "organization"


class ModelListResponse(BaseModel):
    object: str = "list"
    data: list[ModelListItem]


def resolve_org_id(
    session: Session, user: User, org_id_header: str | None
) -> UUID:
    if org_id_header:
        try:
            return UUID(org_id_header)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid X-Org-Id"
            ) from exc

    if user.is_super_admin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Org-Id required for super admin",
        )

    membership = session.exec(
        select(OrgMembership).where(OrgMembership.user_id == user.id)
    ).first()
    if membership:
        return membership.org_id
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Organization not found for user",
    )


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
    current_user: User = Depends(get_current_user),
) -> ModelListResponse:
    membership = session.exec(
        select(OrgMembership).where(OrgMembership.user_id == current_user.id)
    ).first()
    if not membership:
        return ModelListResponse(data=[])

    enabled_model_ids = session.exec(
        select(OrgModel.model_id).where(
            OrgModel.org_id == membership.org_id, OrgModel.is_enabled == True
        )
    ).all()
    if not enabled_model_ids:
        return ModelListResponse(data=[])

    disabled_providers = session.exec(
        select(OrgProviderConfig.provider).where(
            OrgProviderConfig.org_id == membership.org_id,
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
    current_user: User = Depends(get_current_user),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
) -> ChatCompletionResponse:
    if payload.stream:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Streaming is not supported",
        )

    org_id = resolve_org_id(session, current_user, x_org_id)
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
        user_id=current_user.id,
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
    response = await provider.chat(
        model.model_name,
        [message.model_dump() for message in payload.messages],
    )

    assistant_message = ChatMessage(
        chat_id=chat.id, role="assistant", content=response.content
    )
    session.add(assistant_message)
    session.commit()

    usage_event = UsageEvent(
        org_id=org_id,
        user_id=current_user.id,
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
