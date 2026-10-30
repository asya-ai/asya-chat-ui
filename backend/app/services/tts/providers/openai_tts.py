from __future__ import annotations

import httpx

from app.services.tts.types import TtsAudio, TtsError, TtsVoice

OPENAI_TTS_VOICES = [
    TtsVoice(id="alloy", name="Alloy", language="en"),
    TtsVoice(id="ash", name="Ash", language="en"),
    TtsVoice(id="ballad", name="Ballad", language="en"),
    TtsVoice(id="coral", name="Coral", language="en"),
    TtsVoice(id="echo", name="Echo", language="en"),
    TtsVoice(id="fable", name="Fable", language="en"),
    TtsVoice(id="onyx", name="Onyx", language="en"),
    TtsVoice(id="nova", name="Nova", language="en"),
    TtsVoice(id="sage", name="Sage", language="en"),
    TtsVoice(id="shimmer", name="Shimmer", language="en"),
    TtsVoice(id="verse", name="Verse", language="en"),
]


class OpenAiTtsProvider:
    id = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        self._api_key = api_key.strip()
        self._base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self._model = (model or "tts-1").strip() or "tts-1"

    def list_voices(self) -> list[TtsVoice]:
        return list(OPENAI_TTS_VOICES)

    def synthesize(self, text: str, voice_id: str) -> TtsAudio:
        try:
            with httpx.Client(timeout=90.0) as client:
                response = client.post(
                    f"{self._base_url}/audio/speech",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self._model,
                        "input": text,
                        "voice": voice_id,
                        "response_format": "mp3",
                    },
                )
        except httpx.HTTPError as exc:
            raise TtsError(f"OpenAI synthesize failed: {exc}") from exc
        if response.status_code >= 400:
            detail = (response.text or "")[:300]
            raise TtsError(f"OpenAI synthesize failed ({response.status_code}): {detail}")
        return TtsAudio(content=response.content, content_type="audio/mpeg")
