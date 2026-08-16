from app.services.model_suggestions import parse_openrouter_endpoints


def test_parse_openrouter_endpoints_uses_tags() -> None:
    payload = {
        "data": {
            "id": "google/gemini-2.5-pro",
            "endpoints": [
                {
                    "name": "Google | google/gemini-2.5-pro",
                    "tag": "google-vertex/global",
                    "provider_name": "Google",
                    "quantization": "unknown",
                },
                {
                    "name": "Google | google/gemini-2.5-pro",
                    "tag": "google-vertex/eu",
                    "provider_name": "Google",
                    "quantization": "unknown",
                },
                {
                    "name": "Google AI Studio | google/gemini-2.5-pro",
                    "tag": "google-ai-studio",
                    "provider_name": "Google AI Studio",
                    "quantization": "fp8",
                },
                {
                    "name": "duplicate",
                    "tag": "google-vertex/eu",
                    "provider_name": "Google",
                },
                {"name": "missing tag", "provider_name": "Nope"},
            ],
        }
    }

    items = parse_openrouter_endpoints(payload)

    assert [item["tag"] for item in items] == [
        "google-vertex/global",
        "google-vertex/eu",
        "google-ai-studio",
    ]
    eu = next(item for item in items if item["tag"] == "google-vertex/eu")
    assert eu["provider_name"] == "Google"
    assert eu["quantization"] is None
    studio = next(item for item in items if item["tag"] == "google-ai-studio")
    assert studio["quantization"] == "fp8"


def test_parse_openrouter_endpoints_empty_payload() -> None:
    assert parse_openrouter_endpoints({}) == []
    assert parse_openrouter_endpoints({"data": {}}) == []
    assert parse_openrouter_endpoints(None) == []
