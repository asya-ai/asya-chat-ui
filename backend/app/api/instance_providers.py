from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.api.deps import get_current_user, get_db
from app.core.secret_crypto import encrypt_secret
from app.models import InstanceProviderConfig, User
from app.services.instance_providers import (
    BUILTIN_PROVIDERS,
    get_instance_provider_config,
    invalidate_provider_caches,
    list_provider_ids,
    normalize_provider_slug,
)
from app.services.org_service import require_super_admin

router = APIRouter(prefix="/admin/instance-providers", tags=["admin"])


class InstanceProviderRead(BaseModel):
    provider: str
    display_name: str | None
    provider_type: str
    is_enabled: bool
    api_key_set: bool
    base_url: str | None
    endpoint: str | None
    config_json_set: bool
    migrated_from_env: bool
    is_configured: bool


class InstanceProviderCreate(BaseModel):
    provider: str
    display_name: str | None = None
    provider_type: str = Field(default="builtin")
    api_key: str | None = None
    base_url: str | None = None
    endpoint: str | None = None
    config_json: str | None = None
    is_enabled: bool = True


class InstanceProviderUpdate(BaseModel):
    display_name: str | None = None
    is_enabled: bool | None = None
    api_key: str | None = None
    base_url: str | None = None
    endpoint: str | None = None
    config_json: str | None = None


def _is_configured(record: InstanceProviderConfig) -> bool:
    if record.provider == "vertex":
        return bool(record.config_json and record.config_json.strip() not in {"", "{}"})
    if record.provider == "azure":
        return bool(record.api_key and record.endpoint)
    if record.provider_type == "openai_compatible":
        return bool(record.api_key and record.base_url)
    return bool(record.api_key)


def _to_read(record: InstanceProviderConfig) -> InstanceProviderRead:
    return InstanceProviderRead(
        provider=record.provider,
        display_name=record.display_name,
        provider_type=record.provider_type,
        is_enabled=record.is_enabled,
        api_key_set=bool(record.api_key),
        base_url=record.base_url,
        endpoint=record.endpoint,
        config_json_set=bool(record.config_json and record.config_json.strip() not in {"", "{}"}),
        migrated_from_env=record.migrated_from_env,
        is_configured=_is_configured(record),
    )


@router.get("", response_model=list[InstanceProviderRead])
def list_instance_providers(
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[InstanceProviderRead]:
    require_super_admin(current_user)
    records = session.exec(select(InstanceProviderConfig)).all()
    by_provider = {record.provider: record for record in records}
    results: list[InstanceProviderRead] = []
    for provider in list_provider_ids(session):
        record = by_provider.get(provider)
        if record:
            results.append(_to_read(record))
        else:
            results.append(
                InstanceProviderRead(
                    provider=provider,
                    display_name=provider,
                    provider_type=(
                        "openai_compatible"
                        if provider not in BUILTIN_PROVIDERS
                        else "builtin"
                    ),
                    is_enabled=True,
                    api_key_set=False,
                    base_url=None,
                    endpoint=None,
                    config_json_set=False,
                    migrated_from_env=False,
                    is_configured=False,
                )
            )
    return results


@router.post("", response_model=InstanceProviderRead, status_code=status.HTTP_201_CREATED)
def create_instance_provider(
    payload: InstanceProviderCreate,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> InstanceProviderRead:
    require_super_admin(current_user)
    try:
        provider = normalize_provider_slug(payload.provider)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    provider_type = payload.provider_type.strip().lower()
    if provider_type not in {"builtin", "openai_compatible"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="provider_type must be builtin or openai_compatible",
        )
    if provider_type == "builtin" and provider not in BUILTIN_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unknown built-in provider",
        )
    if provider_type == "openai_compatible" and provider in BUILTIN_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Built-in provider ids cannot be used for custom providers",
        )
    if get_instance_provider_config(session, provider):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Provider already configured",
        )

    record = InstanceProviderConfig(
        provider=provider,
        display_name=(payload.display_name or provider).strip() or provider,
        provider_type=provider_type,
        is_enabled=payload.is_enabled,
        api_key=encrypt_secret(payload.api_key),
        base_url=(payload.base_url or "").strip() or None,
        endpoint=(payload.endpoint or "").strip() or None,
        config_json=encrypt_secret((payload.config_json or "").strip() or None),
        migrated_from_env=False,
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    invalidate_provider_caches()
    return _to_read(record)


@router.put("/{provider}", response_model=InstanceProviderRead)
def update_instance_provider(
    provider: str,
    payload: InstanceProviderUpdate,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> InstanceProviderRead:
    require_super_admin(current_user)
    record = get_instance_provider_config(session, provider)
    if not record:
        if provider not in BUILTIN_PROVIDERS:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Provider not found"
            )
        record = InstanceProviderConfig(
            provider=provider,
            display_name=provider,
            provider_type="builtin",
            is_enabled=True,
        )

    if payload.display_name is not None:
        record.display_name = payload.display_name.strip() or record.provider
    if payload.is_enabled is not None:
        record.is_enabled = payload.is_enabled
    if payload.api_key is not None:
        record.api_key = encrypt_secret(payload.api_key)
    if payload.base_url is not None:
        record.base_url = payload.base_url.strip() or None
    if payload.endpoint is not None:
        record.endpoint = payload.endpoint.strip() or None
    if payload.config_json is not None:
        record.config_json = encrypt_secret(payload.config_json.strip() or None)

    record.updated_at = datetime.utcnow()
    session.add(record)
    session.commit()
    session.refresh(record)
    invalidate_provider_caches()
    return _to_read(record)


@router.delete("/{provider}", status_code=status.HTTP_204_NO_CONTENT)
def delete_instance_provider(
    provider: str,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    require_super_admin(current_user)
    if provider in BUILTIN_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Built-in providers cannot be deleted; disable them instead",
        )
    record = get_instance_provider_config(session, provider)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Provider not found"
        )
    session.delete(record)
    session.commit()
    invalidate_provider_caches()
