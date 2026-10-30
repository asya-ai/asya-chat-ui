from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlmodel import Session, select

from app.core.secret_crypto import decrypt_secret, encrypt_secret
from app.models import InstanceTtsSettings
from app.services.instance_providers import resolve_instance_credentials
from app.services.tts.types import (
    CLOUD_TTS_PROVIDERS,
    DEFAULT_ASYA_BASE_URL,
    TTS_PROVIDERS,
    TtsError,
)


def get_tts_settings(session: Session) -> InstanceTtsSettings:
    row = session.exec(select(InstanceTtsSettings).where(InstanceTtsSettings.id == 1)).first()
    if row:
        return row
    row = InstanceTtsSettings(
        id=1,
        provider="asya",
        is_enabled=False,
        base_url=DEFAULT_ASYA_BASE_URL,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def parse_config_json(raw: str | None) -> dict[str, Any]:
    if not raw or not raw.strip():
        return {}
    try:
        decrypted = decrypt_secret(raw) or raw
        data = json.loads(decrypted)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def resolved_api_key(session: Session, row: InstanceTtsSettings) -> str | None:
    if row.api_key:
        value = decrypt_secret(row.api_key)
        if value is None:
            return None
        cleaned = value.strip().strip("\ufeff")
        return cleaned or None
    provider = (row.provider or "").strip().lower()
    if provider == "openai":
        creds = resolve_instance_credentials(session, "openai")
        return creds.api_key
    if provider == "google":
        for candidate in ("gemini", "vertex"):
            creds = resolve_instance_credentials(session, candidate)
            if creds.api_key:
                return creds.api_key
            if isinstance(creds.config, dict):
                # Vertex often uses service account JSON — not usable as Cloud TTS API key.
                continue
    return None


def resolved_openai_base_url(session: Session, row: InstanceTtsSettings) -> str | None:
    if row.base_url and row.base_url.strip():
        return row.base_url.strip()
    creds = resolve_instance_credentials(session, "openai")
    return creds.base_url


def settings_public(
    session: Session,
    row: InstanceTtsSettings,
    *,
    include_secrets_meta: bool = True,
) -> dict[str, Any]:
    config = parse_config_json(row.config_json)
    payload: dict[str, Any] = {
        "provider": row.provider,
        "is_enabled": bool(row.is_enabled),
        "base_url": row.base_url,
        "voice_id": row.voice_id,
        "voice_label": row.voice_label,
        "region": config.get("region"),
        "model": config.get("model"),
        "language": config.get("language"),
        "speed": _resolved_asya_speed(config),
    }
    if include_secrets_meta:
        payload["api_key_set"] = bool(resolved_api_key(session, row))
        payload["config"] = {
            key: value
            for key, value in config.items()
            if key in {"region", "model", "language", "model_id", "speed"}
        }
    return payload


def _resolved_asya_speed(config: dict[str, Any]) -> float:
    raw = config.get("speed", 1.0)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 1.0
    return max(0.5, min(2.0, value))


def status_public(session: Session, row: InstanceTtsSettings) -> dict[str, Any]:
    provider = (row.provider or "").strip().lower()
    if provider not in TTS_PROVIDERS:
        provider = "asya"
    enabled = bool(row.is_enabled)
    voice_id = (row.voice_id or "").strip()
    if enabled:
        if not voice_id:
            enabled = False
        elif provider in CLOUD_TTS_PROVIDERS and not resolved_api_key(session, row):
            enabled = False
    return {
        "enabled": enabled,
        "provider": provider,
        "voice_id": voice_id if enabled else None,
        "voice_label": row.voice_label if enabled else None,
    }


def update_tts_settings(
    session: Session,
    *,
    provider: str | None = None,
    is_enabled: bool | None = None,
    api_key: str | None = None,
    clear_api_key: bool = False,
    base_url: str | None = None,
    voice_id: str | None = None,
    voice_label: str | None = None,
    region: str | None = None,
    model: str | None = None,
    language: str | None = None,
    model_id: str | None = None,
    speed: float | None = None,
) -> InstanceTtsSettings:
    row = get_tts_settings(session)
    if provider is not None:
        normalized = provider.strip().lower()
        if normalized not in TTS_PROVIDERS:
            raise TtsError("Unknown TTS provider")
        row.provider = normalized
        if normalized == "asya" and not (base_url or row.base_url):
            row.base_url = DEFAULT_ASYA_BASE_URL

    if is_enabled is not None:
        row.is_enabled = is_enabled

    if clear_api_key:
        row.api_key = None
    elif api_key is not None and api_key.strip():
        from app.services.tts.providers.asya import verify_asya_credentials

        provider_for_key = (provider or row.provider or "").strip().lower()
        if provider_for_key == "asya":
            verify_asya_credentials(
                api_key=api_key.strip(),
                base_url=(base_url if base_url is not None else row.base_url),
            )
        row.api_key = encrypt_secret(api_key)

    if base_url is not None:
        cleaned = base_url.strip()
        row.base_url = cleaned or None

    if voice_id is not None:
        row.voice_id = voice_id.strip() or None
    if voice_label is not None:
        row.voice_label = voice_label.strip() or None

    config = parse_config_json(row.config_json)
    if region is not None:
        if region.strip():
            config["region"] = region.strip()
        else:
            config.pop("region", None)
    if model is not None:
        if model.strip():
            config["model"] = model.strip()
        else:
            config.pop("model", None)
    if language is not None:
        if language.strip():
            config["language"] = language.strip()
        else:
            config.pop("language", None)
    if model_id is not None:
        if model_id.strip():
            config["model_id"] = model_id.strip()
        else:
            config.pop("model_id", None)
    if speed is not None:
        if speed <= 0:
            config.pop("speed", None)
        else:
            if speed < 0.5 or speed > 2.0:
                raise TtsError("Asya speed must be between 0.5 and 2")
            config["speed"] = float(speed)

    if config:
        row.config_json = encrypt_secret(json.dumps(config))
    else:
        row.config_json = None

    if row.is_enabled:
        if row.provider in CLOUD_TTS_PROVIDERS and not (row.voice_id or "").strip():
            raise TtsError("Voice is required when TTS is enabled")
        if row.provider == "browser" and not (row.voice_id or "").strip():
            raise TtsError("Voice is required when TTS is enabled")
        if row.provider == "azure":
            cfg = parse_config_json(row.config_json)
            if not (row.base_url or "").strip() and not str(cfg.get("region") or "").strip():
                raise TtsError("Azure Speech requires region or base URL")
        provider_id = (row.provider or "").strip().lower()
        if provider_id == "asya":
            from app.services.tts.providers.asya import verify_asya_credentials

            key = resolved_api_key(session, row)
            if not key:
                raise TtsError("Asya API key is required")
            verify_asya_credentials(api_key=key, base_url=row.base_url)

    row.updated_at = datetime.utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)
    return row
