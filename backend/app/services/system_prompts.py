from __future__ import annotations

from collections.abc import Iterable

MAIN_SYSTEM_PROMPT = (
    "Follow the user's instructions carefully. "
    "Use tools when needed, but never invent tool outputs. "
    "When tool results are available, ground your answer in those results. "
    "Always try to be helpful but not tease/request user to ask formore relevant info (you are not a youtuber and you do not need to raise engagement byy using these techniques)."
)

TOOL_SYSTEM_PROMPTS: dict[str, str] = {
    "code_execution": (
        "Use the code_execution tool for data analysis, calculations, CSV/XLSX processing, "
        "plotting, or file-based tasks. If files are provided or the user asks for analysis, "
        "run code_execution before answering. Assume Python/tool access is available; do not "
        "claim you cannot access Python or files. Use real tool calls (not plain-text pseudo "
        "calls). Uploaded files are available under /inputs with the listed filenames."
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
    "get_time": (
        "Use get_time for timezone/city/country/coordinate current-time lookups."
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
    "get_time",
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


def build_system_prompt_messages(
    *,
    locale: str | None = None,
    enabled_tool_names: Iterable[str] | None = None,
) -> list[dict[str, str]]:
    tool_names = set(enabled_tool_names or [])
    messages: list[dict[str, str]] = [{"role": "system", "content": MAIN_SYSTEM_PROMPT}]
    for name in TOOL_PROMPT_ORDER:
        if name in tool_names:
            prompt = TOOL_SYSTEM_PROMPTS.get(name)
            if prompt:
                messages.append({"role": "system", "content": prompt})
    locale_instruction = _locale_prompt(locale)
    if locale_instruction:
        messages.append({"role": "system", "content": locale_instruction})
    return messages
