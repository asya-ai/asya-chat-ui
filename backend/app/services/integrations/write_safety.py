"""Classify Google/Microsoft catalog MCP tools as external writes."""

from __future__ import annotations

from dataclasses import dataclass

from app.services.integrations.catalog import get_catalog, list_catalog
from app.services.mcp.bridge import parse_mcp_tool_name

ProviderId = str  # "google" | "microsoft"

# Explicit write remotes for the embedded Google MCP.
_GOOGLE_WRITE_REMOTES = frozenset(
    {
        "gmail_send_message",
        "gmail_create_draft",
        "gmail_reply_message",
        "gmail_trash_message",
        "gmail_modify_labels",
        "drive_create_folder",
        "drive_move_file",
        "drive_trash_file",
        "drive_copy_file",
        "calendar_create_event",
        "calendar_update_event",
        "calendar_delete_event",
        "meet_create_event",
        "tasks_create_task",
        "tasks_complete_task",
        "tasks_delete_task",
        "docs_create_document",
        "docs_append_text",
        "docs_replace_text",
        "docs_insert_text",
        "sheets_update_values",
        "sheets_append_values",
        "sheets_clear_values",
        "sheets_add_sheet",
        "sheets_delete_sheet",
        "sheets_rename_sheet",
        "sheets_delete_dimension",
        "sheets_copy_sheet",
        "sheets_create_spreadsheet",
        "slides_create_presentation",
        "slides_add_slide",
        "slides_delete_slide",
        "slides_replace_all_text",
        "slides_insert_text",
        "chat_send_message",
        "chat_create_space",
        "chat_delete_message",
    }
)

_WRITE_TOKENS = (
    "create",
    "update",
    "delete",
    "send",
    "reply",
    "forward",
    "move",
    "copy",
    "upload",
    "patch",
    "add",
    "remove",
    "set_",
    "_set",
    "trash",
    "clear",
    "append",
    "rename",
    "cancel",
    "invite",
    "schedule",
    "complete",
    "mark_",
    "post_",
    "put_",
    "write",
    "insert",
    "modify",
    "replace",
)

_READ_PREFIXES = (
    "list_",
    "get_",
    "search_",
    "find_",
    "read_",
    "fetch_",
    "query_",
    "describe_",
    "lookup_",
    "count_",
)


@dataclass(frozen=True)
class ExternalWriteTool:
    tool_name: str
    server_id: str
    remote_name: str
    provider_id: ProviderId
    catalog_id: str


def _slug_to_catalog() -> dict[str, str]:
    return {entry.slug(): entry.catalog_id for entry in list_catalog()}


def resolve_catalog_provider(server_id: str) -> tuple[str, ProviderId] | None:
    """Return (catalog_id, provider) for catalog MCP server ids."""
    catalog_id = _slug_to_catalog().get(server_id)
    if not catalog_id:
        # Allow raw catalog_id as server id fallback.
        entry = get_catalog(server_id)
        if entry is None:
            return None
        catalog_id = entry.catalog_id
    else:
        entry = get_catalog(catalog_id)
    if entry is None or not entry.oauth_provider_id:
        return None
    provider = entry.oauth_provider_id
    if provider not in {"google", "microsoft"}:
        return None
    return catalog_id, provider


def _microsoft_remote_is_write(remote_name: str) -> bool:
    name = remote_name.strip().lower()
    if not name:
        return False
    if any(name.startswith(p) for p in _READ_PREFIXES):
        return False
    return any(token in name for token in _WRITE_TOKENS)


def _google_remote_is_write(remote_name: str) -> bool:
    return remote_name.strip() in _GOOGLE_WRITE_REMOTES


def classify_external_write(tool_name: str) -> ExternalWriteTool | None:
    """If tool is a Google/Microsoft catalog write, return details; else None."""
    parsed = parse_mcp_tool_name(tool_name)
    if not parsed:
        return None
    server_id, remote = parsed
    resolved = resolve_catalog_provider(server_id)
    if not resolved:
        return None
    catalog_id, provider = resolved
    if provider == "google":
        if not _google_remote_is_write(remote):
            return None
    elif provider == "microsoft":
        if not _microsoft_remote_is_write(remote):
            return None
    else:
        return None
    return ExternalWriteTool(
        tool_name=tool_name,
        server_id=server_id,
        remote_name=remote,
        provider_id=provider,
        catalog_id=catalog_id,
    )


def is_external_write_tool(tool_name: str) -> bool:
    return classify_external_write(tool_name) is not None
