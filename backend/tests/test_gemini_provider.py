from __future__ import annotations

from app.services.providers.gemini_provider import GeminiProvider


def _provider_without_init() -> GeminiProvider:
    # Avoid constructing real SDK client in unit tests.
    return GeminiProvider.__new__(GeminiProvider)


def test_extract_thought_signature_from_function_call_mapping() -> None:
    provider = _provider_without_init()
    part = {"functionCall": {"name": "web_search"}}
    function_call = {
        "name": "web_search",
        "thoughtSignature": "sig-from-function-call",
    }

    signature = provider._extract_thought_signature(part, function_call)

    assert signature == "sig-from-function-call"


def test_extract_thought_signature_from_part_nested_mapping() -> None:
    provider = _provider_without_init()
    part = {
        "function_call": {
            "name": "web_search",
            "thought_signature": "sig-from-part",
        }
    }
    function_call = {"name": "web_search"}

    signature = provider._extract_thought_signature(part, function_call)

    assert signature == "sig-from-part"


def test_extract_thought_signature_bytes_are_base64_encoded() -> None:
    provider = _provider_without_init()
    part = {"function_call": {"name": "web_search"}}
    function_call = {"name": "web_search", "thought_signature": b"\x01\x02"}

    signature = provider._extract_thought_signature(part, function_call)

    assert signature == "AQI="


def test_build_function_call_part_keeps_signature_on_part_only() -> None:
    part = GeminiProvider._build_function_call_part(
        {
            "id": "web_search",
            "name": "web_search",
            "arguments": {"query": "jetson", "thought_signature": "drop-me"},
            "thought_signature": "sig-123",
        }
    )

    assert part["thought_signature"] == "sig-123"
    assert part["function_call"]["id"] == "web_search"
    assert part["function_call"]["name"] == "web_search"
    assert part["function_call"]["args"] == {"query": "jetson"}
    assert "thought_signature" not in part["function_call"]
