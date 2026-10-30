from __future__ import annotations

from typing import Any

import httpx

from app.services.tts.types import TtsAudio, TtsError, TtsVoice

# Curated fallback when list API is unavailable with the configured key.
_GOOGLE_FALLBACK_VOICES = [
    TtsVoice(id="en-US-Neural2-A", name="en-US Neural2 A", language="en-US"),
    TtsVoice(id="en-US-Neural2-C", name="en-US Neural2 C", language="en-US"),
    TtsVoice(id="en-US-Neural2-D", name="en-US Neural2 D", language="en-US"),
    TtsVoice(id="en-US-Neural2-F", name="en-US Neural2 F", language="en-US"),
    TtsVoice(id="en-GB-Neural2-A", name="en-GB Neural2 A", language="en-GB"),
    TtsVoice(id="en-GB-Neural2-B", name="en-GB Neural2 B", language="en-GB"),
    TtsVoice(id="de-DE-Neural2-A", name="de-DE Neural2 A", language="de-DE"),
    TtsVoice(id="fr-FR-Neural2-A", name="fr-FR Neural2 A", language="fr-FR"),
    TtsVoice(id="lv-LV-Standard-A", name="lv-LV Standard A", language="lv-LV"),
]


class GoogleTtsProvider:
    id = "google"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str | None = None,
        language_code: str | None = None,
    ) -> None:
        self._api_key = api_key.strip()
        self._base_url = (base_url or "https://texttospeech.googleapis.com/v1").rstrip("/")
        self._language_code = (language_code or "").strip() or None

    def list_voices(self) -> list[TtsVoice]:
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.get(
                    f"{self._base_url}/voices",
                    params={"key": self._api_key},
                )
        except httpx.HTTPError:
            return list(_GOOGLE_FALLBACK_VOICES)
        if response.status_code >= 400:
            return list(_GOOGLE_FALLBACK_VOICES)
        data = response.json()
        items = data.get("voices") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return list(_GOOGLE_FALLBACK_VOICES)
        voices: list[TtsVoice] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            langs = item.get("languageCodes") or []
            language = langs[0] if isinstance(langs, list) and langs else None
            if self._language_code and language and not str(language).startswith(
                self._language_code.split("-")[0]
            ):
                continue
            voices.append(TtsVoice(id=name, name=name, language=str(language) if language else None))
        return voices or list(_GOOGLE_FALLBACK_VOICES)

    def synthesize(self, text: str, voice_id: str) -> TtsAudio:
        language = self._language_code
        if not language and "-" in voice_id:
            # en-US-Neural2-A → en-US
            parts = voice_id.split("-")
            if len(parts) >= 2:
                language = f"{parts[0]}-{parts[1]}"
        language = language or "en-US"
        payload: dict[str, Any] = {
            "input": {"text": text},
            "voice": {"languageCode": language, "name": voice_id},
            "audioConfig": {"audioEncoding": "MP3"},
        }
        try:
            with httpx.Client(timeout=90.0) as client:
                response = client.post(
                    f"{self._base_url}/text:synthesize",
                    params={"key": self._api_key},
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise TtsError(f"Google synthesize failed: {exc}") from exc
        if response.status_code >= 400:
            detail = (response.text or "")[:300]
            raise TtsError(f"Google synthesize failed ({response.status_code}): {detail}")
        data = response.json()
        audio_b64 = data.get("audioContent") if isinstance(data, dict) else None
        if not isinstance(audio_b64, str) or not audio_b64.strip():
            raise TtsError("Google synthesize returned no audio")
        import base64

        try:
            content = base64.b64decode(audio_b64)
        except Exception as exc:
            raise TtsError("Google audio decode failed") from exc
        return TtsAudio(content=content, content_type="audio/mpeg")
