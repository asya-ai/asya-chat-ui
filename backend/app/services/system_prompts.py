from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone

from app.services.mcp import mcp_guidance_for_tools

MAIN_SYSTEM_PROMPT = (
    "Follow the user's instructions carefully. "
    "Use tools when needed, but never invent tool outputs. "
    "When tool results are available, ground your answer in those results. "
    "Before calling a tool, briefly tell the user what you are about to do in plain language. "
    "Prefer one user-visible tool step at a time when steps are independent checklist items. "
    "Use Mermaid diagrams when they would make an explanation clearer. Put Mermaid syntax in "
    "fenced ```mermaid Markdown blocks; those blocks are rendered for the user. "
    "Quote node labels that contain @, /, parentheses, or other special characters "
    '(e.g. C["@scope/pkg"] not C[@scope/pkg]). '
    "Do not spam diagrams where they do not actually help explanation. "
    "Always try to be helpful but not tease/request user to ask formore relevant info (you are not a youtuber and you do not need to raise engagement byy using these techniques). "
    "Request-specific facts arrive in a <context> block on user messages: each turn "
    "keeps a frozen <current_time> stamp; <memories> appear only on the latest user "
    "message. Treat that block as authoritative."
)

MEMORY_SYSTEM_PROMPT = (
    "You have access to a persistent memory system. Prefer using it proactively: "
    "when the user refers to prior work, preferences, decisions, names, or anything "
    "you may have discussed before, search before answering from guesswork. "
    "In a project, prefer search_project_sources (semantic search over indexed prior "
    "chats and uploaded files) and use search_past_chats for chat titles/previews. "
    "If the user asks to rely only on uploaded sources/files/documents, call "
    "search_project_sources with include_chats=false. "
    "Stored memories (when listed) are durable facts/preferences—apply them. "
    "Use store_memory for lasting preferences or explicit 'remember this' "
    "requests; do NOT store transient chat-only details. "
    "Outside projects, past-chat search covers personal chats only."
)

TOOL_SYSTEM_PROMPTS: dict[str, str] = {
    "code_execution": (
        "Use the code_execution tool for data analysis, calculations, CSV/XLSX processing, "
        "plotting, or file-based tasks **where needed**. If files are provided or the user asks for analysis, "
        "run code_execution before answering. Assume Python/tool access is available; do not "
        "claim you cannot access Python or files. Use real tool calls (not plain-text pseudo "
        "calls). Always set `purpose` to a short plain-language goal for the run "
        "(what you are trying to learn or produce), not imports or code. "
        "The sandbox has no network access — use web_search/web_scrape for internet data. "
        "Do not probe host CPU, RAM, cgroup, or kernel details. "
        "Uploaded chat files are available under /inputs with the listed filenames. "
        "When working in a project, project source files are also available under "
        "/inputs/project/. "
        "If image metadata (width/height/exif) is already present in context, do not call "
        "code_execution only to re-read those same basics."
    ),
    "extract_pdf": (
        "Use extract_pdf for PDF files in chat attachments. "
        "Always check page_count first, then request precise page/page ranges before answering. "
        "Do not guess missing pages."
    ),
    "web_search": (
        "Use web_search for fresh or uncertain facts. Keep queries focused and minimal."
    ),
    "web_scrape": (
        "Use web_scrape to read specific pages returned by search or provided by the user. "
        "It supports output=markdown, screenshot or answer. Prefer using answer instead of poluting context with full page info unless it is needed to accomplish the task."
        "For output=answer, always provide a question; the tool returns an answer grounded in the page with source quotes."
    ),
    "download_attachments": (
        "Use download_attachments for direct file/image URLs that should be imported as chat attachments. There is a limit of 25 urls per call"
    ),
    "generate_image": (
        "Use generate_image when the user asks for creating a new image. "
        "Before calling it, briefly say what image you are about to create. "
        "Prefer calling generate_image in its own step rather than batching it with unrelated tools. "
        "The tool returns file_name (e.g. generated-….png). To place it in a Marp presentation, "
        "reference that exact file_name in markdown — "
        "![bg right:40% cover](generated-….png) or ![](generated-….png). "
        "The app resolves chat attachment file names; do not invent /chat/… paths or bare relative URLs that are not the returned file_name."
    ),
    "edit_image": (
        "Use edit_image when the user asks to modify an existing image. "
        "Before calling it, briefly say what change you are about to make. "
        "If no image is specified, it will use the latest image attachment in the chat. "
        "Embed the returned file_name in Marp/markdown the same way as generate_image."
    ),
    "store_memory": (
        "Use store_memory to persist important facts the user shares about themselves, "
        "their preferences, or explicit requests to remember something. Keep entries concise."
    ),
    "remove_memory": (
        "Use remove_memory to delete a stored memory when the user asks you to forget "
        "something or when a fact becomes outdated. Use the memory_id from the memories list."
    ),
    "search_past_chats": (
        "Call search_past_chats to find earlier chats by keyword (titles and previews). "
        "In a project, also use search_project_sources for semantic recall over indexed chat "
        "transcripts and uploaded files. Prefer tools over guessing. Results include "
        "created_at and last_activity_at, ordered by most recent activity."
    ),
    "list_project_sources": (
        "Use list_project_sources to see files and indexed chats in this project. "
        "Pass include_chats=false to list uploaded documents only."
    ),
    "search_project_sources": (
        "Use search_project_sources for semantic retrieval over this project's uploaded "
        "files/URLs and indexed prior chats. When the user wants answers based only on "
        "uploaded sources/documents (not prior chats), set include_chats=false."
    ),
    "read_project_source": (
        "Use read_project_source to read a project file or indexed chat transcript by numeric id."
    ),
}

TOOL_PROMPT_ORDER = (
    "code_execution",
    "extract_pdf",
    "web_search",
    "web_scrape",
    "download_attachments",
    "generate_image",
    "edit_image",
    "store_memory",
    "remove_memory",
    "search_past_chats",
    "list_project_sources",
    "search_project_sources",
    "read_project_source",
)


def _locale_prompt(locale: str | None) -> str | None:
    if not locale:
        return None
    value = locale.replace("_", "-").strip().lower()
    if value.startswith("lv"):
        language = "Latvian"
    elif value.startswith("ja"):
        language = "Japanese"
    elif value.startswith("en"):
        language = "English"
    else:
        return None
    return (
        f"The user interface language is {language}. "
        "Respond in that language unless the user asks otherwise."
    )


def _coerce_utc(at: datetime | None) -> datetime:
    if at is None:
        return datetime.now(timezone.utc)
    if at.tzinfo is None:
        # ChatMessage.created_at is stored as naive UTC via datetime.utcnow.
        return at.replace(tzinfo=timezone.utc)
    return at.astimezone(timezone.utc)


def _floor_to_minute(at: datetime) -> datetime:
    return at.replace(second=0, microsecond=0)


def _current_time_context(
    user_timezone: str | None,
    *,
    at: datetime | None = None,
) -> str:
    now = _floor_to_minute(_coerce_utc(at))
    # Spell the calendar date multiple ways — small models often mangle ISO day numbers.
    utc_long = f"{now.strftime('%A')}, {now.day} {now.strftime('%B %Y')}"
    lines = [
        f"Today's date: {now.strftime('%Y-%m-%d')} ({utc_long})",
        f"Current UTC time: {now.strftime('%H:%M')} UTC",
    ]
    if user_timezone:
        try:
            from zoneinfo import ZoneInfo

            local_now = now.astimezone(ZoneInfo(user_timezone))
            local_long = (
                f"{local_now.strftime('%A')}, {local_now.day} "
                f"{local_now.strftime('%B %Y')}"
            )
            lines.append(
                f"User timezone: {user_timezone}"
                f" (local: {local_now.strftime('%Y-%m-%d %H:%M %Z')}, {local_long})"
            )
        except Exception:
            lines.append(f"User timezone: {user_timezone}")
    lines.append(
        "Use these values for today/tomorrow/this week and clock questions. "
        "Never substitute a different calendar date."
    )
    return "\n".join(lines)


def _memories_context(memories: list[dict[str, str]] | None) -> str:
    if not memories:
        return "(none)"
    return "\n".join(f"- [{mem['id']}] {mem['content']}" for mem in memories)


def build_request_context_block(
    *,
    timezone: str | None = None,
    at: datetime | None = None,
    memories: list[dict[str, str]] | None = None,
    include_memories: bool = False,
) -> str:
    """Per-user-turn context stamp.

    Time is frozen to ``at`` (message created_at) so historical stamps stay identical
    across requests and keep the prompt-cache prefix stable. Memories are bulky and
    change often — attach them only on the latest user turn.
    """
    parts = [
        "<context>",
        "<current_time>",
        _current_time_context(timezone, at=at),
        "</current_time>",
    ]
    if include_memories:
        parts.extend(["<memories>", _memories_context(memories), "</memories>"])
    parts.append("</context>")
    return "\n".join(parts)


def _prepend_context_to_content(content: object, context_block: str) -> object:
    if not context_block:
        return content
    if isinstance(content, list):
        return [{"type": "text", "text": context_block}, *content]
    text = content if isinstance(content, str) else ("" if content is None else str(content))
    if not text:
        return context_block
    return f"{context_block}\n\n{text}"


_CREATED_AT_KEY = "_created_at"


def provider_chat_message(
    role: str,
    content: object,
    *,
    created_at: datetime | None = None,
) -> dict:
    """Build a provider message; stash created_at on user rows for frozen time stamps."""
    message: dict[str, object] = {"role": role, "content": content}
    if role == "user" and created_at is not None:
        message[_CREATED_AT_KEY] = created_at
    return message


def attach_user_context_stamps(
    messages: list[dict],
    *,
    timezone: str | None = None,
    memories: list[dict[str, str]] | None = None,
    include_memories: bool = False,
) -> list[dict]:
    """Stamp every user message with frozen ``<current_time>``; memories only on latest.

    Pass ``_created_at`` on each user message dict (from ChatMessage.created_at) so
    historical stamps reconstruct identically on later turns. Missing timestamps fall
    back to "now" (acceptable for short sync paths without history).
    """
    if not messages:
        return messages
    user_indices = [
        index for index, message in enumerate(messages) if message.get("role") == "user"
    ]
    if not user_indices:
        return messages

    updated = [dict(message) for message in messages]
    latest_user_idx = user_indices[-1]
    for index in user_indices:
        created_at = updated[index].pop(_CREATED_AT_KEY, None)
        block = build_request_context_block(
            timezone=timezone,
            at=created_at if isinstance(created_at, datetime) else None,
            memories=memories,
            include_memories=include_memories and index == latest_user_idx,
        )
        updated[index]["content"] = _prepend_context_to_content(
            updated[index].get("content"), block
        )
    # Drop any leftover private keys on non-user rows.
    for message in updated:
        message.pop(_CREATED_AT_KEY, None)
    return updated


# Back-compat alias used by older call sites / tests.
def attach_request_context_to_latest_user_message(
    messages: list[dict],
    context_block: str,
) -> list[dict]:
    if not context_block or not messages:
        return messages
    last_user_idx: int | None = None
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].get("role") == "user":
            last_user_idx = index
            break
    if last_user_idx is None:
        return messages
    updated = list(messages)
    message = dict(updated[last_user_idx])
    message["content"] = _prepend_context_to_content(message.get("content"), context_block)
    updated[last_user_idx] = message
    return updated


def build_system_prompt_messages(
    *,
    locale: str | None = None,
    enabled_tool_names: Iterable[str] | None = None,
) -> list[dict[str, str]]:
    """Stable system prefix only — clocks/memories ride on user-message <context> stamps."""
    tool_names = set(enabled_tool_names or [])
    messages: list[dict[str, str]] = [
        {"role": "system", "content": MAIN_SYSTEM_PROMPT},
    ]
    locale_instruction = _locale_prompt(locale)
    if locale_instruction:
        messages.append({"role": "system", "content": locale_instruction})
    for name in TOOL_PROMPT_ORDER:
        if name in tool_names:
            prompt = TOOL_SYSTEM_PROMPTS.get(name)
            if prompt:
                messages.append({"role": "system", "content": prompt})
    mcp_guidance = mcp_guidance_for_tools(tool_names)
    if mcp_guidance:
        messages.append({"role": "system", "content": mcp_guidance})
    memory_tools_enabled = bool(
        tool_names.intersection({"store_memory", "remove_memory", "search_past_chats"})
    )
    if memory_tools_enabled:
        messages.append({"role": "system", "content": MEMORY_SYSTEM_PROMPT})
    return messages
