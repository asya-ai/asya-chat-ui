from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone

MAIN_SYSTEM_PROMPT = (
    "Follow the user's instructions carefully. "
    "Use tools when needed, but never invent tool outputs. "
    "When tool results are available, ground your answer in those results. "
    "Use Mermaid diagrams when they would make an explanation clearer. Put Mermaid syntax in "
    "fenced ```mermaid Markdown blocks; those blocks are rendered for the user. Do not spam diagrams where they do not actually help explanation. "
    "Always try to be helpful but not tease/request user to ask formore relevant info (you are not a youtuber and you do not need to raise engagement byy using these techniques)."
)

MEMORY_SYSTEM_PROMPT = (
    "You have access to a persistent memory system. Stored memories are listed below "
    "and represent important facts, preferences, or instructions from the user. "
    "Use them to personalize your responses. You can store new memories for truly "
    "important or global information (user preferences, key facts about them, explicit "
    "requests to remember something). Do NOT store transient or chat-specific details. "
    "You can also search the user's past chats when they reference previous conversations."
)

TOOL_SYSTEM_PROMPTS: dict[str, str] = {
    "code_execution": (
        "Use the code_execution tool for data analysis, calculations, CSV/XLSX processing, "
        "plotting, or file-based tasks **where needed**. If files are provided or the user asks for analysis, "
        "run code_execution before answering. Assume Python/tool access is available; do not "
        "claim you cannot access Python or files. Use real tool calls (not plain-text pseudo "
        "calls). Uploaded files are available under /inputs with the listed filenames. "
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
        "Use generate_image when the user asks for creating a new image."
    ),
    "edit_image": (
        "Use edit_image when the user asks to modify an existing image. "
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
        "Use search_past_chats to find information from the user's previous conversations. "
        "Useful when they reference something discussed before."
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
)


def _locale_prompt(locale: str | None) -> str | None:
    if not locale:
        return None
    value = locale.replace("_", "-").strip().lower()
    if value.startswith("lv"):
        language = "Latvian"
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


def _memories_prompt(memories: list[dict[str, str]]) -> str:
    lines = [MEMORY_SYSTEM_PROMPT, "", "## Stored memories"]
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
    if memories:
        messages.append({"role": "system", "content": _memories_prompt(memories)})
    for name in TOOL_PROMPT_ORDER:
        if name in tool_names:
            prompt = TOOL_SYSTEM_PROMPTS.get(name)
            if prompt:
                messages.append({"role": "system", "content": prompt})
    locale_instruction = _locale_prompt(locale)
    if locale_instruction:
        messages.append({"role": "system", "content": locale_instruction})
    messages.append({"role": "system", "content": _time_prompt(timezone)})
    return messages
