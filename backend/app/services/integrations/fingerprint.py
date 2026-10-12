"""Credential fingerprints for embed session lookup."""

from __future__ import annotations

import hashlib
import hmac

from app.core.config import settings


def credential_fingerprint(raw: str) -> str:
    value = raw.strip().encode("utf-8")
    secret = (settings.secret_key or "chatui").encode("utf-8")
    return hmac.new(secret, value, hashlib.sha256).hexdigest()
