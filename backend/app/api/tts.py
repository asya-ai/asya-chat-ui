from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.api.deps import get_current_user, get_db
from app.models import User
from app.services.org_service import require_super_admin
from app.services.tts.registry import build_provider
from app.services.tts.store import (
    get_tts_settings,
    settings_public,
    status_public,
    update_tts_settings,
)
from app.services.tts.text import plain_text_for_tts
from app.services.tts.types import (
    CLOUD_TTS_PROVIDERS,
    MAX_SYNTHESIZE_CHARS,
    TTS_PROVIDERS,
    TtsError,
)

router = APIRouter(prefix="/tts", tags=["tts"])


class TtsVoiceRead(BaseModel):
    id: str
    name: str
    language: str | None = None


class TtsSettingsRead(BaseModel):
    provider: str
    is_enabled: bool
    api_key_set: bool
    base_url: str | None = None
    voice_id: str | None = None
    voice_label: str | None = None
    region: str | None = None
    model: str | None = None
    language: str | None = None
    speed: float | None = None
    providers: list[str] = Field(default_factory=lambda: list(TTS_PROVIDERS))


class TtsSettingsUpdate(BaseModel):
    provider: str | None = None
    is_enabled: bool | None = None
    api_key: str | None = None
    clear_api_key: bool = False
    base_url: str | None = None
    voice_id: str | None = None
    voice_label: str | None = None
    region: str | None = None
    model: str | None = None
    language: str | None = None
    model_id: str | None = None
    speed: float | None = None


class TtsStatusRead(BaseModel):
    enabled: bool
    provider: str
    voice_id: str | None = None
    voice_label: str | None = None


class TtsSynthesizeRequest(BaseModel):
    text: str


class TtsVoicesPreviewRequest(BaseModel):
    """Optional draft credentials so admins can list voices before saving."""

    provider: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    region: str | None = None
    model: str | None = None
    language: str | None = None
    model_id: str | None = None


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, TtsError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=str(exc) or "TTS provider error",
    )


@router.get("/status", response_model=TtsStatusRead)
def get_tts_status(
    session: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> TtsStatusRead:
    row = get_tts_settings(session)
    return TtsStatusRead(**status_public(session, row))


@router.get("/settings", response_model=TtsSettingsRead)
def get_tts_admin_settings(
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TtsSettingsRead:
    require_super_admin(current_user)
    row = get_tts_settings(session)
    data = settings_public(session, row)
    return TtsSettingsRead(
        provider=data["provider"],
        is_enabled=data["is_enabled"],
        api_key_set=data["api_key_set"],
        base_url=data.get("base_url"),
        voice_id=data.get("voice_id"),
        voice_label=data.get("voice_label"),
        region=data.get("region"),
        model=data.get("model"),
        language=data.get("language"),
        speed=data.get("speed"),
        providers=list(TTS_PROVIDERS),
    )


@router.put("/settings", response_model=TtsSettingsRead)
def put_tts_admin_settings(
    payload: TtsSettingsUpdate,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TtsSettingsRead:
    require_super_admin(current_user)
    try:
        row = update_tts_settings(
            session,
            provider=payload.provider,
            is_enabled=payload.is_enabled,
            api_key=payload.api_key,
            clear_api_key=payload.clear_api_key,
            base_url=payload.base_url,
            voice_id=payload.voice_id,
            voice_label=payload.voice_label,
            region=payload.region,
            model=payload.model,
            language=payload.language,
            model_id=payload.model_id,
            speed=payload.speed,
        )
    except TtsError as exc:
        raise _http_error(exc) from exc
    data = settings_public(session, row)
    return TtsSettingsRead(
        provider=data["provider"],
        is_enabled=data["is_enabled"],
        api_key_set=data["api_key_set"],
        base_url=data.get("base_url"),
        voice_id=data.get("voice_id"),
        voice_label=data.get("voice_label"),
        region=data.get("region"),
        model=data.get("model"),
        language=data.get("language"),
        speed=data.get("speed"),
        providers=list(TTS_PROVIDERS),
    )


def _list_voices_for_admin(
    session: Session,
    *,
    provider: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    region: str | None = None,
    model: str | None = None,
    language: str | None = None,
    model_id: str | None = None,
) -> list[TtsVoiceRead]:
    row = get_tts_settings(session)
    provider_id = (provider or row.provider or "").strip().lower()
    if provider_id == "browser":
        return []
    try:
        adapter = build_provider(
            session,
            row,
            provider=provider_id,
            api_key=api_key,
            base_url=base_url,
            region=region,
            model=model,
            language=language,
            model_id=model_id,
        )
        voices = adapter.list_voices()
    except TtsError as exc:
        raise _http_error(exc) from exc
    except Exception as exc:
        raise _http_error(exc) from exc
    return [
        TtsVoiceRead(id=voice.id, name=voice.name, language=voice.language)
        for voice in voices
    ]


@router.get("/voices", response_model=list[TtsVoiceRead])
def list_tts_voices(
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[TtsVoiceRead]:
    require_super_admin(current_user)
    return _list_voices_for_admin(session)


@router.post("/voices", response_model=list[TtsVoiceRead])
def preview_tts_voices(
    payload: TtsVoicesPreviewRequest,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[TtsVoiceRead]:
    require_super_admin(current_user)
    return _list_voices_for_admin(
        session,
        provider=payload.provider,
        api_key=payload.api_key,
        base_url=payload.base_url,
        region=payload.region,
        model=payload.model,
        language=payload.language,
        model_id=payload.model_id,
    )


@router.post("/synthesize")
def synthesize_tts(
    payload: TtsSynthesizeRequest,
    session: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Response:
    row = get_tts_settings(session)
    status_payload = status_public(session, row)
    if not status_payload["enabled"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="TTS is not enabled",
        )
    provider = status_payload["provider"]
    if provider == "browser":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Browser TTS is client-side only",
        )
    if provider not in CLOUD_TTS_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported TTS provider",
        )
    voice_id = (row.voice_id or "").strip()
    if not voice_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="TTS voice is not configured",
        )
    text = plain_text_for_tts(payload.text, max_chars=MAX_SYNTHESIZE_CHARS)
    if not text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Nothing to speak",
        )
    try:
        adapter = build_provider(session, row)
        audio = adapter.synthesize(text, voice_id)
    except TtsError as exc:
        raise _http_error(exc) from exc
    except Exception as exc:
        raise _http_error(exc) from exc
    return Response(
        content=audio.content,
        media_type=audio.content_type or "audio/mpeg",
        headers={"Cache-Control": "no-store"},
    )
