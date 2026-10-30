from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


TTS_PROVIDERS = ("asya", "openai", "google", "elevenlabs", "azure", "browser")
CLOUD_TTS_PROVIDERS = ("asya", "openai", "google", "elevenlabs", "azure")
DEFAULT_ASYA_BASE_URL = "https://ttsapi.asya.ai"
MAX_SYNTHESIZE_CHARS = 4000


@dataclass(frozen=True)
class TtsVoice:
    id: str
    name: str
    language: str | None = None


@dataclass(frozen=True)
class TtsAudio:
    content: bytes
    content_type: str


class TtsProvider(Protocol):
    id: str

    def list_voices(self) -> list[TtsVoice]: ...

    def synthesize(self, text: str, voice_id: str) -> TtsAudio: ...


class TtsError(Exception):
    """Provider or config failure surfaced to the API layer."""
