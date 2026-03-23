from __future__ import annotations

import json
import importlib
import os
import urllib.request
from typing import Iterable

from anthropic import Anthropic
from google import genai
from groq import Groq
from openai import OpenAI

from app.core.config import settings


_VERTEX_KNOWN_GEMINI_MODELS = (
    "gemini-3.1-pro-preview",
    "gemini-3.1-flash-preview",
    "gemini-3.1-flash-lite-preview",
    "gemini-3-flash-preview",
    "gemini-3-pro-preview",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite-001",
)


def _normalize_gemini_name(name: str) -> str:
    if name.startswith("publishers/google/models/"):
        return name.split("publishers/google/models/", 1)[1]
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


def _extract_vertex_project(config: dict[str, object]) -> str | None:
    value = config.get("project") or config.get("project_id") or config.get("projectId")
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _extract_vertex_location(config: dict[str, object]) -> str | None:
    value = (
        config.get("location")
        or config.get("region")
        or config.get("vertex_location")
        or config.get("vertexLocation")
    )
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _extract_vertex_credentials_info(config: dict[str, object]) -> dict[str, object] | None:
    for key in (
        "credentials",
        "credentials_json",
        "service_account",
        "service_account_json",
        "serviceAccount",
        "serviceAccountJson",
    ):
        value = config.get(key)
        if isinstance(value, dict) and value:
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except Exception:
                parsed = None
            if isinstance(parsed, dict) and parsed:
                return parsed
    if (
        config.get("type") == "service_account"
        and config.get("client_email")
        and config.get("private_key")
    ):
        return config
    return None


def _extract_vertex_credentials_file(config: dict[str, object]) -> str | None:
    value = (
        config.get("credentials_file")
        or config.get("service_account_file")
        or config.get("credentials_path")
        or config.get("google_application_credentials")
        or config.get("googleApplicationCredentials")
    )
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _extract_vertex_scopes(config: dict[str, object]) -> list[str]:
    value = (
        config.get("scopes")
        or config.get("scope")
        or config.get("auth_scopes")
        or config.get("authScopes")
    )
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if "," in text:
            return [item.strip() for item in text.split(",") if item.strip()]
        return [text]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


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


def _anthropic_models() -> tuple[list[dict[str, object]], str | None]:
    if not settings.anthropic_api_key:
        return [], "ANTHROPIC_API_KEY not set"
    try:
        client = Anthropic(
            api_key=settings.anthropic_api_key, base_url=settings.anthropic_base_url
        )
        models = client.models.list()
        items = []
        for model in models.data:
            model_id = getattr(model, "id", None) or getattr(model, "name", None)
            if not model_id:
                continue
            display = getattr(model, "name", None) or model_id
            inferred_input, inferred_output = _infer_image_support(str(model_id))
            items.append(
                {
                    "model_name": model_id,
                    "display_name": display,
                    "context_length": None,
                    "supports_image_input": inferred_input,
                    "supports_image_output": inferred_output,
                }
            )
        return items, None
    except Exception as exc:  # pragma: no cover - external API call
        return [], f"Anthropic error: {exc}"


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


def _openrouter_models() -> tuple[list[dict[str, object]], str | None]:
    if not settings.openrouter_api_key:
        return [], "OPENROUTER_API_KEY not set"
    try:
        client = OpenAI(
            api_key=settings.openrouter_api_key,
            base_url="https://openrouter.ai/api/v1",
        )
        models = client.models.list()
        items = []
        for model in models.data:
            inferred_input, inferred_output = _infer_image_support(model.id)
            items.append(
                {
                    "model_name": model.id,
                    "display_name": model.id,
                    "context_length": getattr(model, "context_length", None),
                    "supports_image_input": inferred_input,
                    "supports_image_output": inferred_output,
                }
            )
        return items, None
    except Exception as exc:  # pragma: no cover - external API call
        return [], f"OpenRouter error: {exc}"


def _vertex_models() -> tuple[list[dict[str, object]], str | None]:
    vertex_config: dict[str, object] = {}
    if settings.gemini_vertex_json:
        try:
            parsed = json.loads(settings.gemini_vertex_json)
            if isinstance(parsed, dict):
                vertex_config = parsed
        except Exception:
            vertex_config = {}

    project = _extract_vertex_project(vertex_config) or settings.google_vertex_project
    location = _extract_vertex_location(vertex_config) or settings.google_vertex_location
    credentials_info = _extract_vertex_credentials_info(vertex_config)
    credentials_file = _extract_vertex_credentials_file(vertex_config)
    scopes = _extract_vertex_scopes(vertex_config) or [
        "https://www.googleapis.com/auth/cloud-platform"
    ]
    if not project or not location:
        return [], "Vertex project/location not set (GOOGLE_VERTEX_* or GEMINI_VERTEX_JSON)"
    try:
        client_kwargs: dict[str, object] = {
            "vertexai": True,
            "project": project,
            "location": location,
        }
        if credentials_file:
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials_file
        if credentials_info:
            service_account = None
            try:
                service_account = importlib.import_module("google.oauth2.service_account")
            except Exception:
                service_account = None
            if service_account is not None:
                credentials = service_account.Credentials.from_service_account_info(
                    credentials_info
                )
                client_kwargs["credentials"] = credentials.with_scopes(scopes)
        client = genai.Client(**client_kwargs)
        items = []
        for model in client.models.list():
            name = _normalize_gemini_name(getattr(model, "name", "") or "")
            if not name:
                continue
            supported_methods = _detect_modalities(
                getattr(model, "supported_generation_methods", None)
                or getattr(model, "supportedGenerationMethods", None)
            )
            if supported_methods and "generateContent" not in supported_methods:
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
            supports_image_input = (
                "image" in input_modalities if input_modalities else None
            )
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
        # Vertex publisher listing can lag/omit some Gemini IDs despite docs availability.
        # Add known core Gemini IDs as fallback discoverability options.
        existing_names = {str(item.get("model_name", "")) for item in items}
        for model_name in _VERTEX_KNOWN_GEMINI_MODELS:
            if model_name in existing_names:
                continue
            inferred_input, inferred_output = _infer_image_support(model_name)
            items.append(
                {
                    "model_name": model_name,
                    "display_name": model_name,
                    "context_length": None,
                    "supports_image_input": inferred_input,
                    "supports_image_output": inferred_output,
                }
            )
        return items, None
    except Exception as exc:  # pragma: no cover - external API call
        return [], f"Vertex error: {exc}"


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
        ("anthropic", _anthropic_models),
        ("openrouter", _openrouter_models),
        ("vertex", _vertex_models),
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
