from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session, select
from sqlalchemy import func

from app.api.deps import get_current_user, get_db
from app.models import ChatModel, Org, OrgModel, OrgMembership, OrgProviderConfig, User
from app.services.model_suggestions import get_model_suggestions
from app.services.org_service import require_super_admin

router = APIRouter(prefix="/models", tags=["models"])


class ModelCreateRequest(BaseModel):
    org_id: str
    provider: str
    model_name: str
    display_name: str
    is_active: bool = True
    context_length: int | None = None
    supports_image_input: bool | None = None
    supports_image_output: bool | None = None
    reasoning_effort: str | None = None


class ModelRead(BaseModel):
    id: str
    provider: str
    model_name: str
    display_name: str
    is_active: bool
    display_order: int
    context_length: int | None = None
    supports_image_input: bool | None = None
    supports_image_output: bool | None = None
    reasoning_effort: str | None = None
    is_available: bool = True


class ModelUpdateRequest(BaseModel):
    display_name: str | None = None
    reasoning_effort: str | None = None


class ModelOrderUpdateRequest(BaseModel):
    model_id: str
    display_order: int


class ModelSuggestionItem(BaseModel):
    model_name: str
    display_name: str
    context_length: int | None = None
    supports_image_input: bool | None = None
    supports_image_output: bool | None = None
    reasoning_effort: str | None = None


class ModelSuggestionProvider(BaseModel):
    provider: str
    models: list[ModelSuggestionItem]
    error: str | None = None


def _normalize_reasoning_effort(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"none", "low", "medium", "high"}:
        return normalized
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Invalid reasoning effort (use none/low/medium/high)",
    )


@router.post("", response_model=ModelRead)
def create_model(
    payload: ModelCreateRequest,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ModelRead:
    require_super_admin(current_user)

    max_order = session.exec(select(func.max(ChatModel.display_order))).first() or 0
    model = ChatModel(
        provider=payload.provider,
        model_name=payload.model_name,
        display_name=payload.display_name,
        is_active=payload.is_active,
        display_order=max_order + 1,
        context_length=payload.context_length,
        supports_image_input=payload.supports_image_input,
        supports_image_output=payload.supports_image_output,
        reasoning_effort=_normalize_reasoning_effort(payload.reasoning_effort),
    )
    session.add(model)
    session.commit()
    session.refresh(model)
    return ModelRead(
        id=str(model.id),
        provider=model.provider,
        model_name=model.model_name,
        display_name=model.display_name,
        is_active=model.is_active,
        display_order=model.display_order,
        context_length=model.context_length,
        supports_image_input=model.supports_image_input,
        supports_image_output=model.supports_image_output,
        reasoning_effort=model.reasoning_effort,
    )


@router.get("", response_model=list[ModelRead])
def list_models(
    org_id: str | None = None,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ModelRead]:
    if current_user.is_super_admin and org_id:
        try:
            org_uuid = UUID(org_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid org id"
            ) from exc
    elif current_user.is_super_admin and not org_id:
        models = session.exec(
            select(ChatModel)
            .where(ChatModel.is_active.is_(True))
            .order_by(ChatModel.display_order, ChatModel.display_name, ChatModel.id)
        ).all()
        return [
            ModelRead(
                id=str(model.id),
                provider=model.provider,
                model_name=model.model_name,
                display_name=model.display_name,
                is_active=model.is_active,
                display_order=model.display_order,
                context_length=model.context_length,
                supports_image_input=model.supports_image_input,
                supports_image_output=model.supports_image_output,
                reasoning_effort=model.reasoning_effort,
            )
            for model in models
        ]
    else:
        membership = session.exec(
            select(OrgMembership).where(OrgMembership.user_id == current_user.id)
        ).first()
        if not membership:
            return []
        org_uuid = membership.org_id

    enabled_model_ids = session.exec(
        select(OrgModel.model_id).where(
            OrgModel.org_id == org_uuid, OrgModel.is_enabled.is_(True)
        )
    ).all()
    if not enabled_model_ids:
        return []

    disabled_providers = set(session.exec(
        select(OrgProviderConfig.provider).where(
            OrgProviderConfig.org_id == org_uuid,
            OrgProviderConfig.is_enabled.is_(False),
        )
    ).all())
    models_query = (
        select(ChatModel)
        .where(ChatModel.is_active.is_(True), ChatModel.id.in_(enabled_model_ids))
        .order_by(ChatModel.display_order, ChatModel.display_name, ChatModel.id)
    )
    models = session.exec(models_query).all()
    return [
        ModelRead(
            id=str(model.id),
            provider=model.provider,
            model_name=model.model_name,
            display_name=model.display_name,
            is_active=model.is_active,
            display_order=model.display_order,
            context_length=model.context_length,
            supports_image_input=model.supports_image_input,
            supports_image_output=model.supports_image_output,
            reasoning_effort=model.reasoning_effort,
            is_available=model.provider not in disabled_providers,
        )
        for model in models
    ]


@router.get("/suggestions", response_model=list[ModelSuggestionProvider])
def list_model_suggestions(
    current_user: User = Depends(get_current_user),
) -> list[ModelSuggestionProvider]:
    require_super_admin(current_user)
    return get_model_suggestions()


@router.delete("/{model_id:uuid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_model(
    model_id: UUID,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    require_super_admin(current_user)
    model = session.exec(select(ChatModel).where(ChatModel.id == model_id)).first()
    if not model:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model not found")
    model.is_active = False
    session.add(model)
    links = session.exec(select(OrgModel).where(OrgModel.model_id == model_id)).all()
    for link in links:
        link.is_enabled = False
        session.add(link)
    session.commit()


@router.patch("/order", response_model=list[ModelRead])
def update_model_order(
    payload: list[ModelOrderUpdateRequest],
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ModelRead]:
    require_super_admin(current_user)
    updates: dict[UUID, int] = {}
    for item in payload:
        try:
            model_uuid = UUID(item.model_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid model id"
            ) from exc
        if item.display_order < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid display order"
            )
        updates[model_uuid] = item.display_order
    if not updates:
        return []
    models = session.exec(select(ChatModel).where(ChatModel.id.in_(updates.keys()))).all()
    for model in models:
        model.display_order = updates.get(model.id, model.display_order)
        session.add(model)
    session.commit()
    ordered = session.exec(
        select(ChatModel)
        .where(ChatModel.is_active.is_(True))
        .order_by(ChatModel.display_order, ChatModel.display_name, ChatModel.id)
    ).all()
    return [
        ModelRead(
            id=str(model.id),
            provider=model.provider,
            model_name=model.model_name,
            display_name=model.display_name,
            is_active=model.is_active,
            display_order=model.display_order,
            context_length=model.context_length,
            supports_image_input=model.supports_image_input,
            supports_image_output=model.supports_image_output,
            reasoning_effort=model.reasoning_effort,
        )
        for model in ordered
    ]


@router.patch("/{model_id:uuid}", response_model=ModelRead)
def update_model(
    model_id: UUID,
    payload: ModelUpdateRequest,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ModelRead:
    require_super_admin(current_user)
    model = session.exec(select(ChatModel).where(ChatModel.id == model_id)).first()
    if not model:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model not found")
    if payload.display_name is not None:
        model.display_name = payload.display_name.strip() or model.display_name
    if payload.reasoning_effort is not None:
        model.reasoning_effort = _normalize_reasoning_effort(payload.reasoning_effort)
    session.add(model)
    session.commit()
    session.refresh(model)
    return ModelRead(
        id=str(model.id),
        provider=model.provider,
        model_name=model.model_name,
        display_name=model.display_name,
        is_active=model.is_active,
        display_order=model.display_order,
        context_length=model.context_length,
        supports_image_input=model.supports_image_input,
        supports_image_output=model.supports_image_output,
        reasoning_effort=model.reasoning_effort,
    )


class OrgModelUpdateRequest(BaseModel):
    model_id: str
    is_enabled: bool = True


@router.put("/orgs/{org_id}", response_model=list[ModelRead])
def set_org_models(
    org_id: str,
    payload: list[OrgModelUpdateRequest],
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ModelRead]:
    require_super_admin(current_user)
    try:
        org_uuid = UUID(org_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid org id"
        ) from exc

    org = session.exec(select(Org).where(Org.id == org_uuid)).first()
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Org not found")

    for item in payload:
        try:
            model_uuid = UUID(item.model_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid model id"
            ) from exc
        model = session.exec(select(ChatModel).where(ChatModel.id == model_uuid)).first()
        if not model or not model.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Model not found"
            )
        link = session.exec(
            select(OrgModel).where(
                OrgModel.org_id == org_uuid, OrgModel.model_id == model_uuid
            )
        ).first()
        if link:
            link.is_enabled = item.is_enabled
            session.add(link)
        else:
            session.add(
                OrgModel(
                    org_id=org_uuid, model_id=model_uuid, is_enabled=item.is_enabled
                )
            )
    session.commit()

    enabled_model_ids = session.exec(
        select(OrgModel.model_id).where(
            OrgModel.org_id == org_uuid, OrgModel.is_enabled.is_(True)
        )
    ).all()
    if not enabled_model_ids:
        return []
    models = session.exec(
        select(ChatModel)
        .where(ChatModel.is_active.is_(True), ChatModel.id.in_(enabled_model_ids))
        .order_by(ChatModel.display_order, ChatModel.created_at)
    ).all()
    return [
        ModelRead(
            id=str(model.id),
            provider=model.provider,
            model_name=model.model_name,
            display_name=model.display_name,
            is_active=model.is_active,
            display_order=model.display_order,
            context_length=model.context_length,
            supports_image_input=model.supports_image_input,
            supports_image_output=model.supports_image_output,
            reasoning_effort=model.reasoning_effort,
        )
        for model in models
    ]
