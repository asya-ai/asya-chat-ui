import base64
import struct

import pytest

from app.services.tts.providers.asya import _asya_audio_from_json
from app.services.tts.types import TtsError


def test_asya_audio_from_json_wav32_to_browser_wav() -> None:
    pcm = struct.pack("<ff", 0.0, 0.25)
    data = {
        "data": base64.b64encode(pcm).decode("ascii"),
        "metadata": {"audio_format": "wav32", "sample_rate": 24000, "channels": 1},
    }
    audio = _asya_audio_from_json(data)
    assert audio.content[:4] == b"RIFF"
    assert audio.content_type == "audio/wav"


def test_asya_audio_from_json_mp3_passthrough() -> None:
    raw = b"ID3fake"
    data = {
        "data": base64.b64encode(raw).decode("ascii"),
        "metadata": {"audio_format": "mp3"},
    }
    audio = _asya_audio_from_json(data)
    assert audio.content == raw
    assert audio.content_type == "audio/mpeg"


def test_asya_audio_from_json_api_error() -> None:
    with pytest.raises(TtsError, match="Invalid token"):
        _asya_audio_from_json(
            {
                "success": False,
                "error": {"code": 401, "message": "Invalid token"},
            }
        )
