"""Code-defined catalog integrations (PitchPatterns + Microsoft 365)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.services.integrations.graph_mcp_embed import (
    EMBEDDED_GRAPH_MCP_URL,
    embedded_graph_mcp_url,
)

ConnectKind = Literal["bearer_api_key", "oauth"]

_GRAPH = "https://graph.microsoft.com"
# User.Read is required by people_get_my_profile and most Graph /me calls.
_MS_BASE_SCOPES = (
    "openid",
    "profile",
    "offline_access",
    f"{_GRAPH}/User.Read",
)
# Embedded Graph MCP (mounted on the ChatUI API). See graph_mcp_embed.py.
# Matches settings default; resolve_catalog_mcp_url reads live GRAPH_MCP_EMBED_URL.
DEFAULT_GRAPH_MCP_URL = EMBEDDED_GRAPH_MCP_URL

# Narrow tools advertised by the shared Graph MCP per catalog card.
_GRAPH_MCP_TOOLSETS: dict[str, str] = {
    "outlook": "mail",
    "outlook_calendar": "calendar",
    "outlook_contacts": "people",
    "onedrive": "files",
    "sharepoint": "files",
    "teams": "chat",
    "todo": "tasks",
}


@dataclass(frozen=True)
class AdminConfigField:
    key: str
    label: str
    required: bool = True
    secret: bool = False
    placeholder: str | None = None
    help_text: str | None = None
    default: str | None = None


@dataclass(frozen=True)
class CatalogMcpBinding:
    transport: Literal["http", "sse", "stdio"]
    # For http/sse: take URL from admin config key
    url_from: str | None = None
    # For stdio (future): command/args templates
    command: str | None = None
    args: tuple[str, ...] = ()


@dataclass(frozen=True)
class CatalogIntegration:
    catalog_id: str
    name: str
    description: str
    connect_kind: ConnectKind
    admin_config_fields: tuple[AdminConfigField, ...]
    mcp: CatalogMcpBinding
    oauth_provider_id: str | None = None
    oauth_scopes: tuple[str, ...] = ()
    user_instructions: str | None = None
    icon_key: str | None = None
    mcp_slug: str | None = None  # defaults to catalog-{id}

    def slug(self) -> str:
        return self.mcp_slug or f"catalog-{self.catalog_id}"


def _ms_scopes(*graph_scopes: str) -> tuple[str, ...]:
    return (*_MS_BASE_SCOPES, *(f"{_GRAPH}/{scope}" for scope in graph_scopes))


def graph_mcp_request_headers(catalog_id: str) -> dict[str, str]:
    """Extra HTTP headers for the shared Graph MCP (toolset + write gate)."""
    headers = {"X-Write-Scope": "true"}
    toolset = _GRAPH_MCP_TOOLSETS.get(catalog_id)
    if toolset:
        headers["X-Toolsets"] = toolset
    return headers


def is_microsoft_catalog(catalog_id: str | None) -> bool:
    return bool(catalog_id) and catalog_id in _GRAPH_MCP_TOOLSETS


def resolve_catalog_mcp_url(
    entry: CatalogIntegration, config: dict[str, Any]
) -> str | None:
    """Effective MCP URL for a catalog entry (embedded for Microsoft apps)."""
    if is_microsoft_catalog(entry.catalog_id):
        return embedded_graph_mcp_url()
    if not entry.mcp.url_from:
        return None
    url = str(config.get(entry.mcp.url_from) or "").strip()
    return url or None


PITCHPATTERNS = CatalogIntegration(
    catalog_id="pitchpatterns",
    name="PitchPatterns",
    description="PitchPatterns MCP",
    connect_kind="bearer_api_key",
    admin_config_fields=(
        AdminConfigField(
            key="mcp_url",
            label="MCP URL",
            placeholder="http://host.docker.internal:9020/mcp",
            default="http://host.docker.internal:9020/mcp",
        ),
        AdminConfigField(
            key="allowed_embed_origins",
            label="Embed origins",
            required=False,
            placeholder="*.pitchpatterns.com",
            help_text=None,
        ),
    ),
    mcp=CatalogMcpBinding(transport="http", url_from="mcp_url"),
    user_instructions="API key",
    icon_key="pitchpatterns",
)

_MS_MCP = CatalogMcpBinding(transport="http", url_from=None)

OUTLOOK = CatalogIntegration(
    catalog_id="outlook",
    name="Outlook Mail",
    description="Read and send email",
    connect_kind="oauth",
    oauth_provider_id="microsoft",
    oauth_scopes=_ms_scopes("Mail.Read", "Mail.ReadWrite", "Mail.Send"),
    admin_config_fields=(),
    mcp=_MS_MCP,
    user_instructions="Sign in with Microsoft",
    icon_key="outlook",
)

OUTLOOK_CALENDAR = CatalogIntegration(
    catalog_id="outlook_calendar",
    name="Outlook Calendar",
    description="Meetings and calendar",
    connect_kind="oauth",
    oauth_provider_id="microsoft",
    oauth_scopes=_ms_scopes("Calendars.ReadWrite"),
    admin_config_fields=(),
    mcp=_MS_MCP,
    user_instructions="Sign in with Microsoft",
    icon_key="outlook_calendar",
)

OUTLOOK_CONTACTS = CatalogIntegration(
    catalog_id="outlook_contacts",
    name="Contacts",
    description="People and contacts",
    connect_kind="oauth",
    oauth_provider_id="microsoft",
    oauth_scopes=_ms_scopes("Contacts.Read", "People.Read"),
    admin_config_fields=(),
    mcp=_MS_MCP,
    user_instructions="Sign in with Microsoft",
    icon_key="outlook_contacts",
)

ONEDRIVE = CatalogIntegration(
    catalog_id="onedrive",
    name="OneDrive",
    description="Personal files",
    connect_kind="oauth",
    oauth_provider_id="microsoft",
    oauth_scopes=_ms_scopes("Files.ReadWrite"),
    admin_config_fields=(),
    mcp=_MS_MCP,
    user_instructions="Sign in with Microsoft",
    icon_key="onedrive",
)

SHAREPOINT = CatalogIntegration(
    catalog_id="sharepoint",
    name="SharePoint",
    description="Sites and libraries",
    connect_kind="oauth",
    oauth_provider_id="microsoft",
    oauth_scopes=_ms_scopes("Sites.Read.All", "Files.ReadWrite.All"),
    admin_config_fields=(),
    mcp=_MS_MCP,
    user_instructions="Sign in with Microsoft",
    icon_key="sharepoint",
)

TEAMS = CatalogIntegration(
    catalog_id="teams",
    name="Microsoft Teams",
    description="Work/school chats and channels",
    connect_kind="oauth",
    oauth_provider_id="microsoft",
    oauth_scopes=_ms_scopes(
        "Chat.Read",
        "Chat.ReadWrite",
        "ChannelMessage.Read.All",
        "Team.ReadBasic.All",
    ),
    admin_config_fields=(),
    mcp=_MS_MCP,
    user_instructions="Work or school Microsoft 365 account",
    icon_key="teams",
)

TODO = CatalogIntegration(
    catalog_id="todo",
    name="Microsoft To Do",
    description="Tasks and lists",
    connect_kind="oauth",
    oauth_provider_id="microsoft",
    oauth_scopes=_ms_scopes("Tasks.ReadWrite"),
    admin_config_fields=(),
    mcp=_MS_MCP,
    user_instructions="Sign in with Microsoft",
    icon_key="todo",
)


_CATALOG: dict[str, CatalogIntegration] = {
    entry.catalog_id: entry
    for entry in (
        PITCHPATTERNS,
        OUTLOOK,
        OUTLOOK_CALENDAR,
        OUTLOOK_CONTACTS,
        ONEDRIVE,
        SHAREPOINT,
        TEAMS,
        TODO,
    )
}


def list_catalog() -> list[CatalogIntegration]:
    return list(_CATALOG.values())


def get_catalog(catalog_id: str) -> CatalogIntegration | None:
    return _CATALOG.get(catalog_id)


def catalog_schema(entry: CatalogIntegration) -> dict[str, Any]:
    return {
        "catalog_id": entry.catalog_id,
        "name": entry.name,
        "description": entry.description,
        "connect_kind": entry.connect_kind,
        "oauth_provider_id": entry.oauth_provider_id,
        "oauth_scopes": list(entry.oauth_scopes),
        "user_instructions": entry.user_instructions,
        "icon_key": entry.icon_key,
        "mcp_slug": entry.slug(),
        "admin_config_fields": [
            {
                "key": f.key,
                "label": f.label,
                "required": f.required,
                "secret": f.secret,
                "placeholder": f.placeholder,
                "help_text": f.help_text,
                "default": f.default,
            }
            for f in entry.admin_config_fields
        ],
    }
