from __future__ import annotations

import json
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
    if name == "mcp_data_list":
        return "Listing stored MCP data"
    if name == "mcp_data_get":
        artifact = str(args.get("artifact_id") or "").strip()
        path = str(args.get("path") or "").strip()
        if artifact and path:
            return f"Reading MCP data {artifact}: {path[:80]}"
        if artifact:
            return f"Reading MCP data {artifact}"
        return "Reading stored MCP data"
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


def tool_call_input_preview(name: str, arguments: dict[str, Any] | None = None) -> str:
    """Detailed input text shown when action info is expanded."""
    args = arguments if isinstance(arguments, dict) else {}
    if name == "web_search":
        queries = _ensure_list(args.get("queries")) or _ensure_list(args.get("query"))
        if queries:
            return f"query: {', '.join(str(item) for item in queries[:3])}"
        return "query: (empty)"
    if name == "web_scrape":
        urls = _ensure_list(args.get("urls")) or _ensure_list(args.get("url"))
        output = str(args.get("output") or "markdown").strip().lower() or "markdown"
        question = str(args.get("question") or "").strip()
        base = f"urls: {', '.join(str(item) for item in urls[:2])}" if urls else "urls: (empty)"
        if output == "answer" and question:
            return f"{base}; question: {question}"
        return f"{base}; output: {output}"
    if name == "download_attachments":
        urls = _ensure_list(args.get("urls")) or _ensure_list(args.get("url"))
        return f"urls: {', '.join(str(item) for item in urls[:3])}" if urls else "urls: (empty)"
    if name == "extract_pdf":
        attachment_id = str(args.get("attachment_id") or "").strip()
        file_name = str(args.get("file_name") or "").strip()
        page = args.get("page")
        page_from = args.get("page_from")
        page_to = args.get("page_to")
        target = (
            f"attachment_id: {attachment_id}"
            if attachment_id
            else (f"file: {file_name}" if file_name else "latest PDF")
        )
        if isinstance(page, int):
            return f"{target}; page: {page}"
        if isinstance(page_from, int) or isinstance(page_to, int):
            return f"{target}; range: {page_from or 1}..{page_to or page_from or 1}"
        return f"{target}; page selection missing"
    if name == "code_execution":
        purpose = str(args.get("purpose") or "").strip()
        if purpose:
            return f"purpose: {purpose}"
        language = str(args.get("language") or "python").strip() or "python"
        code = str(args.get("code") or "").strip()
        first_line = code.splitlines()[0] if code else ""
        return f"{language}: {first_line}" if first_line else f"{language}: (empty code)"
    if name in {"generate_image", "edit_image"}:
        prompt = str(args.get("prompt") or "").strip()
        return f"prompt: {prompt}" if prompt else "prompt: (empty)"
    if name == "store_memory":
        content = str(args.get("content") or "").strip()
        return f"content: {content}" if content else "content: (empty)"
    if name == "remove_memory":
        return f"memory_id: {args.get('memory_id', '')}"
    if name == "search_past_chats":
        query = str(args.get("query") or "").strip()
        return f"query: {query}" if query else "query: (empty)"
    if not args:
        return "no args"
    try:
        dumped = json.dumps(args, ensure_ascii=False)
    except TypeError:
        keys = ", ".join(sorted(str(key) for key in args.keys())[:6])
        return f"args: {keys}" if keys else "no args"
    if len(dumped) <= 2000:
        return dumped
    return f"{dumped[:1997]}..."


def tool_call_output_preview(name: str, output: dict[str, Any] | None = None) -> str:
    """Short result text shown under a tool call in detailed action info."""
    if not isinstance(output, dict):
        return "completed"
    if name == "web_search":
        queries = output.get("queries", []) or []
        count = 0
        if isinstance(queries, list):
            for batch in queries:
                if isinstance(batch, dict):
                    count += len(batch.get("results", []) or [])
        return f"results: {count}"
    if name == "web_scrape":
        results = output.get("results", []) or []
        success = 0
        failures = 0
        if isinstance(results, list):
            for item in results:
                if isinstance(item, dict) and item.get("error"):
                    failures += 1
                else:
                    success += 1
        return f"success: {success}, failed: {failures}"
    if name == "download_attachments":
        results = output.get("results", []) or []
        files = 0
        failures = 0
        if isinstance(results, list):
            for item in results:
                if isinstance(item, dict) and item.get("error"):
                    failures += 1
                else:
                    files += 1
        return f"files: {files}, failed: {failures}"
    if name == "extract_pdf":
        page_count = output.get("page_count")
        selected = output.get("selected_pages")
        if isinstance(selected, list) and selected:
            return f"page_count: {page_count}, extracted: {len(selected)} page(s)"
        return f"page_count: {page_count}"
    if name == "code_execution":
        if output.get("requires_approval"):
            return "requires approval"
        if output.get("timed_out"):
            return "timed out"
        if output.get("error"):
            return f"error: {str(output.get('error'))[:160]}"
        exit_code = output.get("exit_code")
        if isinstance(exit_code, int):
            return f"exit code: {exit_code}"
        return "completed"
    if name in {"generate_image", "edit_image"}:
        if output.get("error"):
            return f"error: {str(output.get('error'))[:160]}"
        count = output.get("image_count")
        if isinstance(count, int):
            return f"images: {count}"
        return "image generated"
    if name == "store_memory":
        return str(output.get("status", "completed"))
    if name == "remove_memory":
        return str(output.get("status", "completed"))
    if name == "search_past_chats":
        results = output.get("results", [])
        return f"found: {len(results)} chat(s)" if isinstance(results, list) else "completed"
    return "completed"
