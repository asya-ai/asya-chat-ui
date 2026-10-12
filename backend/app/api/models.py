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
from app.services.model_suggestions import fetch_openrouter_endpoints, get_model_suggestions
from app.services.org_service import (
    require_org_admin,
    require_provider_enabled,
    require_super_admin,
)
from app.services.team_service import allowed_model_ids, seed_default_team_model
from app.services.instance_providers import get_provider_for_org
from app.services.model_pricing import (
    classify_model_cost_tier,
    resolve_model_token_price,
)

router = APIRouter(prefix="/models", tags=["models"])
_COST_TIER_VALUES = frozenset({1, 2, 3, 4})
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
    uses_responses_api: bool | None = None
    reasoning_effort: str | None = None
    openrouter_endpoint: str | None = None


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
    uses_responses_api: bool | None = None
    reasoning_effort: str | None = None
    openrouter_endpoint: str | None = None
    is_available: bool = True
    input_cost_per_million: float | None = None
    cached_input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None
    cost_per_image: float | None = None
    auto_cost_tier: int | None = None
    cost_tier_override: int | None = None
    cost_tier: int | None = None


class ModelUpdateRequest(BaseModel):
    display_name: str | None = None
    reasoning_effort: str | None = None
    uses_responses_api: bool | None = None
    openrouter_endpoint: str | None = None
    # 1–4 to override; null (when field is set) clears back to auto.
    cost_tier: int | None = None


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


class OpenRouterEndpointItem(BaseModel):
    tag: str
    name: str
    provider_name: str | None = None
    quantization: str | None = None


class OpenRouterEndpointsResponse(BaseModel):
    model_name: str
    endpoints: list[OpenRouterEndpointItem]
    error: str | None = None


def _normalize_cost_tier_override(value: int | None) -> int | None:
    if value is None:
        return None
    if value not in _COST_TIER_VALUES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="cost_tier must be 1–4 or null",
        )
    return value


def _model_read(model: ChatModel, *, is_available: bool = True) -> ModelRead:
    price = resolve_model_token_price(model.provider, model.model_name)
    auto_tier = classify_model_cost_tier(model.provider, model.model_name)
    override = model.cost_tier_override if model.cost_tier_override in _COST_TIER_VALUES else None
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
        uses_responses_api=model.uses_responses_api,
        reasoning_effort=model.reasoning_effort,
        openrouter_endpoint=model.openrouter_endpoint,
        is_available=is_available,
        input_cost_per_million=(
            price.input_per_million if price and price.cost_per_image is None else None
        ),
        cached_input_cost_per_million=(
            price.cached_input_per_million
            if price and price.cost_per_image is None
            else None
        ),
        output_cost_per_million=(
            price.output_per_million if price and price.cost_per_image is None else None
        ),
        cost_per_image=price.cost_per_image if price else None,
        auto_cost_tier=auto_tier,
        cost_tier_override=override,
        cost_tier=override if override is not None else auto_tier,
    )


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


def _normalize_display_name(value: str) -> str:
    """Allow at most one `/` for dropdown grouping (`Group/name`)."""
    normalized = value.strip()
    if not normalized:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Display name is required",
        )
    if normalized.count("/") > 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Display name may contain at most one '/'",
        )
    if "/" in normalized:
        group, _, label = normalized.partition("/")
        if not group.strip() or not label.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Display name group and label must both be non-empty",
            )
        return f"{group.strip()}/{label.strip()}"
    return normalized


def _normalize_openrouter_endpoint(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


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
    provider = get_provider_for_org(
        session,
        org_id,
        "vertex",
        org_config=provider_config,
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

    max_order = session.exec(select(func.max(ChatModel.display_order))).first() or 0
    normalized_model_name = _normalize_provider_model_name(
        payload.provider, payload.model_name
    )
    if payload.provider == "vertex":
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
        display_name=_normalize_display_name(payload.display_name),
        is_active=payload.is_active,
        display_order=max_order + 1,
        context_length=context_length,
        supports_image_input=supports_image_input_value,
        supports_image_output=supports_image_output_value,
        uses_responses_api=payload.uses_responses_api,
        reasoning_effort=_normalize_reasoning_effort(payload.reasoning_effort),
        openrouter_endpoint=(
            _normalize_openrouter_endpoint(payload.openrouter_endpoint)
            if payload.provider == "openrouter"
            else None
        ),
    )
    session.add(model)
    session.commit()
    session.refresh(model)

    link = session.exec(
        select(OrgModel).where(
            OrgModel.org_id == org_uuid,
            OrgModel.model_id == model.id,
        )
    ).first()
    if link:
        if not link.is_enabled:
            link.is_enabled = True
            session.add(link)
    else:
        session.add(OrgModel(org_id=org_uuid, model_id=model.id, is_enabled=True))
    seed_default_team_model(session, org_uuid, model.id, enabled=True)
    session.commit()

    return _model_read(model)


@router.get("", response_model=list[ModelRead])
def list_models(
    org_id: str | None = None,
    scope: str | None = None,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ModelRead]:
    # scope=org → org-enabled catalog for settings admins.
    # default/allowed → models the current user can actually send with (team ∩ org).
    requested_scope = (scope or "allowed").strip().lower()
    if requested_scope not in {"allowed", "org"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid scope (expected allowed or org)",
        )
    use_org_scope = False
    if current_user.is_super_admin and not org_id and requested_scope == "org":
        models = session.exec(
            select(ChatModel)
            .where(ChatModel.is_active.is_(True))
            .order_by(ChatModel.display_order, ChatModel.display_name, ChatModel.id)
        ).all()
        ensure_models_capabilities(session, models)
        return [_model_read(model) for model in models]
    if current_user.is_super_admin and org_id:
        try:
            org_uuid = UUID(org_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid org id"
            ) from exc
        use_org_scope = requested_scope == "org"
    elif current_user.is_super_admin and not org_id:
        # Super-admin chat picker without org context: global active catalog.
        models = session.exec(
            select(ChatModel)
            .where(ChatModel.is_active.is_(True))
            .order_by(ChatModel.display_order, ChatModel.display_name, ChatModel.id)
        ).all()
        ensure_models_capabilities(session, models)
        return [_model_read(model) for model in models]
    else:
        membership = session.exec(
            select(OrgMembership).where(OrgMembership.user_id == current_user.id)
        ).first()
        if not membership:
            return []
        org_uuid = membership.org_id
        if org_id:
            try:
                requested = UUID(org_id)
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid org id"
                ) from exc
            if requested != org_uuid:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN, detail="Org mismatch"
                )
        if requested_scope == "org":
            try:
                require_org_admin(session, org_uuid, current_user.id)
                use_org_scope = True
            except HTTPException:
                use_org_scope = False

    if use_org_scope:
        enabled_model_ids = session.exec(
            select(OrgModel.model_id).where(
                OrgModel.org_id == org_uuid, OrgModel.is_enabled.is_(True)
            )
        ).all()
    else:
        enabled_model_ids = list(
            allowed_model_ids(session, org_uuid, current_user.id)
        )

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
        _model_read(model, is_available=model.provider not in disabled_providers)
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


@router.get("/openrouter/endpoints", response_model=OpenRouterEndpointsResponse)
def list_openrouter_endpoints(
    model_name: str,
    current_user: User = Depends(get_current_user),
) -> OpenRouterEndpointsResponse:
    require_super_admin(current_user)
    trimmed = model_name.strip()
    endpoints, error = fetch_openrouter_endpoints(trimmed)
    return OpenRouterEndpointsResponse(
        model_name=trimmed,
        endpoints=[OpenRouterEndpointItem(**item) for item in endpoints],
        error=error,
    )


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
    return [_model_read(model) for model in ordered]


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
        model.display_name = _normalize_display_name(payload.display_name)
    if payload.reasoning_effort is not None:
        model.reasoning_effort = _normalize_reasoning_effort(payload.reasoning_effort)
    if payload.uses_responses_api is not None:
        model.uses_responses_api = payload.uses_responses_api
    if payload.openrouter_endpoint is not None:
        if model.provider != "openrouter":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="openrouter_endpoint is only valid for openrouter models",
            )
        model.openrouter_endpoint = _normalize_openrouter_endpoint(payload.openrouter_endpoint)
    if "cost_tier" in payload.model_fields_set:
        model.cost_tier_override = _normalize_cost_tier_override(payload.cost_tier)
    session.add(model)
    session.commit()
    session.refresh(model)
    return _model_read(model)


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
        seed_default_team_model(
            session, org_uuid, model_uuid, enabled=item.is_enabled
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
    return [_model_read(model) for model in models]
