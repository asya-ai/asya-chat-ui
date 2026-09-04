from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from app.core.secret_crypto import decrypt_secret, encrypt_secret

_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def validate_slug(slug: str) -> str:
    value = slug.strip().lower()
    if not value:
        raise ValueError("slug is required")
    if "__" in value:
        raise ValueError("slug must not contain '__'")
    if not _SLUG_PATTERN.match(value):
        raise ValueError("slug must be lowercase alphanumeric with hyphens")
    return value


def encrypt_auth_config(data: dict[str, Any] | None) -> str | None:
    if not data:
        return None
    return encrypt_secret(json.dumps(data, separators=(",", ":"), sort_keys=True))


def decrypt_auth_config(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    raw = decrypt_secret(value)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def merge_auth_config(existing: str | None, updates: dict[str, Any]) -> str | None:
    current = decrypt_auth_config(existing)
    for key, val in updates.items():
        if val is None:
            current.pop(key, None)
        elif val == "":
            current.pop(key, None)
        else:
            current[key] = val
    return encrypt_auth_config(current) if current else None


def auth_config_token_set(value: str | None, *, keys: tuple[str, ...] = ("token",)) -> bool:
    data = decrypt_auth_config(value)
    for key in keys:
        token = data.get(key)
        if isinstance(token, str) and token.strip():
            return True
    return False


def auth_fingerprint(auth_type: str, auth_config: str | None, connection_config: str | None = None) -> str:
    parts = [auth_type or "none"]
    for blob in (auth_config, connection_config):
        if blob:
            parts.append(hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16])
    return ":".join(parts)


def format_api_token_header(token: str, header_name: str, header_format: str) -> dict[str, str]:
    name = (header_name or "Authorization").strip() or "Authorization"
    fmt = header_format or "Bearer {token}"
    return {name: fmt.replace("{token}", token)}


def headers_from_shared_auth(auth_type: str, auth_config: str | None) -> dict[str, str]:
    if auth_type not in {"bearer", "api_token"}:
        return {}
    data = decrypt_auth_config(auth_config)
    token = str(data.get("token") or "").strip()
    if not token:
        return {}
    if auth_type == "bearer":
        return {"Authorization": f"Bearer {token}"}
    header_name = str(data.get("header_name") or "Authorization")
    header_format = str(data.get("header_format") or "Bearer {token}")
    return format_api_token_header(token, header_name, header_format)


def headers_from_user_connection(auth_config: str | None) -> dict[str, str]:
    data = decrypt_auth_config(auth_config)
    method = str(data.get("method") or "").strip()
    if method == "bearer":
        token = str(data.get("token") or "").strip()
        if not token:
            return {}
        return {"Authorization": f"Bearer {token}"}
    if method == "api_token":
        token = str(data.get("token") or "").strip()
        if not token:
            return {}
        header_name = str(data.get("header_name") or "Authorization")
        header_format = str(data.get("header_format") or "Bearer {token}")
        return format_api_token_header(token, header_name, header_format)
    return {}


def validate_server_auth_payload(auth_type: str, auth_config: dict[str, Any] | None) -> None:
    payload = auth_config or {}
    if auth_type == "none":
        return
    if auth_type == "bearer":
        if not str(payload.get("token") or "").strip():
            raise ValueError("bearer auth requires token")
        return
    if auth_type == "api_token":
        if not str(payload.get("token") or "").strip():
            raise ValueError("api_token auth requires token")
        return
    if auth_type == "user_provided":
        method = str(payload.get("user_auth_method") or "").strip()
        if method not in {"bearer", "api_token"}:
            raise ValueError("user_provided requires user_auth_method bearer or api_token")
        return
    raise ValueError(f"unsupported auth_type: {auth_type}")
