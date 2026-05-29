from uuid import UUID
import json
import time
import re

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session, select
from sqlalchemy import func

from app.api.deps import get_current_user, get_db
from app.models import ChatModel, Org, OrgModel, OrgMembership, OrgProviderConfig, User
from app.services.model_capabilities import (
    ensure_models_capabilities,
    resolve_capabilities_for_storage,
)
from app.services.model_suggestions import get_model_suggestions
from app.services.org_service import require_provider_enabled, require_super_admin
from app.services.providers.registry import get_provider

router = APIRouter(prefix="/models", tags=["models"])
_VERTEX_INVOCATION_CACHE_TTL_SECONDS = 600
_VERTEX_INVOCATION_CACHE: dict[tuple[str, str], tuple[bool, float]] = {}


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


def _normalize_provider_model_name(provider: str, model_name: str) -> str:
    value = model_name.strip()
    if provider in {"gemini", "vertex"}:
        if value.startswith("publishers/google/models/"):
            value = value.split("publishers/google/models/", 1)[1]
        if value.startswith("models/"):
            value = value.split("models/", 1)[1]
    return value


def _validate_vertex_model_invokable(session: Session, org_id: UUID, model_name: str) -> None:
    provider_config = require_provider_enabled(session, org_id, "vertex")
    config: dict = {}
    if provider_config and provider_config.config_json:
        try:
            parsed = json.loads(provider_config.config_json)
            if isinstance(parsed, dict):
                config = parsed
        except Exception:
            config = {}
    provider = get_provider(
        "vertex",
        api_key=provider_config.api_key_override if provider_config else None,
        base_url=provider_config.base_url_override if provider_config else None,
        endpoint=provider_config.endpoint_override if provider_config else None,
        config=config,
    )
    try:
        provider.client.models.generate_content(
            model=model_name,
            contents="ping",
            config={"max_output_tokens": 1},
        )
    except Exception as exc:
        lowered = str(exc).lower()
        # Vertex sometimes lists alias names that 404 for generateContent while
        # the explicit versioned ID works for the same project/region.
        if (
            ("404" in lowered or "not_found" in lowered)
            and not model_name.endswith("-001")
            and re.match(r"^gemini-2\.\d+-(flash|flash-lite|pro)$", model_name)
        ):
            suggested_model = f"{model_name}-001"
            try:
                provider.client.models.generate_content(
                    model=suggested_model,
                    contents="ping",
                    config={"max_output_tokens": 1},
                )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        "Vertex rejected this alias in your project/region. "
                        f"Use `{suggested_model}` instead."
                    ),
                ) from exc
            except HTTPException:
                raise
            except Exception:
                pass
        detail = str(exc)
        if len(detail) > 400:
            detail = f"{detail[:400]}..."
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Vertex model is not invokable for this org/project/region: {detail}",
        ) from exc


def _is_vertex_model_invokable(session: Session, org_id: UUID, model_name: str) -> bool:
    cache_key = (str(org_id), model_name)
    cached = _VERTEX_INVOCATION_CACHE.get(cache_key)
    now = time.time()
    if cached and cached[1] > now:
        return cached[0]
    try:
        _validate_vertex_model_invokable(session, org_id, model_name)
        _VERTEX_INVOCATION_CACHE[cache_key] = (
            True,
            now + _VERTEX_INVOCATION_CACHE_TTL_SECONDS,
        )
        return True
    except HTTPException:
        _VERTEX_INVOCATION_CACHE[cache_key] = (
            False,
            now + _VERTEX_INVOCATION_CACHE_TTL_SECONDS,
        )
        return False


@router.post("", response_model=ModelRead)
def create_model(
    payload: ModelCreateRequest,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ModelRead:
    require_super_admin(current_user)

    max_order = session.exec(select(func.max(ChatModel.display_order))).first() or 0
    normalized_model_name = _normalize_provider_model_name(
        payload.provider, payload.model_name
    )
    if payload.provider == "vertex":
        try:
            org_uuid = UUID(payload.org_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid org id"
            ) from exc
        org = session.exec(select(Org).where(Org.id == org_uuid)).first()
        if not org:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Org not found"
            )
        _validate_vertex_model_invokable(session, org_uuid, normalized_model_name)
    resolved_input, resolved_output, resolved_context = resolve_capabilities_for_storage(
        payload.provider, normalized_model_name
    )
    supports_image_input_value = (
        payload.supports_image_input
        if payload.supports_image_input is not None
        else resolved_input
    )
    supports_image_output_value = (
        payload.supports_image_output
        if payload.supports_image_output is not None
        else resolved_output
    )
    context_length = (
        payload.context_length
        if payload.context_length is not None
        else resolved_context
    )
    model = ChatModel(
        provider=payload.provider,
        model_name=normalized_model_name,
        display_name=payload.display_name,
        is_active=payload.is_active,
        display_order=max_order + 1,
        context_length=context_length,
        supports_image_input=supports_image_input_value,
        supports_image_output=supports_image_output_value,
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
        ensure_models_capabilities(session, models)
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
    ensure_models_capabilities(session, models)
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
    org_id: str | None = None,
    invokable_only: bool = False,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ModelSuggestionProvider]:
    require_super_admin(current_user)
    suggestions = get_model_suggestions()
    if not invokable_only or not org_id:
        return suggestions
    try:
        org_uuid = UUID(org_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid org id"
        ) from exc
    org = session.exec(select(Org).where(Org.id == org_uuid)).first()
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Org not found")
    for provider in suggestions:
        if provider.get("provider") != "vertex":
            continue
        models = provider.get("models", [])
        if not isinstance(models, list):
            continue
        provider["models"] = [
            model
            for model in models
            if isinstance(model, dict)
            and isinstance(model.get("model_name"), str)
            and _is_vertex_model_invokable(session, org_uuid, model["model_name"])
        ]
    return suggestions


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
