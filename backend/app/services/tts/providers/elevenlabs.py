from __future__ import annotations

from typing import Any

import httpx

from app.services.tts.types import TtsAudio, TtsError, TtsVoice


class ElevenLabsTtsProvider:
    id = "elevenlabs"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str | None = None,
        model_id: str | None = None,
    ) -> None:
        self._api_key = api_key.strip()
        self._base_url = (base_url or "https://api.elevenlabs.io").rstrip("/")
        self._model_id = (model_id or "eleven_multilingual_v2").strip() or "eleven_multilingual_v2"

    def _headers(self, *, accept: str | None = None) -> dict[str, str]:
        headers = {"xi-api-key": self._api_key}
        if accept:
            headers["Accept"] = accept
        return headers

    def list_voices(self) -> list[TtsVoice]:
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.get(
                    f"{self._base_url}/v1/voices",
                    headers=self._headers(accept="application/json"),
                )
        except httpx.HTTPError as exc:
            raise TtsError(f"ElevenLabs voices failed: {exc}") from exc
        if response.status_code >= 400:
            raise TtsError(f"ElevenLabs voices failed ({response.status_code})")
        data = response.json()
        items = data.get("voices") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []
        voices: list[TtsVoice] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            voice_id = str(item.get("voice_id") or item.get("id") or "").strip()
            if not voice_id:
                continue
            name = str(item.get("name") or voice_id).strip()
            labels = item.get("labels") if isinstance(item.get("labels"), dict) else {}
            language = labels.get("language") if isinstance(labels, dict) else None
            voices.append(
                TtsVoice(
                    id=voice_id,
                    name=name,
                    language=str(language) if language else None,
                )
            )
        return voices

    def synthesize(self, text: str, voice_id: str) -> TtsAudio:
        payload: dict[str, Any] = {
            "text": text,
            "model_id": self._model_id,
        }
        try:
            with httpx.Client(timeout=90.0) as client:
                response = client.post(
                    f"{self._base_url}/v1/text-to-speech/{voice_id}",
                    headers=self._headers(accept="audio/mpeg"),
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise TtsError(f"ElevenLabs synthesize failed: {exc}") from exc
        if response.status_code >= 400:
            detail = (response.text or "")[:300]
            raise TtsError(f"ElevenLabs synthesize failed ({response.status_code}): {detail}")
        return TtsAudio(content=response.content, content_type="audio/mpeg")
