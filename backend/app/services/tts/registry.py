from __future__ import annotations

from sqlmodel import Session

from app.models import InstanceTtsSettings
from app.services.tts.providers.asya import AsyaTtsProvider
from app.services.tts.providers.azure_tts import AzureTtsProvider
from app.services.tts.providers.elevenlabs import ElevenLabsTtsProvider
from app.services.tts.providers.google_tts import GoogleTtsProvider
from app.services.tts.providers.openai_tts import OpenAiTtsProvider
from app.services.tts.store import (
    _resolved_asya_speed,
    parse_config_json,
    resolved_api_key,
    resolved_openai_base_url,
)
from app.services.tts.types import DEFAULT_ASYA_BASE_URL, TtsError, TtsProvider


def build_provider(
    session: Session,
    row: InstanceTtsSettings,
    *,
    provider: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    region: str | None = None,
    model: str | None = None,
    language: str | None = None,
    model_id: str | None = None,
    speed: float | None = None,
) -> TtsProvider:
    """Build a provider from saved settings, optionally overridden by draft values."""
    provider_id = (provider or row.provider or "").strip().lower()
    config = parse_config_json(row.config_json)

    draft_key = (api_key or "").strip() or None
    saved_key = resolved_api_key(session, row) if not draft_key else None
    effective_key = draft_key or saved_key

    draft_base = (base_url if base_url is not None else row.base_url) or None
    if isinstance(draft_base, str):
        draft_base = draft_base.strip() or None

    effective_region = (
        (region or "").strip()
        or str(config.get("region") or "").strip()
        or None
    )
    effective_model = (
        (model or "").strip()
        or str(config.get("model") or "").strip()
        or None
    )
    effective_language = (
        (language or "").strip()
        or str(config.get("language") or "").strip()
        or None
    )
    effective_model_id = (
        (model_id or "").strip()
        or str(config.get("model_id") or config.get("model") or "").strip()
        or None
    )

    if provider_id == "browser":
        raise TtsError("Browser TTS is client-side only")

    if provider_id == "asya":
        if not effective_key:
            raise TtsError("Asya TTS API key is not configured")
        effective_speed = (
            float(speed)
            if speed is not None and speed > 0
            else _resolved_asya_speed(config)
        )
        return AsyaTtsProvider(
            api_key=effective_key,
            base_url=draft_base or DEFAULT_ASYA_BASE_URL,
            speed=effective_speed,
        )

    if provider_id == "openai":
        if not effective_key:
            # Fall back to instance OpenAI when draft didn't supply a key.
            if not draft_key:
                effective_key = resolved_api_key(session, row)
            if not effective_key:
                raise TtsError("OpenAI TTS API key is not configured")
        openai_base = draft_base
        if not openai_base and (not provider or provider_id == (row.provider or "").strip().lower()):
            openai_base = resolved_openai_base_url(session, row)
        return OpenAiTtsProvider(
            api_key=effective_key,
            base_url=openai_base,
            model=effective_model or "tts-1",
        )

    if provider_id == "google":
        if not effective_key:
            raise TtsError("Google TTS API key is not configured")
        return GoogleTtsProvider(
            api_key=effective_key,
            base_url=draft_base,
            language_code=effective_language,
        )

    if provider_id == "elevenlabs":
        if not effective_key:
            raise TtsError("ElevenLabs API key is not configured")
        return ElevenLabsTtsProvider(
            api_key=effective_key,
            base_url=draft_base,
            model_id=effective_model_id,
        )

    if provider_id == "azure":
        if not effective_key:
            raise TtsError("Azure Speech key is not configured")
        return AzureTtsProvider(
            api_key=effective_key,
            region=effective_region,
            base_url=draft_base,
        )

    raise TtsError(f"Unknown TTS provider: {provider_id}")
