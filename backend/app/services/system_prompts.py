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
    "Always try to be helpful but not tease/request user to ask formore relevant info (you are not a youtuber and you do not need to raise engagement byy using these techniques)."
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
        "Prefer calling generate_image in its own step rather than batching it with unrelated tools."
    ),
    "edit_image": (
        "Use edit_image when the user asks to modify an existing image. "
        "Before calling it, briefly say what change you are about to make. "
        "If no image is specified, it will use the latest image attachment in the chat."
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


def _time_prompt(user_timezone: str | None) -> str:
    now = datetime.now(timezone.utc)
    utc_str = now.strftime("%Y-%m-%d %H:%M UTC")
    parts = [f"Current UTC time: {utc_str}."]
    if user_timezone:
        try:
            from zoneinfo import ZoneInfo
            local_now = now.astimezone(ZoneInfo(user_timezone))
            local_str = local_now.strftime("%Y-%m-%d %H:%M %Z")
            parts.append(f"User's timezone: {user_timezone} (local time: {local_str}).")
        except Exception:
            parts.append(f"User's timezone: {user_timezone}.")
    return " ".join(parts)


def _memories_prompt(memories: list[dict[str, str]] | None) -> str:
    lines = [MEMORY_SYSTEM_PROMPT]
    if memories:
        lines.extend(["", "## Stored memories"])
        for mem in memories:
            lines.append(f"- [{mem['id']}] {mem['content']}")
    return "\n".join(lines)


def build_system_prompt_messages(
    *,
    locale: str | None = None,
    timezone: str | None = None,
    enabled_tool_names: Iterable[str] | None = None,
    memories: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    tool_names = set(enabled_tool_names or [])
    messages: list[dict[str, str]] = [{"role": "system", "content": MAIN_SYSTEM_PROMPT}]
    memory_tools_enabled = bool(
        tool_names.intersection({"store_memory", "remove_memory", "search_past_chats"})
    )
    if memories or memory_tools_enabled:
        messages.append({"role": "system", "content": _memories_prompt(memories)})
    for name in TOOL_PROMPT_ORDER:
        if name in tool_names:
            prompt = TOOL_SYSTEM_PROMPTS.get(name)
            if prompt:
                messages.append({"role": "system", "content": prompt})
    mcp_guidance = mcp_guidance_for_tools(tool_names)
    if mcp_guidance:
        messages.append({"role": "system", "content": mcp_guidance})
    locale_instruction = _locale_prompt(locale)
    if locale_instruction:
        messages.append({"role": "system", "content": locale_instruction})
    messages.append({"role": "system", "content": _time_prompt(timezone)})
    return messages
