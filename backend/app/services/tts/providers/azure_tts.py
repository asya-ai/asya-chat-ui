from __future__ import annotations

import re
from xml.sax.saxutils import escape

import httpx

from app.services.tts.types import TtsAudio, TtsError, TtsVoice

_REGION_RE = re.compile(r"^[a-z0-9]+(?:[a-z0-9-]*[a-z0-9])?$", re.IGNORECASE)


class AzureTtsProvider:
    id = "azure"

    def __init__(
        self,
        *,
        api_key: str,
        region: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self._api_key = api_key.strip()
        base = (base_url or "").strip().rstrip("/")
        region_value = (region or "").strip()
        if base:
            self._endpoint = base
        elif region_value and _REGION_RE.match(region_value):
            self._endpoint = f"https://{region_value}.tts.speech.microsoft.com"
        else:
            raise TtsError("Azure Speech requires region or base_url")

    def _headers(self, *, output_format: bool = False) -> dict[str, str]:
        headers = {"Ocp-Apim-Subscription-Key": self._api_key}
        if output_format:
            headers["Content-Type"] = "application/ssml+xml"
            headers["X-Microsoft-OutputFormat"] = "audio-16khz-128kbitrate-mono-mp3"
        return headers

    def list_voices(self) -> list[TtsVoice]:
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.get(
                    f"{self._endpoint}/cognitiveservices/voices/list",
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            raise TtsError(f"Azure voices failed: {exc}") from exc
        if response.status_code >= 400:
            raise TtsError(f"Azure voices failed ({response.status_code})")
        data = response.json()
        if not isinstance(data, list):
            return []
        voices: list[TtsVoice] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            voice_id = str(item.get("ShortName") or item.get("Name") or "").strip()
            if not voice_id:
                continue
            display = str(
                item.get("DisplayName") or item.get("LocalName") or voice_id
            ).strip()
            locale = item.get("Locale")
            voices.append(
                TtsVoice(
                    id=voice_id,
                    name=f"{display} ({locale})" if locale else display,
                    language=str(locale) if locale else None,
                )
            )
        return voices

    def synthesize(self, text: str, voice_id: str) -> TtsAudio:
        locale = "en-US"
        if "-" in voice_id:
            parts = voice_id.split("-")
            if len(parts) >= 2:
                locale = f"{parts[0]}-{parts[1]}"
        safe_text = escape(text)
        safe_voice = escape(voice_id, {'"': "&quot;", "'": "&apos;"})
        ssml = (
            f'<speak version="1.0" xml:lang="{locale}">'
            f'<voice name="{safe_voice}">{safe_text}</voice></speak>'
        )
        try:
            with httpx.Client(timeout=90.0) as client:
                response = client.post(
                    f"{self._endpoint}/cognitiveservices/v1",
                    headers=self._headers(output_format=True),
                    content=ssml.encode("utf-8"),
                )
        except httpx.HTTPError as exc:
            raise TtsError(f"Azure synthesize failed: {exc}") from exc
        if response.status_code >= 400:
            detail = (response.text or "")[:300]
            raise TtsError(f"Azure synthesize failed ({response.status_code}): {detail}")
        return TtsAudio(content=response.content, content_type="audio/mpeg")
