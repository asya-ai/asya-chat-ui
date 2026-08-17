from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from app.services.mcp import mcp_action_summary


def _ensure_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _code_summary(code: str, limit: int = 80) -> str:
    for line in (code or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if len(stripped) > limit:
            return f"{stripped[: limit - 1]}…"
        return stripped
    return ""


def _display_url(url: str) -> str:
    text = (url or "").strip()
    if not text:
        return ""
    try:
        parsed = urlparse(text)
    except Exception:
        return text[:120]
    host = (parsed.netloc or "").strip()
    if host:
        return host
    return text[:120]


def tool_call_action_summary(name: str, arguments: dict[str, Any] | None = None) -> str:
    """Human-readable one-liner for non-debug thinking activity."""
    args = arguments if isinstance(arguments, dict) else {}
    mcp_summary = mcp_action_summary(name, args)
    if mcp_summary:
        return mcp_summary
    if name == "web_search":
        queries = _ensure_list(args.get("queries")) or _ensure_list(args.get("query"))
        query = next(
            (str(item).strip() for item in queries if isinstance(item, str) and item.strip()),
            "",
        )
        return f"Looking up {query}" if query else "Looking up web"
    if name == "web_scrape":
        urls = _ensure_list(args.get("urls")) or _ensure_list(args.get("url"))
        target = next(
            (
                _display_url(str(item))
                for item in urls
                if isinstance(item, (str, bytes)) and str(item).strip()
            ),
            "",
        )
        question = str(args.get("question") or "").strip()
        if question:
            truncated = f"{question[:120]}…" if len(question) > 120 else question
            if target:
                return f"Exploring {target}: {truncated}"
            return f"Exploring web: {truncated}"
        return f"Exploring {target}" if target else "Exploring web"
    if name == "code_execution":
        purpose = str(args.get("purpose") or "").strip()
        if purpose:
            truncated = f"{purpose[:120]}…" if len(purpose) > 120 else purpose
            return f"Running code ({truncated})"
        summary = _code_summary(str(args.get("code") or ""))
        return f"Running code ({summary})" if summary else "Running code"
    if name == "download_attachments":
        return "Downloading attachments"
    if name == "extract_pdf":
        return "Extracting PDF"
    if name == "generate_image":
        return "Generating image"
    if name == "edit_image":
        return "Editing image"
    if name == "store_memory":
        return "Saving memory"
    if name == "remove_memory":
        return "Removing memory"
    if name == "search_past_chats":
        query = str(args.get("query") or "").strip()
        return f"Searching past chats: {query}" if query else "Searching past chats"
    if name == "start_coworking":
        title = str(args.get("title") or args.get("file_name") or "").strip()
        return f"Opening co-editing: {title}" if title else "Opening co-editing"
    if name == "cowork_read":
        return "Reading document"
    if name == "cowork_write":
        return "Writing document"
    if name == "cowork_str_replace":
        return "Editing document"
    if name == "cowork_append":
        return "Appending to document"
    if name == "get_current_time":
        return "Checking the time"
    label = (name or "tool").replace("_", " ").strip() or "tool"
    return f"Running {label}"
