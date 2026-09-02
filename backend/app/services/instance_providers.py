from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlmodel import Session, select

from app.core.config import settings
from app.core.secret_crypto import decrypt_secret, encrypt_secret
from app.models import (
    InstanceProviderConfig,
    InstanceProviderEnvSuppression,
    OrgProviderConfig,
)

BUILTIN_PROVIDERS = [
    "openai",
    "azure",
    "gemini",
    "groq",
    "anthropic",
    "openrouter",
    "vertex",
]

_PROVIDER_SLUG_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

_PROVIDER_ENV_SPECS: dict[str, dict[str, str]] = {
    "openai": {
        "api_key": "openai_api_key",
        "api_key_env": "OPENAI_API_KEY",
        "base_url": "openai_base_url",
        "base_url_env": "OPENAI_BASE_URL",
    },
    "azure": {
        "api_key": "azure_openai_api_key",
        "api_key_env": "AZURE_OPENAI_API_KEY",
        "endpoint": "azure_openai_endpoint",
        "endpoint_env": "AZURE_OPENAI_ENDPOINT",
    },
    "gemini": {
        "api_key": "gemini_api_key",
        "api_key_env": "GEMINI_API_KEY",
        "base_url": "gemini_base_url",
        "base_url_env": "GEMINI_BASE_URL",
    },
    "groq": {
        "api_key": "groq_api_key",
        "api_key_env": "GROQ_API_KEY",
        "base_url": "groq_base_url",
        "base_url_env": "GROQ_BASE_URL",
    },
    "anthropic": {
        "api_key": "anthropic_api_key",
        "api_key_env": "ANTHROPIC_API_KEY",
        "base_url": "anthropic_base_url",
        "base_url_env": "ANTHROPIC_BASE_URL",
    },
    "openrouter": {
        "api_key": "openrouter_api_key",
        "api_key_env": "OPENROUTER_API_KEY",
    },
    "vertex": {
        "config_json": "gemini_vertex_json",
        "config_json_env": "GEMINI_VERTEX_JSON",
        "project": "google_vertex_project",
        "project_env": "GOOGLE_VERTEX_PROJECT",
        "location": "google_vertex_location",
        "location_env": "GOOGLE_VERTEX_LOCATION",
    },
}

ENV_KEY_TO_PROVIDER_FIELD: dict[str, tuple[str, str]] = {}
for _provider, _spec in _PROVIDER_ENV_SPECS.items():
    for _field, _value in _spec.items():
        if _field.endswith("_env"):
            ENV_KEY_TO_PROVIDER_FIELD[_value] = (_provider, _field[: -len("_env")])


@dataclass
class ResolvedCredentials:
    api_key: str | None = None
    base_url: str | None = None
    endpoint: str | None = None
    config: dict[str, Any] | None = None
    provider_type: str = "builtin"


def normalize_provider_slug(value: str) -> str:
    slug = value.strip().lower().replace(" ", "-")
    if not slug or not _PROVIDER_SLUG_RE.match(slug):
        raise ValueError(
            "Provider id must start with a letter and contain only lowercase letters, numbers, hyphens, or underscores"
        )
    return slug


def list_provider_ids(session: Session) -> list[str]:
    custom = session.exec(
        select(InstanceProviderConfig.provider).where(
            InstanceProviderConfig.provider_type == "openai_compatible"
        )
    ).all()
    ordered = list(BUILTIN_PROVIDERS)
    for provider in custom:
        if provider not in ordered:
            ordered.append(provider)
    return ordered


def get_instance_provider_config(
    session: Session, provider: str
) -> InstanceProviderConfig | None:
    return session.exec(
        select(InstanceProviderConfig).where(InstanceProviderConfig.provider == provider)
    ).first()


def is_env_import_suppressed(session: Session, provider: str) -> bool:
    return (
        session.exec(
            select(InstanceProviderEnvSuppression).where(
                InstanceProviderEnvSuppression.provider == provider
            )
        ).first()
        is not None
    )


def suppress_env_import(session: Session, provider: str) -> None:
    if is_env_import_suppressed(session, provider):
        return
    session.add(InstanceProviderEnvSuppression(provider=provider))


def clear_env_import_suppression(session: Session, provider: str) -> None:
    record = session.exec(
        select(InstanceProviderEnvSuppression).where(
            InstanceProviderEnvSuppression.provider == provider
        )
    ).first()
    if record:
        session.delete(record)


def delete_instance_provider(session: Session, provider: str) -> None:
    record = get_instance_provider_config(session, provider)
    if not record:
        raise LookupError("Provider not found")
    if provider in _PROVIDER_ENV_SPECS:
        suppress_env_import(session, provider)
    session.delete(record)
    session.commit()
    invalidate_provider_caches()


def _settings_str(field: str) -> str | None:
    value = getattr(settings, field, None)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _vertex_config_from_settings() -> dict[str, Any]:
    config: dict[str, Any] = {}
    raw_json = _settings_str("gemini_vertex_json")
    if raw_json and raw_json not in {"", "{}"}:
        try:
            parsed = json.loads(raw_json)
            if isinstance(parsed, dict):
                config.update(parsed)
        except json.JSONDecodeError:
            pass
    project = _settings_str("google_vertex_project")
    if project and not config.get("project"):
        config["project"] = project
    location = _settings_str("google_vertex_location")
    if location and location != "global" and not config.get("location"):
        config["location"] = location
    return config


def _parse_stored_config_json(value: str | None) -> dict[str, Any] | None:
    raw = decrypt_secret(value)
    if not raw or raw.strip() in {"", "{}"}:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _encrypt_stored_config_json(value: str | None) -> str | None:
    if value is None:
        return None
    plain = value.strip()
    if not plain:
        return None
    return encrypt_secret(plain)


def reencrypt_plaintext_instance_secrets(session: Session) -> None:
    records = session.exec(select(InstanceProviderConfig)).all()
    changed = False
    for record in records:
        if record.config_json and not record.config_json.startswith("enc:v1:"):
            encrypted = encrypt_secret(record.config_json)
            if encrypted != record.config_json:
                record.config_json = encrypted
                record.updated_at = datetime.utcnow()
                session.add(record)
                changed = True
    if changed:
        session.commit()


def reencrypt_plaintext_org_provider_secrets(session: Session) -> None:
    from app.models import OrgProviderConfig

    records = session.exec(select(OrgProviderConfig)).all()
    changed = False
    for record in records:
        if record.config_json and not record.config_json.startswith("enc:v1:"):
            encrypted = encrypt_secret(record.config_json)
            if encrypted != record.config_json:
                record.config_json = encrypted
                session.add(record)
                changed = True
    if changed:
        session.commit()


def _instance_has_config(config: InstanceProviderConfig) -> bool:
    if config.provider == "vertex":
        if config.config_json and config.config_json.strip() not in {"", "{}"}:
            return True
        return False
    if config.provider == "azure":
        return bool(config.api_key and config.endpoint)
    if config.provider_type == "openai_compatible":
        return bool(config.api_key and config.base_url)
    return bool(config.api_key)


def has_instance_provider_config(session: Session, provider: str) -> bool:
    config = get_instance_provider_config(session, provider)
    return bool(config and config.is_enabled and _instance_has_config(config))


def _fallback_credentials(provider: str) -> ResolvedCredentials:
    spec = _PROVIDER_ENV_SPECS.get(provider)
    if not spec:
        return ResolvedCredentials()

    creds = ResolvedCredentials(provider_type="builtin")
    if provider == "vertex":
        config = _vertex_config_from_settings()
        creds.config = config or None
        return creds

    for field in ("api_key", "base_url", "endpoint"):
        settings_field = spec.get(field)
        if settings_field:
            value = _settings_str(settings_field)
            if field == "api_key":
                creds.api_key = value
            elif field == "base_url":
                creds.base_url = value
            elif field == "endpoint":
                creds.endpoint = value
    return creds


def resolve_instance_credentials(
    session: Session, provider: str
) -> ResolvedCredentials:
    config = get_instance_provider_config(session, provider)
    if not config or not config.is_enabled:
        fallback = _fallback_credentials(provider)
        if config:
            fallback.provider_type = config.provider_type
        return fallback

    creds = ResolvedCredentials(provider_type=config.provider_type)
    if config.api_key:
        creds.api_key = decrypt_secret(config.api_key)
    if config.base_url:
        creds.base_url = config.base_url.strip() or None
    if config.endpoint:
        creds.endpoint = config.endpoint.strip() or None
    if config.config_json:
        creds.config = _parse_stored_config_json(config.config_json)

    if not creds.api_key and provider != "vertex":
        fallback = _fallback_credentials(provider)
        creds.api_key = creds.api_key or fallback.api_key
        creds.base_url = creds.base_url or fallback.base_url
        creds.endpoint = creds.endpoint or fallback.endpoint
    if provider == "vertex" and not creds.config:
        creds.config = _fallback_credentials("vertex").config

    return creds


def resolve_effective_credentials(
    session: Session,
    provider: str,
    org_config: OrgProviderConfig | None,
) -> ResolvedCredentials:
    instance = resolve_instance_credentials(session, provider)
    if not org_config:
        return instance

    creds = ResolvedCredentials(
        api_key=instance.api_key,
        base_url=instance.base_url,
        endpoint=instance.endpoint,
        config=instance.config,
        provider_type=instance.provider_type,
    )
    if org_config.api_key_override:
        creds.api_key = org_config.api_key_override
    if org_config.base_url_override:
        creds.base_url = org_config.base_url_override.strip() or creds.base_url
    if org_config.endpoint_override:
        creds.endpoint = org_config.endpoint_override.strip() or creds.endpoint
    if org_config.config_json:
        parsed = _parse_stored_config_json(org_config.config_json)
        if parsed:
            creds.config = parsed
    return creds


def env_key_stored_in_db(session: Session, env_key: str) -> bool:
    mapping = ENV_KEY_TO_PROVIDER_FIELD.get(env_key)
    if not mapping:
        return False
    provider, field = mapping
    config = get_instance_provider_config(session, provider)
    if not config:
        return False
    if field == "api_key":
        return bool(config.api_key)
    if field == "base_url":
        return bool(config.base_url)
    if field == "endpoint":
        return bool(config.endpoint)
    if field == "config_json":
        return bool(config.config_json and config.config_json.strip() not in {"", "{}"})
    if field == "project":
        parsed = _parse_stored_config_json(config.config_json)
        if parsed and (
            parsed.get("project")
            or parsed.get("project_id")
            or parsed.get("projectId")
        ):
            return True
        return False
    if field == "location":
        return bool(config.config_json)
    return False


def _migrate_vertex_from_env(session: Session) -> InstanceProviderConfig | None:
    if is_env_import_suppressed(session, "vertex"):
        return None

    existing = get_instance_provider_config(session, "vertex")
    if existing and _instance_has_config(existing):
        return None

    config = _vertex_config_from_settings()
    if not config.get("project") and not config.get("project_id") and not config.get("projectId"):
        raw_json = _settings_str("gemini_vertex_json")
        if not raw_json or raw_json in {"", "{}"}:
            return None

    record = existing or InstanceProviderConfig(
        provider="vertex",
        provider_type="builtin",
        display_name="Vertex",
    )
    record.config_json = _encrypt_stored_config_json(
        json.dumps(config) if config else None
    )
    record.is_enabled = True
    record.migrated_from_env = True
    record.updated_at = datetime.utcnow()
    session.add(record)
    return record


def migrate_env_providers(session: Session) -> list[str]:
    migrated: list[str] = []

    for provider, spec in _PROVIDER_ENV_SPECS.items():
        if is_env_import_suppressed(session, provider):
            continue
        if provider == "vertex":
            if _migrate_vertex_from_env(session):
                migrated.append("vertex")
            continue

        existing = get_instance_provider_config(session, provider)
        if existing and _instance_has_config(existing):
            continue

        api_key_field = spec.get("api_key")
        api_key = _settings_str(api_key_field) if api_key_field else None
        if not api_key:
            continue

        record = existing or InstanceProviderConfig(
            provider=provider,
            provider_type="builtin",
            display_name=provider,
        )
        record.api_key = encrypt_secret(api_key)
        for field in ("base_url", "endpoint"):
            settings_field = spec.get(field)
            if settings_field:
                value = _settings_str(settings_field)
                if value:
                    setattr(record, field, value)
        record.is_enabled = True
        record.migrated_from_env = True
        record.updated_at = datetime.utcnow()
        session.add(record)
        migrated.append(provider)

    if migrated:
        session.commit()
    return migrated


def get_provider_for_org(
    session: Session,
    org_id,
    provider: str,
    *,
    org_config: OrgProviderConfig | None = None,
    reasoning_effort: str | None = None,
    prompt_cache_key: str | None = None,
    prompt_cache_retention: str | None = None,
    prompt_cache_enabled: bool = True,
    prefer_responses_api: bool = False,
    openrouter_endpoint: str | None = None,
    extra_body: dict | None = None,
):
    from app.services.org_service import require_provider_enabled
    from app.services.providers.registry import get_provider

    if org_config is None:
        org_config = require_provider_enabled(session, org_id, provider)

    creds = resolve_effective_credentials(session, provider, org_config)
    openai_compatible = creds.provider_type == "openai_compatible"

    return get_provider(
        provider,
        api_key=creds.api_key,
        base_url=creds.base_url,
        endpoint=creds.endpoint,
        reasoning_effort=reasoning_effort,
        prompt_cache_key=prompt_cache_key,
        prompt_cache_retention=prompt_cache_retention,
        prompt_cache_enabled=prompt_cache_enabled,
        prefer_responses_api=prefer_responses_api,
        config=creds.config,
        extra_body=extra_body,
        openrouter_endpoint=openrouter_endpoint,
        openai_compatible=openai_compatible,
    )


def provider_is_configured(creds: ResolvedCredentials, provider: str) -> bool:
    if provider == "vertex":
        config = creds.config or {}
        return bool(
            config.get("project")
            or config.get("project_id")
            or config.get("projectId")
        )
    if provider == "azure":
        return bool(creds.api_key and creds.endpoint)
    if creds.provider_type == "openai_compatible":
        return bool(creds.api_key and creds.base_url)
    return bool(creds.api_key)


def invalidate_provider_caches() -> None:
    from app.services.model_suggestions import invalidate_model_suggestions_cache

    invalidate_model_suggestions_cache()
