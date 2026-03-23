import json
import logging
import importlib
import os

from google import genai

from app.core.config import settings
from app.services.providers.gemini_provider import GeminiProvider


def _parse_vertex_json(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _extract_credentials_info(vertex_config: dict) -> dict | None:
    for key in (
        "credentials",
        "credentials_json",
        "service_account",
        "service_account_json",
        "serviceAccount",
        "serviceAccountJson",
    ):
        value = vertex_config.get(key)
        if isinstance(value, dict) and value:
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except Exception:
                parsed = None
            if isinstance(parsed, dict) and parsed:
                return parsed
    # Also support passing the raw service-account object directly as GEMINI_VERTEX_JSON/config_json.
    if (
        isinstance(vertex_config.get("type"), str)
        and vertex_config.get("type") == "service_account"
        and vertex_config.get("client_email")
        and vertex_config.get("private_key")
    ):
        return vertex_config
    return None


def _extract_project(vertex_config: dict) -> str | None:
    value = (
        vertex_config.get("project")
        or vertex_config.get("project_id")
        or vertex_config.get("projectId")
    )
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _extract_location(vertex_config: dict) -> str | None:
    value = (
        vertex_config.get("location")
        or vertex_config.get("region")
        or vertex_config.get("vertex_location")
        or vertex_config.get("vertexLocation")
    )
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _extract_credentials_file(vertex_config: dict) -> str | None:
    value = (
        vertex_config.get("credentials_file")
        or vertex_config.get("service_account_file")
        or vertex_config.get("credentials_path")
        or vertex_config.get("google_application_credentials")
        or vertex_config.get("googleApplicationCredentials")
    )
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _extract_scopes(vertex_config: dict) -> list[str]:
    value = (
        vertex_config.get("scopes")
        or vertex_config.get("scope")
        or vertex_config.get("auth_scopes")
        or vertex_config.get("authScopes")
    )
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if "," in text:
            return [item.strip() for item in text.split(",") if item.strip()]
        return [text]
    if isinstance(value, (list, tuple, set)):
        scopes = [str(item).strip() for item in value if str(item).strip()]
        return scopes
    return []


class VertexProvider(GeminiProvider):
    def __init__(
        self,
        *,
        api_key: str | None = None,
        prompt_cache_key: str | None = None,
        config: dict | None = None,
    ) -> None:
        self.logger = logging.getLogger(__name__)
        self.prompt_cache_key = prompt_cache_key

        env_config = _parse_vertex_json(settings.gemini_vertex_json)
        merged_config: dict = {}
        if env_config:
            merged_config.update(env_config)
        if config:
            merged_config.update(config)

        # Determine configuration (priority: org config > GEMINI_VERTEX_JSON > dedicated env vars)
        project = (
            _extract_project(merged_config)
            or settings.google_vertex_project
            or _extract_project(env_config)
        )
        location = (
            _extract_location(merged_config)
            or settings.google_vertex_location
            or _extract_location(env_config)
        )
        credentials_info = _extract_credentials_info(merged_config)
        credentials_file = _extract_credentials_file(merged_config)
        scopes = _extract_scopes(merged_config) or [
            "https://www.googleapis.com/auth/cloud-platform"
        ]
        client_kwargs: dict = {"vertexai": True}
        if project:
            client_kwargs["project"] = project
        if location:
            client_kwargs["location"] = location

        auth_mode = "adc"

        if credentials_file:
            # Let google-auth ADC loader pick up the file path.
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials_file
            auth_mode = "credentials_file"

        service_account = None
        if credentials_info:
            try:
                service_account = importlib.import_module("google.oauth2.service_account")
            except Exception:
                service_account = None

        if credentials_info and service_account is not None:
            try:
                credentials = service_account.Credentials.from_service_account_info(
                    credentials_info
                )
                client_kwargs["credentials"] = credentials.with_scopes(scopes)
                auth_mode = "service_account_json"
            except Exception as exc:
                self.logger.warning(
                    "Failed to parse Vertex service account credentials JSON: %s",
                    exc,
                )
        elif credentials_info and service_account is None:
            self.logger.warning(
                "Vertex credentials provided but google.oauth2.service_account is unavailable"
            )

        if project and location:
            self.logger.info(
                "Initializing Vertex AI client for project %s, location %s, auth_mode=%s",
                project,
                location,
                auth_mode,
            )
            self.client = genai.Client(**client_kwargs)
        else:
            self.logger.warning(
                "Vertex AI project/location not configured (checked config_json, GEMINI_VERTEX_JSON, and GOOGLE_VERTEX_*), falling back to default client init (might fail)"
            )
            self.client = genai.Client(**client_kwargs)
