from __future__ import annotations

import importlib
import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

_VERTEX_PUBLISHER_PREFIXES = (
    "publishers/anthropic/models/",
    "publishers/google/models/",
)


def _parse_vertex_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def extract_project(vertex_config: dict[str, Any]) -> str | None:
    value = (
        vertex_config.get("project")
        or vertex_config.get("project_id")
        or vertex_config.get("projectId")
    )
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def extract_location(vertex_config: dict[str, Any]) -> str | None:
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


def extract_credentials_info(vertex_config: dict[str, Any]) -> dict[str, Any] | None:
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
    if (
        isinstance(vertex_config.get("type"), str)
        and vertex_config.get("type") == "service_account"
        and vertex_config.get("client_email")
        and vertex_config.get("private_key")
    ):
        return vertex_config
    return None


def extract_credentials_file(vertex_config: dict[str, Any]) -> str | None:
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


def extract_scopes(vertex_config: dict[str, Any]) -> list[str]:
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
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def normalize_vertex_model_name(model_name: str) -> str:
    value = model_name.strip()
    for prefix in _VERTEX_PUBLISHER_PREFIXES:
        if value.startswith(prefix):
            value = value.split(prefix, 1)[1]
            break
    if value.startswith("models/"):
        value = value.split("models/", 1)[1]
    return value


def is_vertex_claude_model(model_name: str) -> bool:
    return normalize_vertex_model_name(model_name).lower().startswith("claude")


@dataclass
class VertexRuntimeConfig:
    project: str | None
    location: str
    credentials: Any | None
    auth_mode: str


def merge_vertex_config(config: dict[str, Any] | None) -> dict[str, Any]:
    env_config = _parse_vertex_json(settings.gemini_vertex_json)
    merged: dict[str, Any] = {}
    if env_config:
        merged.update(env_config)
    if config:
        merged.update(config)
    return merged


def resolve_vertex_runtime_config(
    config: dict[str, Any] | None = None,
) -> VertexRuntimeConfig:
    merged = merge_vertex_config(config)
    env_config = _parse_vertex_json(settings.gemini_vertex_json)
    project = (
        extract_project(merged)
        or settings.google_vertex_project
        or extract_project(env_config)
    )
    location = (
        extract_location(merged)
        or (settings.google_vertex_location or "").strip()
        or extract_location(env_config)
        or "global"
    )
    credentials_info = extract_credentials_info(merged)
    credentials_file = extract_credentials_file(merged)
    scopes = extract_scopes(merged) or [
        "https://www.googleapis.com/auth/cloud-platform"
    ]
    credentials = None
    auth_mode = "adc"

    if credentials_file:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials_file
        auth_mode = "credentials_file"

    if credentials_info:
        try:
            service_account = importlib.import_module("google.oauth2.service_account")
        except Exception:
            service_account = None
        if service_account is not None:
            try:
                credentials = service_account.Credentials.from_service_account_info(
                    credentials_info
                )
                credentials = credentials.with_scopes(scopes)
                auth_mode = "service_account_json"
            except Exception as exc:
                logger.warning(
                    "Failed to parse Vertex service account credentials JSON: %s",
                    exc,
                )

    return VertexRuntimeConfig(
        project=project,
        location=location,
        credentials=credentials,
        auth_mode=auth_mode,
    )