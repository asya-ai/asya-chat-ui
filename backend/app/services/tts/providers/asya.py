from __future__ import annotations

import array
import base64
import io
import struct
import wave
from typing import Any

import httpx

from app.services.tts.types import DEFAULT_ASYA_BASE_URL, TtsAudio, TtsError, TtsVoice


def _normalize_token(value: str) -> str:
    return value.strip().strip("\ufeff")


def _asya_voice_items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("voices", "items", "data", "results"):
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = value.get("voices") or value.get("items") or value.get("data")
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
    return []


def _voice_id(item: dict[str, Any]) -> str:
    for key in ("uuid", "voice_uuid", "voice_id", "id"):
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _meta_int(metadata: dict[str, Any], key: str, default: int) -> int:
    value = metadata.get(key)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _pcm_to_wav(pcm_bytes: bytes, *, sample_rate: int, channels: int, sampwidth: int) -> bytes:
    if channels <= 0 or sample_rate <= 0:
        raise TtsError("Asya audio metadata invalid")
    if not pcm_bytes:
        raise TtsError("Asya synthesize returned no audio")
    if len(pcm_bytes) % (sampwidth * channels) != 0:
        raise TtsError("Asya PCM payload invalid")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buffer.getvalue()


def _float_pcm_to_wav(raw: bytes, *, sample_rate: int, channels: int, sample_width: int) -> bytes:
    if sample_width == 8:
        count = len(raw) // 8
        if count == 0 or len(raw) % 8 != 0:
            raise TtsError("Asya PCM payload invalid")
        floats = struct.unpack(f"<{count}d", raw)
    else:
        count = len(raw) // 4
        if count == 0 or len(raw) % 4 != 0:
            raise TtsError("Asya PCM payload invalid")
        floats = struct.unpack(f"<{count}f", raw)
    if count % channels != 0:
        raise TtsError("Asya PCM payload invalid")
    samples = array.array("h")
    for value in floats:
        clipped = max(-1.0, min(1.0, float(value)))
        samples.append(int(clipped * 32767.0))
    return _pcm_to_wav(
        samples.tobytes(),
        sample_rate=sample_rate,
        channels=channels,
        sampwidth=2,
    )


def _browser_audio_from_asya(raw: bytes, metadata: dict[str, Any]) -> TtsAudio:
    if raw[:4] == b"RIFF":
        return TtsAudio(content=raw, content_type="audio/wav")

    fmt = str(metadata.get("audio_format") or "wav32").strip().lower()
    sample_rate = _meta_int(metadata, "sample_rate", 24000)
    channels = _meta_int(metadata, "channels", 1)

    if fmt == "mp3":
        return TtsAudio(content=raw, content_type="audio/mpeg")
    if fmt == "wav16":
        wav = _pcm_to_wav(raw, sample_rate=sample_rate, channels=channels, sampwidth=2)
        return TtsAudio(content=wav, content_type="audio/wav")
    if fmt == "wav64":
        wav = _float_pcm_to_wav(
            raw, sample_rate=sample_rate, channels=channels, sample_width=8
        )
        return TtsAudio(content=wav, content_type="audio/wav")
    # wav32 default — raw float32 PCM from Asya API
    wav = _float_pcm_to_wav(
        raw, sample_rate=sample_rate, channels=channels, sample_width=4
    )
    return TtsAudio(content=wav, content_type="audio/wav")


def _is_auth_failure(response: httpx.Response) -> bool:
    if response.status_code == 401:
        return True
    text = response.text or ""
    if "Invalid token" in text or "Not authenticated" in text:
        return True
    try:
        data = response.json()
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    err = data.get("error")
    if isinstance(err, dict) and err.get("code") == 401:
        return True
    return data.get("detail") == "Not authenticated"


def _asya_api_error(data: dict[str, Any], *, fallback: str) -> TtsError:
    if data.get("success") is not False:
        return TtsError(fallback)
    err = data.get("error")
    if isinstance(err, dict):
        message = str(err.get("message") or fallback).strip()
        code = err.get("code")
        if code is not None:
            return TtsError(f"{fallback} ({code}): {message}")
        return TtsError(f"{fallback}: {message}")
    return TtsError(fallback)


def _asya_audio_from_json(data: dict[str, Any]) -> TtsAudio:
    if data.get("success") is False:
        raise _asya_api_error(data, fallback="Asya synthesize failed")

    payload: dict[str, Any] = data
    nested = data.get("data")
    if isinstance(nested, dict) and isinstance(nested.get("data"), str):
        payload = nested

    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    audio_b64 = payload.get("data")
    if not isinstance(audio_b64, str) or not audio_b64.strip():
        audio_b64 = (
            data.get("audio")
            or data.get("audio_base64")
            or data.get("audioContent")
        )
        metadata = {}

    if not isinstance(audio_b64, str) or not audio_b64.strip():
        raise TtsError("Asya synthesize returned no audio")

    raw = audio_b64.strip()
    if "," in raw and raw.startswith("data:"):
        raw = raw.split(",", 1)[1]
    try:
        content = base64.b64decode(raw)
    except Exception as exc:
        raise TtsError("Asya audio decode failed") from exc
    if not content:
        raise TtsError("Asya synthesize returned no audio")
    return _browser_audio_from_asya(content, metadata)


def _authorization_candidates(api_key: str) -> list[str]:
    """Match Asya demo: use token as entered; also try Bearer variant."""
    raw = _normalize_token(api_key)
    if not raw:
        return []
    if raw.lower().startswith("bearer "):
        bare = raw[7:].strip()
        return [raw] + ([bare] if bare else [])
    return [raw, f"Bearer {raw}"]


class AsyaTtsProvider:
    id = "asya"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str | None = None,
        speed: float = 1.0,
    ) -> None:
        self._api_key = _normalize_token(api_key)
        self._base_url = (base_url or DEFAULT_ASYA_BASE_URL).rstrip("/")
        self._speed = max(0.5, min(2.0, float(speed)))

    def _request(
        self,
        client: httpx.Client,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        error_prefix: str,
    ) -> httpx.Response:
        last_detail = error_prefix
        for auth in _authorization_candidates(self._api_key):
            headers = {
                "Authorization": auth,
                "Accept": "*/*",
                "Content-Type": "application/json",
            }
            try:
                response = client.request(
                    method,
                    f"{self._base_url}{path}",
                    headers=headers,
                    json=json,
                )
            except httpx.HTTPError as exc:
                last_detail = f"{error_prefix}: {exc}"
                continue
            if response.status_code >= 400:
                detail = (
                    f"{error_prefix} ({response.status_code}): "
                    f"{(response.text or '')[:300]}"
                )
                if _is_auth_failure(response):
                    last_detail = detail
                    continue
                raise TtsError(detail)
            return response
        raise TtsError(last_detail)

    def list_voices(self) -> list[TtsVoice]:
        with httpx.Client(timeout=30.0) as client:
            response = self._request(
                client, "GET", "/voices", error_prefix="Asya voices failed"
            )
        try:
            data = response.json()
        except Exception as exc:
            raise TtsError("Asya voices returned invalid JSON") from exc
        items = _asya_voice_items(data)
        voices: list[TtsVoice] = []
        for item in items:
            voice_id = _voice_id(item)
            if not voice_id:
                continue
            name = str(
                item.get("name")
                or item.get("voice_name")
                or item.get("display_name")
                or voice_id
            ).strip()
            language = item.get("language") or item.get("lang")
            voices.append(
                TtsVoice(
                    id=voice_id,
                    name=name,
                    language=str(language) if language else None,
                )
            )
        if not voices:
            raise TtsError("Asya returned no voices for this account")
        return voices

    def synthesize(self, text: str, voice_id: str) -> TtsAudio:
        payload: dict[str, Any] = {
            "text": text,
            "voice_uuid": voice_id,
            "audio_format": "wav32",
            "sample_rate": 24000,
            "speed": self._speed,
            "quality": 32,
        }
        with httpx.Client(timeout=90.0) as client:
            response = self._request(
                client,
                "POST",
                "/synthesize",
                json=payload,
                error_prefix="Asya synthesize failed",
            )

        content_type = (response.headers.get("content-type") or "").split(";")[0].strip()
        if content_type.startswith("audio/"):
            return TtsAudio(
                content=response.content,
                content_type=content_type or "audio/mpeg",
            )

        try:
            data = response.json()
        except Exception as exc:
            raise TtsError("Asya synthesize returned unexpected payload") from exc
        if not isinstance(data, dict):
            raise TtsError("Asya synthesize returned unexpected payload")
        return _asya_audio_from_json(data)


def verify_asya_credentials(*, api_key: str, base_url: str | None = None) -> None:
    """Raises TtsError when the token cannot call Asya."""
    AsyaTtsProvider(api_key=api_key, base_url=base_url).list_voices()
