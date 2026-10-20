"""Code-defined OAuth providers for catalog integrations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode


@dataclass(frozen=True)
class ProviderField:
    key: str
    label: str
    secret: bool = False
    required: bool = True
    readonly: bool = False
    placeholder: str | None = None
    help_text: str | None = None


@dataclass(frozen=True)
class OAuthProviderDef:
    provider_id: str
    name: str
    description: str
    fields: tuple[ProviderField, ...]
    docs_url: str | None = None
    default_scopes: tuple[str, ...] = ()

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        missing: list[str] = []
        for item in self.fields:
            if not item.required:
                continue
            value = config.get(item.key)
            if value is None or (isinstance(value, str) and not value.strip()):
                # Secrets may already be set server-side; callers pass *_set flags.
                if item.secret and config.get(f"{item.key}_set"):
                    continue
                missing.append(item.key)
        return missing

    def authorize_url(self, config: dict[str, Any], *, state: str, scopes: list[str]) -> str:
        raise NotImplementedError

    def token_url(self, config: dict[str, Any]) -> str:
        raise NotImplementedError


@dataclass(frozen=True)
class MicrosoftProvider(OAuthProviderDef):
    provider_id: str = "microsoft"
    name: str = "Microsoft"
    description: str = "Entra ID"
    docs_url: str | None = "https://learn.microsoft.com/entra/identity-platform/"
    default_scopes: tuple[str, ...] = ("openid", "profile", "offline_access")
    fields: tuple[ProviderField, ...] = (
        ProviderField("client_id", "Application (client) ID"),
        ProviderField("client_secret", "Client secret", secret=True),
        ProviderField(
            "tenant_id",
            "Directory (tenant) ID",
            required=False,
            placeholder="common",
        ),
        ProviderField("redirect_uri", "Redirect URI", readonly=True),
    )

    def authorize_url(self, config: dict[str, Any], *, state: str, scopes: list[str]) -> str:
        tenant = str(config.get("tenant_id") or "common").strip() or "common"
        params = {
            "client_id": str(config.get("client_id") or "").strip(),
            "response_type": "code",
            "redirect_uri": str(config.get("redirect_uri") or "").strip(),
            "response_mode": "query",
            "scope": " ".join(scopes),
            "state": state,
            # Ensure refresh_token is issued when offline_access is requested.
            "prompt": "consent",
        }
        return f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize?{urlencode(params)}"

    def token_url(self, config: dict[str, Any]) -> str:
        tenant = str(config.get("tenant_id") or "common").strip() or "common"
        return f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"


@dataclass(frozen=True)
class GoogleProvider(OAuthProviderDef):
    provider_id: str = "google"
    name: str = "Google"
    description: str = "Google Cloud OAuth"
    docs_url: str | None = "https://console.cloud.google.com/apis/credentials"
    default_scopes: tuple[str, ...] = ("openid", "email", "profile")
    fields: tuple[ProviderField, ...] = (
        ProviderField("client_id", "Client ID"),
        ProviderField("client_secret", "Client secret", secret=True),
        ProviderField("redirect_uri", "Redirect URI", readonly=True),
    )

    def authorize_url(self, config: dict[str, Any], *, state: str, scopes: list[str]) -> str:
        params = {
            "client_id": str(config.get("client_id") or "").strip(),
            "response_type": "code",
            "redirect_uri": str(config.get("redirect_uri") or "").strip(),
            "scope": " ".join(scopes),
            "state": state,
            "access_type": "offline",
            "prompt": "consent",
        }
        return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"

    def token_url(self, config: dict[str, Any]) -> str:
        return "https://oauth2.googleapis.com/token"


_PROVIDERS: dict[str, OAuthProviderDef] = {
    "microsoft": MicrosoftProvider(),
    "google": GoogleProvider(),
}


def list_oauth_providers() -> list[OAuthProviderDef]:
    return list(_PROVIDERS.values())


def get_oauth_provider(provider_id: str) -> OAuthProviderDef | None:
    return _PROVIDERS.get(provider_id)


def provider_schema(provider: OAuthProviderDef) -> dict[str, Any]:
    return {
        "provider_id": provider.provider_id,
        "name": provider.name,
        "description": provider.description,
        "docs_url": provider.docs_url,
        "default_scopes": list(provider.default_scopes),
        "fields": [
            {
                "key": f.key,
                "label": f.label,
                "secret": f.secret,
                "required": f.required,
                "readonly": f.readonly,
                "placeholder": f.placeholder,
                "help_text": f.help_text,
            }
            for f in provider.fields
        ],
    }
