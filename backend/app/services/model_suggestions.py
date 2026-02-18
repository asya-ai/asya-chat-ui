from __future__ import annotations

import json
import urllib.request
from typing import Iterable

from google import genai
from groq import Groq
from openai import OpenAI

from app.core.config import settings


def _normalize_gemini_name(name: str) -> str:
    if name.startswith("models/"):
        return name.split("/", 1)[1]
    return name


def _detect_modalities(value: object) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {value}
    if isinstance(value, (list, tuple, set)):
        return {str(item) for item in value}
    return set()


def _infer_image_support(model_name: str) -> tuple[bool | None, bool | None]:
    lowered = model_name.lower()
    if "image" in lowered or "vision" in lowered:
        return True, "image" in lowered
    return None, None


def _openai_models() -> tuple[list[dict[str, object]], str | None]:
    if not settings.openai_api_key:
        return [], "OPENAI_API_KEY not set"
    try:
        client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url)
        models = client.models.list()
        items = []
        for model in models.data:
            input_modalities = _detect_modalities(
                getattr(model, "input_modalities", None)
                or getattr(model, "modalities", None)
            )
            output_modalities = _detect_modalities(
                getattr(model, "output_modalities", None)
                or getattr(model, "supported_output_modalities", None)
            )
            supports_image_input = "image" in input_modalities if input_modalities else None
            supports_image_output = (
                "image" in output_modalities if output_modalities else None
            )
            inferred_input, inferred_output = _infer_image_support(model.id)
            if supports_image_input is None:
                supports_image_input = inferred_input
            if supports_image_output is None:
                supports_image_output = inferred_output
            items.append(
                {
                    "model_name": model.id,
                    "display_name": model.id,
                    "context_length": getattr(model, "context_length", None),
                    "supports_image_input": supports_image_input,
                    "supports_image_output": supports_image_output,
                }
            )
        return items, None
    except Exception as exc:  # pragma: no cover - external API call
        return [], f"OpenAI error: {exc}"


def _groq_models() -> tuple[list[dict[str, object]], str | None]:
    if not settings.groq_api_key:
        return [], "GROQ_API_KEY not set"
    try:
        client = Groq(api_key=settings.groq_api_key, base_url=settings.groq_base_url)
        models = client.models.list()
        items = []
        for model in models.data:
            inferred_input, inferred_output = _infer_image_support(model.id)
            items.append(
                {
                    "model_name": model.id,
                    "display_name": model.id,
                    "context_length": getattr(model, "context_window", None)
                    or getattr(model, "context_length", None),
                    "supports_image_input": inferred_input,
                    "supports_image_output": inferred_output,
                }
            )
        return items, None
    except Exception as exc:  # pragma: no cover - external API call
        return [], f"Groq error: {exc}"


def _gemini_models() -> tuple[list[dict[str, object]], str | None]:
    if not settings.gemini_api_key:
        return [], "GEMINI_API_KEY not set"
    try:
        client = genai.Client(api_key=settings.gemini_api_key)
        items = []
        for model in client.models.list():
            name = _normalize_gemini_name(getattr(model, "name", "") or "")
            if not name:
                continue
            display_name = getattr(model, "display_name", None) or name
            input_modalities = _detect_modalities(
                getattr(model, "input_modalities", None)
                or getattr(model, "supported_input_modalities", None)
            )
            output_modalities = _detect_modalities(
                getattr(model, "output_modalities", None)
                or getattr(model, "supported_output_modalities", None)
            )
            supports_image_input = "image" in input_modalities if input_modalities else None
            supports_image_output = (
                "image" in output_modalities if output_modalities else None
            )
            inferred_input, inferred_output = _infer_image_support(name)
            if supports_image_input is None:
                supports_image_input = inferred_input
            if supports_image_output is None:
                supports_image_output = inferred_output
            items.append(
                {
                    "model_name": name,
                    "display_name": display_name,
                    "context_length": getattr(model, "input_token_limit", None),
                    "supports_image_input": supports_image_input,
                    "supports_image_output": supports_image_output,
                }
            )
        return items, None
    except Exception as exc:  # pragma: no cover - external API call
        return [], f"Gemini error: {exc}"


def _azure_models() -> tuple[list[dict[str, object]], str | None]:
    if not settings.azure_openai_api_key or not settings.azure_openai_endpoint:
        return [], "AZURE_OPENAI_API_KEY or AZURE_OPENAI_ENDPOINT not set"
    try:
        endpoint = settings.azure_openai_endpoint.rstrip("/")
        api_version = settings.azure_openai_api_version
        url = f"{endpoint}/openai/deployments?api-version={api_version}"
        request = urllib.request.Request(
            url,
            headers={"api-key": settings.azure_openai_api_key},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        data = payload.get("data", [])
        items = []
        for item in data:
            name = item.get("id") or item.get("name")
            if not name:
                continue
            inferred_input, inferred_output = _infer_image_support(name)
            items.append(
                {
                    "model_name": name,
                    "display_name": name,
                    "context_length": None,
                    "supports_image_input": inferred_input,
                    "supports_image_output": inferred_output,
                }
            )
        return items, None
    except Exception as exc:  # pragma: no cover - external API call
        return [], f"Azure error: {exc}"


def _dedupe(items: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    seen = set()
    results = []
    for item in items:
        key = item.get("model_name", "")
        if not key or key in seen:
            continue
        seen.add(key)
        results.append(item)
    return results


def get_model_suggestions() -> list[dict[str, object]]:
    providers = [
        ("openai", _openai_models),
        ("azure", _azure_models),
        ("gemini", _gemini_models),
        ("groq", _groq_models),
    ]
    results: list[dict[str, object]] = []
    for provider, fn in providers:
        models, error = fn()
        results.append(
            {
                "provider": provider,
                "models": _dedupe(models),
                "error": error,
            }
        )
    return results
