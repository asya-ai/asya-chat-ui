from datetime import datetime
import html
import logging
import re
from urllib.parse import urlparse
from uuid import UUID, uuid4

import json
import httpx
import anyio
from sqlalchemy import func
from fastapi import APIRouter, Depends, HTTPException, status, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator, model_validator
from sqlmodel import Session, select

from app.api.deps import get_current_user, get_db
from app.core.config import settings
from app.core.security import decode_access_token
from app.db.session import engine
from app.models import (
    Chat,
    ChatGenerationEvent,
    ChatGenerationTask,
    ChatMessage,
    ChatMessageAttachment,
    ChatModel,
    GenerationStatus,
    Org,
    OrgModel,
    UsageEvent,
    User,
)
from app.services.org_service import require_org_member, require_provider_enabled
from app.services.providers.base import ChatResponse, ChatUsage
from app.services.providers.registry import get_provider
from app.services.tools.image_tool import (
    ImageToolContext,
    edit_image,
    generate_image,
    get_image_model,
)
from app.services.tools.code_execution import CodeExecutionContext, run_code_execution
from app.services.tools.registry import ToolRegistry, ToolSpec, ToolResult
from app.services.tools.time_tool import TimeToolContext, get_time
from app.services.tools.web_tools import WebToolContext, web_scrape, web_search
from app.workers.celery_app import celery_app

router = APIRouter(prefix="/chats", tags=["chats"])
logger = logging.getLogger(__name__)

MAX_CONTEXT_MESSAGES = 40
HEAD_CONTEXT_MESSAGES = 4
TAIL_CONTEXT_MESSAGES = 12
MAX_TOOL_STEPS = 10
MAX_WEB_SEARCH_CALLS = 2
MAX_WEB_SCRAPE_CALLS = 5
TOOL_GUIDANCE_PROMPT = (
    "You have access to the code_execution tool and should prefer it for any "
    "data analysis, calculations, CSV/XLSX processing, plotting, or file-based tasks. "
    "If files are provided or the user asks for analysis, run code_execution before "
    "answering. Assume Python/tool access is available; if you are about to say you "
    "cannot access Python or files, DO NOT respond that way—instead call "
    "code_execution and verify. You MAY call code_execution multiple times to refine "
    "your result based on previous outputs. Never claim you lack access to "
    "Python/tools. Do not write tool calls in plain text (no 'to=code_execution.run'); "
    "use the tool call interface with name code_execution and a code string. "
    "Uploaded files are available under /inputs with the exact filenames listed "
    "in the user's message."
)


def _estimate_tokens(messages: list[dict]) -> int:
    total_chars = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            for part in content:
                if part.get("type") == "text":
                    total_chars += len(part.get("text", ""))
        elif isinstance(content, str):
            total_chars += len(content)
    return max(1, total_chars // 4)


def _truncate_messages(messages: list[dict], *, token_limit: int | None) -> list[dict]:
    if token_limit is None:
        return messages
    if len(messages) <= MAX_CONTEXT_MESSAGES and _estimate_tokens(messages) <= token_limit:
        return messages
    head = messages[:HEAD_CONTEXT_MESSAGES]
    tail = messages[-TAIL_CONTEXT_MESSAGES:]
    truncated = [
        *head,
        {"role": "system", "content": "[chat contents truncated]"},
        *tail,
    ]
    if _estimate_tokens(truncated) <= token_limit:
        return truncated
    return tail


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


def _prepend_tool_guidance(messages: list[dict], *, locale: str | None = None) -> list[dict]:
    system_messages = [{"role": "system", "content": TOOL_GUIDANCE_PROMPT}]
    locale_instruction = _locale_prompt(locale)
    if locale_instruction:
        system_messages.append({"role": "system", "content": locale_instruction})
    return [*system_messages, *messages]


def _is_image_output_model(model: ChatModel) -> bool:
    if "image" in model.model_name.lower():
        return True
    if model.supports_image_output is True:
        return True
    if model.supports_image_output is False:
        return False
    return False


def _grounding_enabled(org: Org, provider: str) -> bool:
    if provider == "openai":
        return org.web_grounding_openai
    if provider == "gemini":
        return org.web_grounding_gemini
    return False


def _build_tool_registry(
    session: Session,
    org_id: UUID,
    *,
    chat_id: UUID | None = None,
    preferred_provider: str | None = None,
    web_tools_enabled: bool = False,
    web_search_enabled: bool = False,
    web_scrape_enabled: bool = False,
    exec_policy: str = "off",
    exec_network_enabled: bool = False,
    locale: str | None = None,
) -> ToolRegistry:
    image_model = get_image_model(
        session, str(org_id), preferred_provider=preferred_provider
    )
    if not image_model:
        logger.info("No image model enabled for org_id=%s (tool still exposed)", org_id)
    registry = ToolRegistry()
    async def _handler(args: dict) -> object:
        return await generate_image(
            ImageToolContext(session=session, org_id=str(org_id)),
            prompt=args.get("prompt", ""),
        )

    registry.register(
        ToolSpec(
            name="generate_image",
            description="Generate an image from a text prompt.",
            parameters={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Text prompt for the image"}
                },
                "required": ["prompt"],
            },
        ),
        _handler,
    )
    async def _edit_handler(args: dict) -> object:
        return await edit_image(
            ImageToolContext(session=session, org_id=str(org_id)),
            prompt=args.get("prompt", ""),
            image_id=args.get("image_id"),
            image_base64=args.get("image_base64"),
            image_content_type=args.get("image_content_type"),
            mask_id=args.get("mask_id"),
            mask_base64=args.get("mask_base64"),
            mask_content_type=args.get("mask_content_type"),
        )

    registry.register(
        ToolSpec(
            name="edit_image",
            description="Edit an existing image with a prompt and optional mask.",
            parameters={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Edit instructions"},
                    "image_id": {"type": "string", "description": "Attachment ID of the image"},
                    "image_base64": {"type": "string", "description": "Base64 image data"},
                    "image_content_type": {"type": "string", "description": "Image MIME type"},
                    "mask_id": {"type": "string", "description": "Attachment ID of the mask"},
                    "mask_base64": {"type": "string", "description": "Base64 mask data"},
                    "mask_content_type": {"type": "string", "description": "Mask MIME type"},
                },
                "required": ["prompt"],
            },
        ),
        _edit_handler,
    )
    async def _time_handler(args: dict) -> object:
        return await get_time(
            TimeToolContext(org_id=str(org_id)),
            timezone_name=args.get("timezone"),
            city=args.get("city"),
            country=args.get("country"),
            latitude=args.get("latitude"),
            longitude=args.get("longitude"),
        )

    registry.register(
        ToolSpec(
            name="get_time",
            description=(
                "Get the current time for a timezone, city, country, or coordinates."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "timezone": {
                        "type": "string",
                        "description": "IANA timezone name, e.g. Europe/Riga",
                    },
                    "city": {"type": "string", "description": "City name"},
                    "country": {"type": "string", "description": "Country name"},
                    "latitude": {"type": "number", "description": "Latitude"},
                    "longitude": {"type": "number", "description": "Longitude"},
                },
            },
        ),
        _time_handler,
    )
    if web_tools_enabled and web_search_enabled:
        async def _search_handler(args: dict) -> object:
            return await web_search(
                WebToolContext(org_id=str(org_id), locale=locale),
                query=args.get("query"),
                queries=args.get("queries"),
                max_results=args.get("max_results"),
            )

        registry.register(
            ToolSpec(
                name="web_search",
                description="Search the web for relevant results (keep it minimal and fast; use once, maybe twice if you have followup questions but not abuse this as it is really slow). Prefer to check your answers not imagine facts.",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "queries": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Multiple search queries",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Max results per query",
                        },
                    },
                },
            ),
            _search_handler,
        )
    if web_tools_enabled and web_scrape_enabled:
        async def _scrape_handler(args: dict) -> object:
            return await web_scrape(
                WebToolContext(org_id=str(org_id), locale=locale),
                url=args.get("url"),
                urls=args.get("urls"),
                output=args.get("output"),
            )

        registry.register(
            ToolSpec(
                name="web_scrape",
                description=(
                    "Fetch a web page and return markdown or a full-page screenshot "
                    "(only if needed; keep it minimal). Use output=markdown or output=screenshot."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "URL to scrape"},
                        "urls": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Multiple URLs to scrape",
                        },
                        "output": {
                            "type": "string",
                            "enum": ["markdown", "screenshot"],
                            "description": "Choose markdown text or a full-page screenshot",
                        },
                    },
                },
            ),
            _scrape_handler,
        )
    if exec_policy != "off" and chat_id:
        async def _exec_handler(args: dict) -> object:
            code = args.get("code", "")
            if exec_policy == "prompt":
                return ToolResult(
                    name="code_execution",
                    output={
                        "error": "Execution requires approval",
                        "code": code,
                        "requires_approval": True,
                    },
                )
            return await run_code_execution(
                CodeExecutionContext(
                    session=session,
                    org_id=str(org_id),
                    chat_id=str(chat_id),
                    network_enabled=exec_network_enabled,
                ),
                code=code,
                language=args.get("language", "python"),
            )

        registry.register(
            ToolSpec(
                name="code_execution",
                description=(
                    "Execute any code as if it was python file."
                    "Your given code will be put in main.py and executed in a sandbox."
                    "Given that it is run as `python main.py`, you need to use `print()` statements to see results."
                    "Use this for any data analysis, plotting, or file-based tasks."
                    "All files from this chat are available as read-only under /inputs."
                    "Write any output files to /outputs to return them to the user (images, resulting csv etc.)."
                    "YOu dont need to tell user where the file was created, it will be sent together with your response to them."
                    "You can call this tool multiple times. Filenames are <attachment_id>_<sanitized_name>."
                    "Calls do not reuse same sandbox, so any created files will be lost after the call."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "description": "Python code to execute",
                        },
                        "language": {
                            "type": "string",
                            "description": "Execution language (python only)",
                        },
                    },
                    "required": ["code"],
                },
            ),
            _exec_handler,
        )
    logger.info("Registered tools: %s", [tool.name for tool in registry.list_specs()])
    return registry


def _sanitize_attachment_filename(name: str) -> str:
    base = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", base).strip("._")
    return cleaned or "file"


def _attachment_exec_path(attachment: ChatMessageAttachment) -> str:
    safe_name = _sanitize_attachment_filename(attachment.file_name)
    return f"/inputs/{attachment.id}_{safe_name}"


def _attachment_lines(attachments: list[ChatMessageAttachment]) -> list[str]:
    lines: list[str] = []
    if attachments:
        lines.append(
            "Use the code_execution tool to read/analyze these files before answering."
        )
    for attachment in attachments:
        lines.append(
            f"- {attachment.file_name} ({attachment.content_type}) at {_attachment_exec_path(attachment)}"
        )
    return lines


def _source_item(url: str, title: str | None = None) -> dict:
    host = ""
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        host = ""
    return {
        "url": url,
        "title": title,
        "host": host or url,
    }


async def _resolve_source_urls(urls: list[str]) -> list[dict]:
    if not urls:
        return []
    unique = list(dict.fromkeys([url for url in urls if isinstance(url, str) and url]))
    results: list[dict] = []
    async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
        for url in unique:
            title = None
            final_url = url
            try:
                response = await client.get(url, headers={"User-Agent": "chatui/1.0"})
                final_url = str(response.url)
                content_type = response.headers.get("content-type", "")
                if "text/html" in content_type:
                    text = response.text[:20000]
                    match = re.search(r"<title>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
                    if match:
                        title = html.unescape(match.group(1)).strip()
            except Exception:
                final_url = url
            results.append(_source_item(final_url, title))
    return results


def _limit_sources(items: list[dict] | None, max_items: int = 5) -> list[dict]:
    if not items:
        return []
    return items[:max_items]


async def _normalize_sources(
    items: list[dict] | list[str] | None,
) -> list[dict]:
    if not items:
        return []
    dict_items = [item for item in items if isinstance(item, dict)]
    if dict_items:
        return _limit_sources(dict_items)
    url_items = [item for item in items if isinstance(item, str) and item]
    if not url_items:
        return []
    return _limit_sources(await _resolve_source_urls(url_items))


async def _maybe_update_chat_title(
    *,
    session: Session,
    chat: Chat,
    provider,
    model: ChatModel,
    history: list[ChatMessage],
) -> None:
    message_count = len(history)
    should_update_title = message_count == 2 or message_count % 10 == 0
    if not should_update_title:
        return
    if message_count == 2:
        title_source = history[:2]
    else:
        title_source = history[-10:]
    prompt_lines = []
    for item in title_source:
        content = (item.content or "").strip()
        if not content and item.role == "assistant":
            content = "[image generated]"
        prompt_lines.append(f"{item.role.upper()}: {content}")
    title_prompt = "\n".join(prompt_lines)

    title_model = model
    title_provider = provider

    # Image-output models can't do text chat. Find the nearest chat model from
    # the same provider (same org) and use that for title generation instead.
    if _is_image_output_model(model):
        fallback = session.exec(
            select(ChatModel)
            .where(
                ChatModel.provider == model.provider,
                ChatModel.is_active == True,  # noqa: E712
                ChatModel.supports_image_output.is_(None)
                | (ChatModel.supports_image_output == False),  # noqa: E712
            )
            .limit(1)
        ).first()
        if fallback:
            try:
                provider_config = require_provider_enabled(
                    session, chat.org_id, fallback.provider
                )
                config = None
                if provider_config and provider_config.config_json:
                    try:
                        config = json.loads(provider_config.config_json)
                    except json.JSONDecodeError:
                        pass
                title_provider = get_provider(
                    fallback.provider,
                    api_key=provider_config.api_key_override if provider_config else None,
                    base_url=provider_config.base_url_override if provider_config else None,
                    endpoint=provider_config.endpoint_override if provider_config else None,
                    config=config,
                )
                title_model = fallback
            except Exception:
                logger.warning(
                    "Could not build fallback title provider for chat_id=%s", chat.id
                )
                return
        else:
            logger.warning(
                "No chat model found for title generation (provider=%s)", model.provider
            )
            return

    title_messages = [
        {
            "role": "system",
            "content": "Create a concise chat title (max 6 words). Reply with the title only.",
        },
        {"role": "user", "content": title_prompt},
    ]
    try:
        title_response = await title_provider.chat(title_model.model_name, title_messages)
        title = title_response.content.strip().strip('"').strip("'")
        if title:
            chat.title = title
            session.add(chat)
            session.commit()
    except Exception:
        logger.warning(
            "Failed to generate chat title for chat_id=%s model=%s",
            chat.id,
            title_model.model_name,
            exc_info=True,
        )


def _extract_ws_token(websocket: WebSocket) -> str | None:
    auth = websocket.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()
    protocols = websocket.headers.get("sec-websocket-protocol")
    if protocols:
        for entry in protocols.split(","):
            value = entry.strip()
            if value.startswith("token."):
                return value[len("token.") :]
    return None


def _get_user_from_token(session: Session, token: str) -> User:
    user_id = UUID(decode_access_token(token))
    user = session.exec(select(User).where(User.id == user_id)).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found"
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Inactive user"
        )
    return user


async def _ws_send_event(websocket: WebSocket, payload: dict) -> None:
    await websocket.send_json(payload)


def _format_model_error(exc: Exception) -> str:
    message = str(exc)
    lowered = message.lower()
    if "not a chat model" in lowered:
        return "Selected model does not support chat completions. Choose a chat-capable model."
    return f"Model error: {message}"


def _enqueue_generation_task(task_id: UUID) -> None:
    celery_app.send_task("chatui.generate_chat_response", args=[str(task_id)])


def _event_payload_from_record(event: ChatGenerationEvent) -> dict:
    payload = event.payload_json or {}
    if event.event_type == "activity":
        payload = {"activity": payload}
    elif event.event_type == "tool_event":
        payload = {"tool_event": payload}
    payload.setdefault("task_id", str(event.task_id))
    return payload


async def _stream_task_events_ws(
    websocket: WebSocket, task_id: UUID, *, after_sequence: int = 0
) -> None:
    last_sequence = after_sequence
    while True:
        with Session(engine) as stream_session:
            events = stream_session.exec(
                select(ChatGenerationEvent)
                .where(ChatGenerationEvent.task_id == task_id)
                .where(ChatGenerationEvent.sequence > last_sequence)
                .order_by(ChatGenerationEvent.sequence)
            ).all()
            for event in events:
                last_sequence = event.sequence
                await _ws_send_event(websocket, _event_payload_from_record(event))

            task = stream_session.exec(
                select(ChatGenerationTask).where(ChatGenerationTask.id == task_id)
            ).first()
            if (
                task
                and task.status in {GenerationStatus.completed, GenerationStatus.failed, GenerationStatus.cancelled}
                and not events
            ):
                return
        await anyio.sleep(0.5)


async def _stream_task_events_sse(task_id: UUID, *, after_sequence: int = 0):
    last_sequence = after_sequence
    while True:
        with Session(engine) as stream_session:
            events = stream_session.exec(
                select(ChatGenerationEvent)
                .where(ChatGenerationEvent.task_id == task_id)
                .where(ChatGenerationEvent.sequence > last_sequence)
                .order_by(ChatGenerationEvent.sequence)
            ).all()
            for event in events:
                last_sequence = event.sequence
                payload = _event_payload_from_record(event)
                yield f"data: {json.dumps(payload)}\n\n"

            task = stream_session.exec(
                select(ChatGenerationTask).where(ChatGenerationTask.id == task_id)
            ).first()
            if (
                task
                and task.status in {GenerationStatus.completed, GenerationStatus.failed, GenerationStatus.cancelled}
                and not events
            ):
                return
        await anyio.sleep(0.5)


async def _run_agentic_loop(
    *,
    provider,
    model: ChatModel,
    messages: list[dict],
    tool_registry: ToolRegistry,
    activity_sender: anyio.abc.ObjectSendStream | None = None,
    tool_event_sender: anyio.abc.ObjectSendStream | None = None,
) -> tuple[str, list[dict], list[dict], list[dict], ChatUsage | None]:
    tool_specs = tool_registry.list_specs()
    attachments: list[dict] = []
    sources: list[dict] = []
    image_usages: list[dict] = []
    last_usage: ChatUsage | None = None
    last_tool_error: str | None = None
    search_calls = 0
    scrape_calls = 0
    async def _emit(label: str, state: str) -> None:
        if activity_sender:
            await activity_sender.send({"label": label, "state": state})

    async def _emit_tool_event(payload: dict) -> None:
        if tool_event_sender:
            await tool_event_sender.send(payload)

    def _labels_for_call(name: str, arguments: dict) -> list[str]:
        if name == "web_search":
            queries = arguments.get("queries") or []
            query = arguments.get("query")
            if query:
                queries = [query] + list(queries)
            labels = [f"Searching: {item}" for item in queries if isinstance(item, str) and item]
            return labels or ["Searching web"]
        if name == "web_scrape":
            return ["Reading sources"]
        if name == "generate_image":
            return ["Generating image"]
        if name == "edit_image":
            return ["Editing image"]
        if name == "code_execution":
            return ["Executing code"]
        return [f"Running {name}"]
    for step_index in range(MAX_TOOL_STEPS):
        step_label = f"Step {step_index + 1}/{MAX_TOOL_STEPS}"
        await _emit(step_label, "start")
        try:
            logger.info(
                "Agentic step %s for model=%s tools=%s",
                step_label,
                model.model_name,
                [tool.name for tool in tool_specs],
            )
            last_user_message = next(
                (
                    item
                    for item in reversed(messages)
                    if item.get("role") == "user" and item.get("content")
                ),
                None,
            )
            if last_user_message:
                content = last_user_message.get("content")
                if isinstance(content, str) and content:
                    logger.debug(
                        "Agentic step %s last_user_len=%s",
                        step_label,
                        len(content),
                    )
            response = await provider.chat_with_tools(
                model.model_name, messages, tool_specs
            )
            last_usage = response.usage
            tool_calls = response.tool_calls or []
            logger.info(
                "Agentic step %s tool_calls=%s finish_reason=%s response_len=%s",
                step_label,
                len(tool_calls),
                response.finish_reason,
                len(response.content or ""),
            )
            if not tool_calls:
                logger.info(
                    "No tool calls returned at %s. response_len=%s",
                    step_label,
                    len(response.content or ""),
                )
                if response.content:
                    logger.info(
                        "No tool calls returned. response_snippet=%s",
                        response.content[:200],
                    )
                if response.content:
                    try:
                        parsed = json.loads(response.content)
                    except json.JSONDecodeError:
                        parsed = None
                    if isinstance(parsed, dict) and "prompt" in parsed:
                        logger.info(
                            "Falling back to generate_image with prompt JSON payload"
                        )
                        result = await tool_registry.execute(
                            "generate_image", {"prompt": parsed.get("prompt", "")}
                        )
                        if result.attachments:
                            attachments.extend(result.attachments)
                        return "", attachments, sources, image_usages, last_usage
                    await _emit("Answering", "start")
                    return response.content, attachments, sources, image_usages, last_usage
                logger.info("No tool calls and empty content; forcing final answer")
                messages.append(
                    {"role": "user", "content": "Please provide the final answer now."}
                )
                response = await provider.chat_with_tools(
                    model.model_name, messages, tool_specs
                )
                await _emit("Answering", "start")
                return response.content or "", attachments, sources, image_usages, last_usage
            logger.info("Tool calls: %s", [call.name for call in tool_calls])
            assistant_call_message = {
                "role": "assistant",
                "content": response.content or "",
                "tool_calls": [
                    {
                        "id": call.id,
                        "name": call.name,
                        "arguments": call.arguments,
                        "thought_signature": call.thought_signature,
                    }
                    for call in tool_calls
                ],
            }
            messages.append(assistant_call_message)
            for call in tool_calls:
                logger.info(
                    "Tool call start name=%s args_keys=%s",
                    call.name,
                    list(call.arguments.keys()) if isinstance(call.arguments, dict) else [],
                )
                if call.name == "web_search":
                    if search_calls >= MAX_WEB_SEARCH_CALLS:
                        result = ToolResult(
                            name="web_search",
                            output={"error": "Search limit reached"},
                        )
                    else:
                        labels = _labels_for_call(call.name, call.arguments)
                        for label in labels:
                            await _emit(label, "start")
                        search_calls += 1
                        result = await tool_registry.execute(call.name, call.arguments)
                elif call.name == "web_scrape":
                    if scrape_calls >= MAX_WEB_SCRAPE_CALLS:
                        result = ToolResult(
                            name="web_scrape",
                            output={"error": "Scrape limit reached"},
                        )
                    else:
                        labels = _labels_for_call(call.name, call.arguments)
                        for label in labels:
                            await _emit(label, "start")
                        scrape_calls += 1
                        result = await tool_registry.execute(call.name, call.arguments)
                elif call.name == "code_execution":
                    await _emit_tool_event(
                        {
                            "type": "code_execution",
                            "id": call.id,
                            "code": call.arguments.get("code", ""),
                            "output": {},
                        }
                    )
                    labels = _labels_for_call(call.name, call.arguments)
                    for label in labels:
                        await _emit(label, "start")
                    result = await tool_registry.execute(call.name, call.arguments)
                else:
                    labels = _labels_for_call(call.name, call.arguments)
                    for label in labels:
                        await _emit(label, "start")
                    result = await tool_registry.execute(call.name, call.arguments)
                if call.name == "code_execution":
                    logger.info(
                        "Code execution output keys=%s",
                        list(result.output.keys())
                        if isinstance(result.output, dict)
                        else [],
                    )
                    await _emit_tool_event(
                        {
                            "type": "code_execution",
                            "id": call.id,
                            "code": call.arguments.get("code", ""),
                            "output": result.output,
                        }
                    )
                if "error" in result.output:
                    logger.info(
                        "Tool error name=%s error=%s",
                        call.name,
                        result.output.get("error"),
                    )
                    error_text = result.output.get("error")
                    if isinstance(error_text, str) and error_text:
                        last_tool_error = error_text
                if result.output.get("requires_approval"):
                    last_tool_error = "Execution requires approval."
                if result.attachments:
                    attachments.extend(result.attachments)
                if call.name in {"generate_image", "edit_image"}:
                    model_id = result.output.get("model_id")
                    if model_id:
                        image_usages.append(
                            {
                                "model_id": model_id,
                                "prompt_tokens": 0,
                                "completion_tokens": 0,
                                "total_tokens": 0,
                                "input_tokens": 0,
                                "output_tokens": 0,
                                "cached_tokens": 0,
                                "thinking_tokens": 0,
                                "image_width": result.output.get("image_width"),
                                "image_height": result.output.get("image_height"),
                                "image_count": result.output.get("image_count"),
                                "image_format": result.output.get("image_format"),
                            }
                        )
                if call.name == "web_search":
                    queries = result.output.get("queries", []) or []
                    for query_result in queries:
                        for item in query_result.get("results", []) or []:
                            url = item.get("url")
                            if url:
                                sources.append(_source_item(url, item.get("title")))
                if call.name == "web_scrape":
                    for item in result.output.get("results", []) or []:
                        url = item.get("url")
                        if url:
                            sources.append(_source_item(url, item.get("title")))
                messages.append(
                    {
                        "role": "tool",
                        "name": call.name,
                        "tool_call_id": call.id,
                        "content": json.dumps(result.output),
                    }
                )
                for label in labels:
                    await _emit(label, "end")
        finally:
            await _emit(step_label, "end")
    unique = {item.get("url"): item for item in sources if isinstance(item, dict)}
    if not response.content:
        logger.info("Tool loop reached max steps; requesting final response")
        has_tool_history = any(
            message.get("role") == "tool" or message.get("tool_calls")
            for message in messages
            if isinstance(message, dict)
        )
        messages.append(
            {"role": "user", "content": "Please provide the final answer now."}
        )
        if has_tool_history and hasattr(provider, "chat_with_tools"):
            response = await provider.chat_with_tools(
                model.model_name, messages, tool_specs
            )
        else:
            response = await provider.chat(model.model_name, messages)
        await _emit("Answering", "start")
        if not response.content:
            fallback = last_tool_error or "No response generated."
            return fallback, attachments, _limit_sources(list(unique.values())), image_usages
    return response.content, attachments, _limit_sources(list(unique.values())), image_usages


class ChatCreateRequest(BaseModel):
    org_id: str
    model_id: str | None = None
    title: str | None = None


class ChatRead(BaseModel):
    id: str
    title: str | None
    model_id: str | None
    created_at: datetime
    last_activity_at: datetime


class ChatMessageAttachmentCreate(BaseModel):
    file_name: str
    content_type: str
    data_base64: str

    @field_validator("data_base64")
    @classmethod
    def _validate_attachment_size(cls, value: str) -> str:
        if not value:
            return value
        padding = value.count("=")
        estimated_bytes = max(len(value) * 3 // 4 - padding, 0)
        if estimated_bytes > settings.attachments_max_file_bytes:
            raise ValueError("Attachment exceeds maximum size.")
        return value


class ChatMessageAttachmentRead(BaseModel):
    id: str
    file_name: str
    content_type: str
    data_base64: str


class ChatMessageCreateRequest(BaseModel):
    content: str
    model_id: str | None = None
    stream: bool | None = False
    attachments: list[ChatMessageAttachmentCreate] | None = None
    reasoning_effort: str | None = None
    locale: str | None = None

    @model_validator(mode="after")
    def _validate_attachments(self) -> "ChatMessageCreateRequest":
        items = self.attachments or []
        if len(items) > settings.attachments_max_files:
            raise ValueError("Too many attachments.")
        total_bytes = 0
        for item in items:
            padding = item.data_base64.count("=")
            total_bytes += max(len(item.data_base64) * 3 // 4 - padding, 0)
        if total_bytes > settings.attachments_max_total_bytes:
            raise ValueError("Total attachments size exceeded.")
        return self


class ChatMessageRead(BaseModel):
    id: str
    role: str
    content: str
    created_at: datetime
    model_id: str | None = None
    model_name: str | None = None
    attachments: list[ChatMessageAttachmentRead] | None = None
    sources: list[dict] | None = None
    task_id: str | None = None
    generation_status: str | None = None


class ChatGenerationTaskRead(BaseModel):
    id: str
    chat_id: str
    user_message_id: str
    assistant_message_id: str
    status: str
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    model_id: str | None = None
    model_name: str | None = None


class ChatGenerationEventRead(BaseModel):
    id: str
    event_type: str
    payload: dict | None = None
    sequence: int
    created_at: datetime


class ChatMessageEditRequest(BaseModel):
    content: str
    attachments: list[ChatMessageAttachmentCreate] | None = None
    reasoning_effort: str | None = None
    locale: str | None = None

    @model_validator(mode="after")
    def _validate_attachments(self) -> "ChatMessageEditRequest":
        items = self.attachments or []
        if len(items) > settings.attachments_max_files:
            raise ValueError("Too many attachments.")
        total_bytes = 0
        for item in items:
            padding = item.data_base64.count("=")
            total_bytes += max(len(item.data_base64) * 3 // 4 - padding, 0)
        if total_bytes > settings.attachments_max_total_bytes:
            raise ValueError("Total attachments size exceeded.")
        return self


class ChatMessageEditResponse(BaseModel):
    user_message: ChatMessageRead
    assistant_message: ChatMessageRead


async def _stream_message_ws(
    websocket: WebSocket,
    session: Session,
    current_user: User,
    chat_id: str,
    payload: ChatMessageCreateRequest,
) -> None:
    try:
        chat_uuid = UUID(chat_id)
    except ValueError:
        await _ws_send_event(websocket, {"error": "Invalid chat id"})
        return

    chat = session.exec(select(Chat).where(Chat.id == chat_uuid)).first()
    if not chat or chat.is_deleted:
        await _ws_send_event(websocket, {"error": "Chat not found"})
        return

    require_org_member(
        session, chat.org_id, current_user.id, is_super_admin=current_user.is_super_admin
    )

    model_id = chat.model_id
    if payload.model_id:
        try:
            model_id = UUID(payload.model_id)
        except ValueError:
            await _ws_send_event(websocket, {"error": "Invalid model id"})
            return
        chat.model_id = model_id

    if not model_id:
        await _ws_send_event(websocket, {"error": "Chat model not set"})
        return

    model = session.exec(select(ChatModel).where(ChatModel.id == model_id)).first()
    if not model:
        await _ws_send_event(websocket, {"error": "Model not found"})
        return
    enabled = session.exec(
        select(OrgModel).where(
            OrgModel.org_id == chat.org_id,
            OrgModel.model_id == model.id,
            OrgModel.is_enabled.is_(True),
        )
    ).first()
    if not enabled:
        await _ws_send_event(
            websocket, {"error": "Model is not enabled for this organization"}
        )
        return

    user_message = ChatMessage(
        chat_id=chat.id,
        role="user",
        content=payload.content,
        status="done",
    )
    session.add(user_message)
    session.commit()
    session.refresh(user_message)

    attachments = []
    if payload.attachments:
        for item in payload.attachments:
            attachments.append(
                ChatMessageAttachment(
                    message_id=user_message.id,
                    file_name=item.file_name,
                    content_type=item.content_type,
                    data_base64=item.data_base64,
                )
            )
        session.add_all(attachments)
        session.commit()

    await _ws_send_event(websocket, {"user_message_id": str(user_message.id)})

    assistant_message = ChatMessage(
        chat_id=chat.id,
        role="assistant",
        content="",
        model_id=model.id,
        status="generating",
        started_at=datetime.utcnow(),
    )
    session.add(assistant_message)
    session.commit()
    session.refresh(assistant_message)

    task = ChatGenerationTask(
        chat_id=chat.id,
        user_message_id=user_message.id,
        assistant_message_id=assistant_message.id,
        status=GenerationStatus.queued,
        metadata_json={
            "model_id": str(model.id),
            "model_name": model.display_name,
            "locale": payload.locale,
            "reasoning_effort": payload.reasoning_effort,
        },
    )
    session.add(task)
    session.commit()
    session.refresh(task)

    await _ws_send_event(
        websocket,
        {"task_id": str(task.id), "assistant_message_id": str(assistant_message.id)},
    )
    _enqueue_generation_task(task.id)
    await _stream_task_events_ws(websocket, task.id)
    return

    history = session.exec(
        select(ChatMessage)
        .where(ChatMessage.chat_id == chat.id)
        .where(ChatMessage.is_current.is_(True))
        .order_by(ChatMessage.created_at)
    ).all()
    history_attachments = session.exec(
        select(ChatMessageAttachment).where(
            ChatMessageAttachment.message_id.in_([message.id for message in history])
        )
    ).all()
    attachments_by_message: dict[UUID, list[ChatMessageAttachment]] = {}
    for attachment in history_attachments:
        attachments_by_message.setdefault(attachment.message_id, []).append(attachment)

    def build_messages() -> list[dict]:
        items: list[dict] = []
        for msg in history:
            if msg.role != "user":
                items.append({"role": msg.role, "content": msg.content})
                continue
            msg_attachments = attachments_by_message.get(msg.id, [])
            if not msg_attachments:
                items.append({"role": msg.role, "content": msg.content})
                continue
            image_attachments = [
                attachment
                for attachment in msg_attachments
                if attachment.content_type.startswith("image/")
            ]
            file_attachments = [
                attachment
                for attachment in msg_attachments
                if not attachment.content_type.startswith("image/")
            ]
            if image_attachments and model.provider not in {"openai", "azure", "gemini"}:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Images are not supported for this model provider",
                )
            attachment_lines = _attachment_lines(msg_attachments)
            if not image_attachments:
                text = msg.content or ""
                if attachment_lines:
                    text += (
                        "\n\nAttachments (available in /inputs for code execution):\n"
                        + "\n".join(attachment_lines)
                    )
                items.append({"role": msg.role, "content": text})
                continue
            content_parts: list[dict] = []
            if msg.content:
                content_parts.append({"type": "text", "text": msg.content})
            if attachment_lines:
                content_parts.append(
                    {
                        "type": "text",
                        "text": "Attachments (available in /inputs for code execution):\n"
                        + "\n".join(attachment_lines),
                    }
                )
            for attachment in image_attachments:
                content_parts.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{attachment.content_type};base64,{attachment.data_base64}"
                        },
                    }
                )
            items.append({"role": msg.role, "content": content_parts})
        return items

    messages = _truncate_messages(
        _prepend_tool_guidance(build_messages(), locale=payload.locale),
        token_limit=model.context_length,
    )

    org = session.exec(select(Org).where(Org.id == chat.org_id)).first()
    if not org:
        await _ws_send_event(websocket, {"error": "Org not found"})
        return

    provider_config = require_provider_enabled(session, chat.org_id, model.provider)
    config = None
    if provider_config and provider_config.config_json:
        try:
            config = json.loads(provider_config.config_json)
        except json.JSONDecodeError:
            pass
    prompt_cache_key = f"chat:{chat.id}"
    provider = get_provider(
        model.provider,
        api_key=provider_config.api_key_override if provider_config else None,
        base_url=provider_config.base_url_override if provider_config else None,
        endpoint=provider_config.endpoint_override if provider_config else None,
        reasoning_effort=payload.reasoning_effort or model.reasoning_effort,
        prompt_cache_key=prompt_cache_key,
        prompt_cache_retention=settings.openai_prompt_cache_retention,
        config=config,
    )
    grounding_enabled = _grounding_enabled(org, model.provider)
    tool_registry = _build_tool_registry(
        session,
        chat.org_id,
        chat_id=chat.id,
        preferred_provider=model.provider,
        web_tools_enabled=not grounding_enabled,
        web_search_enabled=org.web_search_enabled,
        web_scrape_enabled=org.web_scrape_enabled,
        exec_policy=org.exec_policy,
        exec_network_enabled=org.exec_network_enabled,
        locale=payload.locale,
    )
    tool_attachments: list[dict] | None = None
    image_usages: list[dict] = []
    tool_sources: list[dict] | None = None

    await _ws_send_event(websocket, {"activity": {"label": "Thinking", "state": "start"}})

    if _is_image_output_model(model):
        await _ws_send_event(
            websocket, {"activity": {"label": "Generating image", "state": "start"}}
        )
        image_result = await generate_image(
            ImageToolContext(session=session, org_id=str(chat.org_id)),
            prompt=payload.content,
            model_override=model,
        )
        assistant_message = ChatMessage(
            chat_id=chat.id,
            role="assistant",
            content="",
            model_id=model.id,
        )
        session.add(assistant_message)
        session.commit()
        session.refresh(assistant_message)
        if image_result.attachments:
            session.add_all(
                [
                    ChatMessageAttachment(
                        message_id=assistant_message.id,
                        file_name=item["file_name"],
                        content_type=item["content_type"],
                        data_base64=item["data_base64"],
                    )
                    for item in image_result.attachments
                ]
            )
            session.commit()
        usage_event = UsageEvent(
            org_id=chat.org_id,
            user_id=current_user.id,
            chat_id=chat.id,
            message_id=assistant_message.id,
            model_id=model.id,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            input_tokens=0,
            output_tokens=0,
            cached_tokens=0,
            thinking_tokens=0,
            image_width=None,
            image_height=None,
            image_count=None,
            image_format=None,
        )
        session.add(usage_event)
        session.commit()
        await _ws_send_event(
            websocket, {"activity": {"label": "Generating image", "state": "end"}}
        )
        await _ws_send_event(
            websocket,
            {
                "done": True,
                "message_id": str(assistant_message.id),
                "content": "",
                "model_name": model.display_name,
                "model_id": str(model.id),
                "attachments": image_result.attachments or [],
            },
        )
        return

    if grounding_enabled and hasattr(provider, "chat_grounded"):
        await _ws_send_event(
            websocket, {"activity": {"label": "Searching the web", "state": "start"}}
        )
        response = await provider.chat_grounded(model.model_name, messages)
        tool_sources = await _normalize_sources(response.sources or [])
        await _ws_send_event(
            websocket, {"activity": {"label": "Searching the web", "state": "end"}}
        )
        assistant_message = ChatMessage(
            chat_id=chat.id,
            role="assistant",
            content=response.content,
            model_id=model.id,
            sources=tool_sources or None,
        )
        session.add(assistant_message)
        session.commit()
        session.refresh(assistant_message)

        prompt_tokens = response.usage.prompt_tokens
        completion_tokens = response.usage.completion_tokens
        total_tokens = response.usage.total_tokens
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        usage_event = UsageEvent(
            org_id=chat.org_id,
            user_id=current_user.id,
            chat_id=chat.id,
            message_id=assistant_message.id,
            model_id=model.id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=response.usage.cached_tokens,
            thinking_tokens=response.usage.thinking_tokens,
        )
        session.add(usage_event)
        session.commit()
        await _maybe_update_chat_title(
            session=session,
            chat=chat,
            provider=provider,
            model=model,
            history=history + [assistant_message],
        )
        await _ws_send_event(
            websocket,
            {
                "done": True,
                "message_id": str(assistant_message.id),
                "content": response.content,
                "model_name": model.display_name,
                "model_id": str(model.id),
                "sources": tool_sources or [],
            },
        )
        return

    if tool_registry and hasattr(provider, "chat_with_tools"):
        await _ws_send_event(
            websocket, {"activity": {"label": "Using tools", "state": "start"}}
        )
        send_stream, receive_stream = anyio.create_memory_object_stream(50)
        tool_send_stream, tool_receive_stream = anyio.create_memory_object_stream(50)

        async def _forward_activity() -> None:
            async with receive_stream:
                async for item in receive_stream:
                    await _ws_send_event(websocket, {"activity": item})

        async def _forward_tool_events() -> None:
            async with tool_receive_stream:
                async for item in tool_receive_stream:
                    await _ws_send_event(websocket, {"tool_event": item})

        try:
            async with anyio.create_task_group() as tg:
                tg.start_soon(_forward_activity)
                tg.start_soon(_forward_tool_events)
                content, tool_attachments, tool_sources, image_usages, last_usage = (
                    await _run_agentic_loop(
                    provider=provider,
                    model=model,
                    messages=messages,
                    tool_registry=tool_registry,
                    activity_sender=send_stream,
                    tool_event_sender=tool_send_stream,
                    )
                )
                await send_stream.aclose()
                await tool_send_stream.aclose()
        except Exception as exc:
            logger.exception("Tool streaming failed")
            await _ws_send_event(websocket, {"error": _format_model_error(exc)})
            await _ws_send_event(
                websocket, {"activity": {"label": "Thinking", "state": "end"}}
            )
            return

        tool_sources = await _normalize_sources(tool_sources)
        assistant_message = ChatMessage(
            chat_id=chat.id,
            role="assistant",
            content=content,
            model_id=model.id,
            sources=tool_sources or None,
        )
        session.add(assistant_message)
        session.commit()
        session.refresh(assistant_message)
        if tool_attachments:
            session.add_all(
                [
                    ChatMessageAttachment(
                        message_id=assistant_message.id,
                        file_name=item["file_name"],
                        content_type=item["content_type"],
                        data_base64=item["data_base64"],
                    )
                    for item in tool_attachments
                ]
            )
        session.commit()
        usage = last_usage or ChatUsage(0, 0, 0, 0, 0, 0, 0)
        usage_event = UsageEvent(
            org_id=chat.org_id,
            user_id=current_user.id,
            chat_id=chat.id,
            message_id=assistant_message.id,
            model_id=model.id,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cached_tokens=usage.cached_tokens,
            thinking_tokens=usage.thinking_tokens,
            image_width=None,
            image_height=None,
            image_count=None,
            image_format=None,
        )
        session.add(usage_event)
        session.commit()
        await _maybe_update_chat_title(
            session=session,
            chat=chat,
            provider=provider,
            model=model,
            history=history + [assistant_message],
        )
        await _ws_send_event(
            websocket, {"activity": {"label": "Using tools", "state": "end"}}
        )
        await _ws_send_event(
            websocket,
            {
                "done": True,
                "message_id": str(assistant_message.id),
                "content": content,
                "model_name": model.display_name,
                "model_id": str(model.id),
                "attachments": tool_attachments or [],
                "sources": tool_sources or [],
            },
        )
        return

    assistant_content = ""
    usage = ChatUsage(0, 0, 0, 0, 0, 0, 0)
    try:
        response = await provider.chat(model.model_name, messages)
        assistant_content = response.content or ""
        usage = response.usage
    except Exception as exc:
        logger.exception("Chat request failed")
        await _ws_send_event(websocket, {"error": _format_model_error(exc)})
        await _ws_send_event(
            websocket, {"activity": {"label": "Thinking", "state": "end"}}
        )
        return

    assistant_message = ChatMessage(
        chat_id=chat.id,
        role="assistant",
        content=assistant_content,
        model_id=model.id,
    )
    session.add(assistant_message)
    session.commit()
    session.refresh(assistant_message)

    prompt_tokens = usage.prompt_tokens
    completion_tokens = usage.completion_tokens
    total_tokens = usage.total_tokens
    input_tokens = usage.input_tokens
    output_tokens = usage.output_tokens
    usage_event = UsageEvent(
        org_id=chat.org_id,
        user_id=current_user.id,
        chat_id=chat.id,
        message_id=assistant_message.id,
        model_id=model.id,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=usage.cached_tokens,
        thinking_tokens=usage.thinking_tokens,
    )
    session.add(usage_event)
    session.commit()
    if image_usages:
        for item in image_usages:
            session.add(
                UsageEvent(
                    org_id=chat.org_id,
                    user_id=current_user.id,
                    chat_id=chat.id,
                    message_id=assistant_message.id,
                    model_id=UUID(item["model_id"]),
                    prompt_tokens=item["prompt_tokens"],
                    completion_tokens=item["completion_tokens"],
                    total_tokens=item["total_tokens"],
                    input_tokens=item["input_tokens"],
                    output_tokens=item["output_tokens"],
                    cached_tokens=item["cached_tokens"],
                    thinking_tokens=item["thinking_tokens"],
                                image_width=item.get("image_width"),
                                image_height=item.get("image_height"),
                                image_count=item.get("image_count"),
                                image_format=item.get("image_format"),
                )
            )
        session.commit()
    await _maybe_update_chat_title(
        session=session,
        chat=chat,
        provider=provider,
        model=model,
        history=history + [assistant_message],
    )

    await _ws_send_event(websocket, {"activity": {"label": "Thinking", "state": "end"}})
    await _ws_send_event(
        websocket,
        {
            "done": True,
            "message_id": str(assistant_message.id),
            "content": assistant_content,
            "model_name": model.display_name,
            "model_id": str(model.id),
        },
    )


async def _stream_edit_ws(
    websocket: WebSocket,
    session: Session,
    current_user: User,
    chat_id: str,
    message_id: str,
    payload: ChatMessageEditRequest,
) -> None:
    try:
        chat_uuid = UUID(chat_id)
        message_uuid = UUID(message_id)
    except ValueError:
        await _ws_send_event(websocket, {"error": "Invalid id"})
        return

    chat = session.exec(select(Chat).where(Chat.id == chat_uuid)).first()
    if not chat or chat.is_deleted:
        await _ws_send_event(websocket, {"error": "Chat not found"})
        return
    if chat.user_id != current_user.id:
        await _ws_send_event(websocket, {"error": "Cannot edit this message"})
        return

    require_org_member(
        session, chat.org_id, current_user.id, is_super_admin=current_user.is_super_admin
    )

    message = session.exec(
        select(ChatMessage).where(
            ChatMessage.id == message_uuid, ChatMessage.chat_id == chat.id
        )
    ).first()
    if not message:
        await _ws_send_event(websocket, {"error": "Message not found"})
        return

    model_id = chat.model_id
    if not model_id:
        await _ws_send_event(websocket, {"error": "Chat model not set"})
        return

    model = session.exec(select(ChatModel).where(ChatModel.id == model_id)).first()
    if not model:
        await _ws_send_event(websocket, {"error": "Model not found"})
        return
    enabled = session.exec(
        select(OrgModel).where(
            OrgModel.org_id == chat.org_id,
            OrgModel.model_id == model.id,
            OrgModel.is_enabled.is_(True),
        )
    ).first()
    if not enabled:
        await _ws_send_event(
            websocket, {"error": "Model is not enabled for this organization"}
        )
        return

    to_hide = session.exec(
        select(ChatMessage)
        .where(ChatMessage.chat_id == chat.id)
        .where(ChatMessage.is_current.is_(True))
        .where(ChatMessage.created_at >= message.created_at)
    ).all()
    for item in to_hide:
        item.is_current = False
        session.add(item)
    session.commit()

    new_message = ChatMessage(
        chat_id=chat.id,
        role="user",
        content=payload.content,
        parent_id=message.id,
        branch_id=uuid4(),
        is_current=True,
        status="done",
    )
    session.add(new_message)
    session.commit()
    session.refresh(new_message)

    if payload.attachments is None:
        prev_attachments = session.exec(
            select(ChatMessageAttachment).where(
                ChatMessageAttachment.message_id == message.id
            )
        ).all()
        if prev_attachments:
            session.add_all(
                [
                    ChatMessageAttachment(
                        message_id=new_message.id,
                        file_name=attachment.file_name,
                        content_type=attachment.content_type,
                        data_base64=attachment.data_base64,
                    )
                    for attachment in prev_attachments
                ]
            )
            session.commit()
    else:
        if payload.attachments:
            session.add_all(
                [
                    ChatMessageAttachment(
                        message_id=new_message.id,
                        file_name=attachment.file_name,
                        content_type=attachment.content_type,
                        data_base64=attachment.data_base64,
                    )
                    for attachment in payload.attachments
                ]
            )
            session.commit()

    await _ws_send_event(
        websocket,
        {"edited_message_id": message_id, "user_message_id": str(new_message.id)},
    )

    assistant_message = ChatMessage(
        chat_id=chat.id,
        role="assistant",
        content="",
        model_id=model.id,
        status="generating",
        started_at=datetime.utcnow(),
    )
    session.add(assistant_message)
    session.commit()
    session.refresh(assistant_message)

    task = ChatGenerationTask(
        chat_id=chat.id,
        user_message_id=new_message.id,
        assistant_message_id=assistant_message.id,
        status=GenerationStatus.queued,
        metadata_json={
            "model_id": str(model.id),
            "model_name": model.display_name,
            "locale": payload.locale,
            "reasoning_effort": payload.reasoning_effort,
        },
    )
    session.add(task)
    session.commit()
    session.refresh(task)

    await _ws_send_event(
        websocket,
        {"task_id": str(task.id), "assistant_message_id": str(assistant_message.id)},
    )
    _enqueue_generation_task(task.id)
    await _stream_task_events_ws(websocket, task.id)
    return

    history = session.exec(
        select(ChatMessage)
        .where(ChatMessage.chat_id == chat.id)
        .where(ChatMessage.is_current.is_(True))
        .order_by(ChatMessage.created_at)
    ).all()
    history_attachments = session.exec(
        select(ChatMessageAttachment).where(
            ChatMessageAttachment.message_id.in_([message.id for message in history])
        )
    ).all()
    attachments_by_message: dict[UUID, list[ChatMessageAttachment]] = {}
    for attachment in history_attachments:
        attachments_by_message.setdefault(attachment.message_id, []).append(attachment)

    def build_messages() -> list[dict]:
        items: list[dict] = []
        for msg in history:
            if msg.role != "user":
                items.append({"role": msg.role, "content": msg.content})
                continue
            msg_attachments = attachments_by_message.get(msg.id, [])
            if not msg_attachments:
                items.append({"role": msg.role, "content": msg.content})
                continue
            image_attachments = [
                attachment
                for attachment in msg_attachments
                if attachment.content_type.startswith("image/")
            ]
            file_attachments = [
                attachment
                for attachment in msg_attachments
                if not attachment.content_type.startswith("image/")
            ]
            if image_attachments and model.provider not in {"openai", "azure", "gemini"}:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Images are not supported for this model provider",
                )
            attachment_lines = _attachment_lines(msg_attachments)
            if not image_attachments:
                text = msg.content or ""
                if attachment_lines:
                    text += (
                        "\n\nAttachments (available in /inputs for code execution):\n"
                        + "\n".join(attachment_lines)
                    )
                items.append({"role": msg.role, "content": text})
                continue
            content_parts: list[dict] = []
            if msg.content:
                content_parts.append({"type": "text", "text": msg.content})
            if attachment_lines:
                content_parts.append(
                    {
                        "type": "text",
                        "text": "Attachments (available in /inputs for code execution):\n"
                        + "\n".join(attachment_lines),
                    }
                )
            for attachment in image_attachments:
                content_parts.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{attachment.content_type};base64,{attachment.data_base64}"
                        },
                    }
                )
            items.append({"role": msg.role, "content": content_parts})
        return items

    messages = _truncate_messages(
        _prepend_tool_guidance(build_messages(), locale=payload.locale),
        token_limit=model.context_length,
    )

    org = session.exec(select(Org).where(Org.id == chat.org_id)).first()
    if not org:
        await _ws_send_event(websocket, {"error": "Org not found"})
        return

    provider_config = require_provider_enabled(session, chat.org_id, model.provider)
    config = None
    if provider_config and provider_config.config_json:
        try:
            config = json.loads(provider_config.config_json)
        except json.JSONDecodeError:
            pass
    prompt_cache_key = f"chat:{chat.id}"
    provider = get_provider(
        model.provider,
        api_key=provider_config.api_key_override if provider_config else None,
        base_url=provider_config.base_url_override if provider_config else None,
        endpoint=provider_config.endpoint_override if provider_config else None,
        reasoning_effort=payload.reasoning_effort or model.reasoning_effort,
        prompt_cache_key=prompt_cache_key,
        prompt_cache_retention=settings.openai_prompt_cache_retention,
        config=config,
    )
    grounding_enabled = _grounding_enabled(org, model.provider)
    tool_registry = _build_tool_registry(
        session,
        chat.org_id,
        chat_id=chat.id,
        preferred_provider=model.provider,
        web_tools_enabled=not grounding_enabled,
        web_search_enabled=org.web_search_enabled,
        web_scrape_enabled=org.web_scrape_enabled,
        exec_policy=org.exec_policy,
        exec_network_enabled=org.exec_network_enabled,
        locale=payload.locale,
    )

    if _is_image_output_model(model):
        image_result = await generate_image(
            ImageToolContext(session=session, org_id=str(chat.org_id)),
            prompt=payload.content,
            model_override=model,
        )
        assistant_message = ChatMessage(
            chat_id=chat.id,
            role="assistant",
            content="",
            model_id=model.id,
            is_current=True,
        )
        session.add(assistant_message)
        session.commit()
        session.refresh(assistant_message)
        if image_result.attachments:
            session.add_all(
                [
                    ChatMessageAttachment(
                        message_id=assistant_message.id,
                        file_name=item["file_name"],
                        content_type=item["content_type"],
                        data_base64=item["data_base64"],
                    )
                    for item in image_result.attachments
                ]
            )
            session.commit()
        usage_event = UsageEvent(
            org_id=chat.org_id,
            user_id=current_user.id,
            chat_id=chat.id,
            message_id=assistant_message.id,
            model_id=model.id,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            input_tokens=0,
            output_tokens=0,
            cached_tokens=0,
            thinking_tokens=0,
            image_width=None,
            image_height=None,
            image_count=None,
            image_format=None,
        )
        session.add(usage_event)
        session.commit()
        await _maybe_update_chat_title(
            session=session,
            chat=chat,
            provider=provider,
            model=model,
            history=history + [assistant_message],
        )
        await _ws_send_event(
            websocket,
            {
                "done": True,
                "message_id": str(assistant_message.id),
                "content": "",
                "model_name": model.display_name,
                "model_id": str(model.id),
                "attachments": image_result.attachments or [],
            },
        )
        return

    if grounding_enabled and hasattr(provider, "chat_grounded"):
        await _ws_send_event(
            websocket, {"activity": {"label": "Searching the web", "state": "start"}}
        )
        response = await provider.chat_grounded(model.model_name, messages)
        tool_sources = await _normalize_sources(response.sources or [])
        await _ws_send_event(
            websocket, {"activity": {"label": "Searching the web", "state": "end"}}
        )
        assistant_message = ChatMessage(
            chat_id=chat.id,
            role="assistant",
            content=response.content,
            model_id=model.id,
            sources=tool_sources or None,
        )
        session.add(assistant_message)
        session.commit()
        session.refresh(assistant_message)
        usage_event = UsageEvent(
            org_id=chat.org_id,
            user_id=current_user.id,
            chat_id=chat.id,
            message_id=assistant_message.id,
            model_id=model.id,
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
            total_tokens=response.usage.total_tokens,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cached_tokens=response.usage.cached_tokens,
            thinking_tokens=response.usage.thinking_tokens,
        )
        session.add(usage_event)
        session.commit()
        await _maybe_update_chat_title(
            session=session,
            chat=chat,
            provider=provider,
            model=model,
            history=history + [assistant_message],
        )
        await _ws_send_event(
            websocket,
            {
                "done": True,
                "message_id": str(assistant_message.id),
                "content": response.content,
                "model_name": model.display_name,
                "model_id": str(model.id),
                "sources": tool_sources or [],
            },
        )
        return

    if tool_registry and hasattr(provider, "chat_with_tools"):
        send_stream, receive_stream = anyio.create_memory_object_stream(50)
        tool_send_stream, tool_receive_stream = anyio.create_memory_object_stream(50)

        async def _forward_activity() -> None:
            async with receive_stream:
                async for item in receive_stream:
                    await _ws_send_event(websocket, {"activity": item})

        async def _forward_tool_events() -> None:
            async with tool_receive_stream:
                async for item in tool_receive_stream:
                    await _ws_send_event(websocket, {"tool_event": item})

        try:
            async with anyio.create_task_group() as tg:
                tg.start_soon(_forward_activity)
                tg.start_soon(_forward_tool_events)
                content, tool_attachments, tool_sources, image_usages, last_usage = (
                    await _run_agentic_loop(
                    provider=provider,
                    model=model,
                    messages=messages,
                    tool_registry=tool_registry,
                    activity_sender=send_stream,
                    tool_event_sender=tool_send_stream,
                    )
                )
                await send_stream.aclose()
                await tool_send_stream.aclose()
        except Exception as exc:
            logger.exception("Edit tool streaming failed")
            await _ws_send_event(websocket, {"error": _format_model_error(exc)})
            return

        tool_sources = await _normalize_sources(tool_sources)
        assistant_message = ChatMessage(
            chat_id=chat.id,
            role="assistant",
            content=content,
            model_id=model.id,
            sources=tool_sources or None,
            is_current=True,
        )
        session.add(assistant_message)
        session.commit()
        session.refresh(assistant_message)
        if tool_attachments:
            session.add_all(
                [
                    ChatMessageAttachment(
                        message_id=assistant_message.id,
                        file_name=item["file_name"],
                        content_type=item["content_type"],
                        data_base64=item["data_base64"],
                    )
                    for item in tool_attachments
                ]
            )
            session.commit()
        usage = last_usage or ChatUsage(0, 0, 0, 0, 0, 0, 0)
        usage_event = UsageEvent(
            org_id=chat.org_id,
            user_id=current_user.id,
            chat_id=chat.id,
            message_id=assistant_message.id,
            model_id=model.id,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cached_tokens=usage.cached_tokens,
            thinking_tokens=usage.thinking_tokens,
            image_width=None,
            image_height=None,
            image_count=None,
            image_format=None,
        )
        session.add(usage_event)
        session.commit()
        if image_usages:
            for item in image_usages:
                session.add(
                    UsageEvent(
                        org_id=chat.org_id,
                        user_id=current_user.id,
                        chat_id=chat.id,
                        message_id=assistant_message.id,
                        model_id=UUID(item["model_id"]),
                        prompt_tokens=item["prompt_tokens"],
                        completion_tokens=item["completion_tokens"],
                        total_tokens=item["total_tokens"],
                        input_tokens=item["input_tokens"],
                        output_tokens=item["output_tokens"],
                        cached_tokens=item["cached_tokens"],
                        thinking_tokens=item["thinking_tokens"],
                        image_width=item.get("image_width"),
                        image_height=item.get("image_height"),
                        image_count=item.get("image_count"),
                        image_format=item.get("image_format"),
                    )
                )
            session.commit()
        await _maybe_update_chat_title(
            session=session,
            chat=chat,
            provider=provider,
            model=model,
            history=history + [assistant_message],
        )
        await _ws_send_event(
            websocket,
            {
                "done": True,
                "message_id": str(assistant_message.id),
                "content": content,
                "model_name": model.display_name,
                "model_id": str(model.id),
                "attachments": tool_attachments or [],
                "sources": tool_sources or [],
            },
        )
        return

    assistant_content = ""
    usage = ChatUsage(0, 0, 0, 0, 0, 0, 0)
    try:
        response = await provider.chat(model.model_name, messages)
        assistant_content = response.content or ""
        usage = response.usage
    except Exception as exc:
        logger.exception("Edit chat request failed")
        await _ws_send_event(websocket, {"error": _format_model_error(exc)})
        return

    assistant_message = ChatMessage(
        chat_id=chat.id,
        role="assistant",
        content=assistant_content,
        model_id=model.id,
        is_current=True,
    )
    session.add(assistant_message)
    session.commit()
    session.refresh(assistant_message)

    prompt_tokens = usage.prompt_tokens
    completion_tokens = usage.completion_tokens
    total_tokens = usage.total_tokens
    input_tokens = usage.input_tokens
    output_tokens = usage.output_tokens
    usage_event = UsageEvent(
        org_id=chat.org_id,
        user_id=current_user.id,
        chat_id=chat.id,
        message_id=assistant_message.id,
        model_id=model.id,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=usage.cached_tokens,
        thinking_tokens=usage.thinking_tokens,
    )
    session.add(usage_event)
    session.commit()

    await _maybe_update_chat_title(
        session=session,
        chat=chat,
        provider=provider,
        model=model,
        history=history + [assistant_message],
    )
    await _ws_send_event(
        websocket,
        {
            "done": True,
            "message_id": str(assistant_message.id),
            "content": assistant_content,
            "model_name": model.display_name,
            "model_id": str(model.id),
        },
    )


@router.post("", response_model=ChatRead)
def create_chat(
    payload: ChatCreateRequest,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChatRead:
    try:
        org_id = UUID(payload.org_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid org id"
        ) from exc

    require_org_member(
        session, org_id, current_user.id, is_super_admin=current_user.is_super_admin
    )

    model_id = None
    if payload.model_id:
        try:
            model_id = UUID(payload.model_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid model id"
            ) from exc

    chat = Chat(
        org_id=org_id,
        user_id=current_user.id,
        model_id=model_id,
        title=payload.title,
    )
    session.add(chat)
    session.commit()
    session.refresh(chat)
    return ChatRead(
        id=str(chat.id),
        title=chat.title,
        model_id=str(chat.model_id) if chat.model_id else None,
        created_at=chat.created_at,
        last_activity_at=chat.created_at,
    )


@router.get("", response_model=list[ChatRead])
def list_chats(
    org_id: str,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ChatRead]:
    try:
        org_uuid = UUID(org_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid org id"
        ) from exc

    require_org_member(
        session, org_uuid, current_user.id, is_super_admin=current_user.is_super_admin
    )

    last_activity_subq = (
        select(
            ChatMessage.chat_id,
            func.max(ChatMessage.created_at).label("last_activity_at"),
        )
        .group_by(ChatMessage.chat_id)
        .subquery()
    )
    chats = session.exec(
        select(Chat, last_activity_subq.c.last_activity_at)
        .outerjoin(last_activity_subq, last_activity_subq.c.chat_id == Chat.id)
        .where(
            Chat.org_id == org_uuid,
            Chat.user_id == current_user.id,
            Chat.is_deleted.is_(False),
        )
    ).all()
    return [
        ChatRead(
            id=str(chat.id),
            title=chat.title,
            model_id=str(chat.model_id) if chat.model_id else None,
            created_at=chat.created_at,
            last_activity_at=last_activity_at or chat.created_at,
        )
        for chat, last_activity_at in chats
    ]


@router.get("/{chat_id}/messages", response_model=list[ChatMessageRead])
def list_messages(
    chat_id: str,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ChatMessageRead]:
    try:
        chat_uuid = UUID(chat_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid chat id"
        ) from exc

    chat = session.exec(select(Chat).where(Chat.id == chat_uuid)).first()
    if not chat or chat.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")
    if chat.user_id != current_user.id and not current_user.is_super_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Cannot access this chat"
        )

    org = session.exec(select(Org).where(Org.id == chat.org_id)).first()
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found"
        )

    require_org_member(
        session, chat.org_id, current_user.id, is_super_admin=current_user.is_super_admin
    )

    messages = session.exec(
        select(ChatMessage)
        .where(ChatMessage.chat_id == chat_uuid)
        .where(ChatMessage.is_current.is_(True))
        .order_by(ChatMessage.created_at)
    ).all()
    model_ids = {message.model_id for message in messages if message.model_id}
    models = (
        session.exec(select(ChatModel).where(ChatModel.id.in_(model_ids))).all()
        if model_ids
        else []
    )
    model_map = {model.id: model.display_name for model in models}
    attachments = []
    if messages:
        attachments = session.exec(
            select(ChatMessageAttachment).where(
                ChatMessageAttachment.message_id.in_(
                    [message.id for message in messages]
                )
            )
        ).all()
    task_map: dict[UUID, ChatGenerationTask] = {}
    if messages:
        tasks = session.exec(
            select(ChatGenerationTask).where(
                ChatGenerationTask.assistant_message_id.in_(
                    [message.id for message in messages]
                )
            )
        ).all()
        task_map = {task.assistant_message_id: task for task in tasks}
    attachments_by_message: dict[UUID, list[ChatMessageAttachmentRead]] = {}
    for attachment in attachments:
        attachments_by_message.setdefault(attachment.message_id, []).append(
            ChatMessageAttachmentRead(
                id=str(attachment.id),
                file_name=attachment.file_name,
                content_type=attachment.content_type,
                data_base64=attachment.data_base64,
            )
        )
    return [
        ChatMessageRead(
            id=str(message.id),
            role=message.role,
            content=message.content,
            created_at=message.created_at,
            model_id=str(message.model_id) if message.model_id else None,
            model_name=model_map.get(message.model_id),
            attachments=attachments_by_message.get(message.id),
            sources=message.sources,
            task_id=str(task_map[message.id].id) if message.id in task_map else None,
            generation_status=task_map[message.id].status.value
            if message.id in task_map
            else None,
        )
        for message in messages
    ]


@router.get("/{chat_id}/generation", response_model=list[ChatGenerationTaskRead])
def list_generation_tasks(
    chat_id: str,
    active_only: bool = True,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ChatGenerationTaskRead]:
    try:
        chat_uuid = UUID(chat_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid chat id"
        ) from exc
    chat = session.exec(select(Chat).where(Chat.id == chat_uuid)).first()
    if not chat or chat.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")
    require_org_member(
        session, chat.org_id, current_user.id, is_super_admin=current_user.is_super_admin
    )
    query = select(ChatGenerationTask).where(ChatGenerationTask.chat_id == chat.id)
    if active_only:
        query = query.where(
            ChatGenerationTask.status.notin_(
                [
                    GenerationStatus.completed,
                    GenerationStatus.failed,
                    GenerationStatus.cancelled,
                ]
            )
        )
    tasks = session.exec(query.order_by(ChatGenerationTask.created_at)).all()
    return [
        ChatGenerationTaskRead(
            id=str(task.id),
            chat_id=str(task.chat_id),
            user_message_id=str(task.user_message_id),
            assistant_message_id=str(task.assistant_message_id),
            status=task.status.value,
            error=task.error,
            created_at=task.created_at,
            started_at=task.started_at,
            completed_at=task.completed_at,
            model_id=(task.metadata_json or {}).get("model_id"),
            model_name=(task.metadata_json or {}).get("model_name"),
        )
        for task in tasks
    ]


@router.get("/{chat_id}/generation/{task_id}", response_model=ChatGenerationTaskRead)
def get_generation_task(
    chat_id: str,
    task_id: str,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChatGenerationTaskRead:
    try:
        chat_uuid = UUID(chat_id)
        task_uuid = UUID(task_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid id"
        ) from exc
    task = session.exec(select(ChatGenerationTask).where(ChatGenerationTask.id == task_uuid)).first()
    if not task or task.chat_id != chat_uuid:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    chat = session.exec(select(Chat).where(Chat.id == chat_uuid)).first()
    if not chat or chat.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")
    require_org_member(
        session, chat.org_id, current_user.id, is_super_admin=current_user.is_super_admin
    )
    return ChatGenerationTaskRead(
        id=str(task.id),
        chat_id=str(task.chat_id),
        user_message_id=str(task.user_message_id),
        assistant_message_id=str(task.assistant_message_id),
        status=task.status.value,
        error=task.error,
        created_at=task.created_at,
        started_at=task.started_at,
        completed_at=task.completed_at,
        model_id=(task.metadata_json or {}).get("model_id"),
        model_name=(task.metadata_json or {}).get("model_name"),
    )


@router.get(
    "/{chat_id}/generation/{task_id}/events",
    response_model=list[ChatGenerationEventRead],
)
def list_generation_events(
    chat_id: str,
    task_id: str,
    after: int | None = None,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ChatGenerationEventRead]:
    try:
        chat_uuid = UUID(chat_id)
        task_uuid = UUID(task_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid id"
        ) from exc
    task = session.exec(select(ChatGenerationTask).where(ChatGenerationTask.id == task_uuid)).first()
    if not task or task.chat_id != chat_uuid:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    chat = session.exec(select(Chat).where(Chat.id == chat_uuid)).first()
    if not chat or chat.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")
    require_org_member(
        session, chat.org_id, current_user.id, is_super_admin=current_user.is_super_admin
    )
    query = select(ChatGenerationEvent).where(ChatGenerationEvent.task_id == task_uuid)
    if after is not None:
        query = query.where(ChatGenerationEvent.sequence > after)
    events = session.exec(query.order_by(ChatGenerationEvent.sequence)).all()
    return [
        ChatGenerationEventRead(
            id=str(event.id),
            event_type=event.event_type,
            payload=event.payload_json,
            sequence=event.sequence,
            created_at=event.created_at,
        )
        for event in events
    ]


@router.delete("/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_chat(
    chat_id: str,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    try:
        chat_uuid = UUID(chat_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid chat id"
        ) from exc

    chat = session.exec(select(Chat).where(Chat.id == chat_uuid)).first()
    if not chat or chat.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")
    if chat.user_id != current_user.id and not current_user.is_super_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Cannot access this chat"
        )
    if chat.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Cannot delete this chat"
        )
    require_org_member(
        session, chat.org_id, current_user.id, is_super_admin=current_user.is_super_admin
    )
    chat.is_deleted = True
    session.add(chat)
    session.commit()


@router.post("/{chat_id}/messages", response_model=list[ChatMessageRead])
async def create_message(
    chat_id: str,
    payload: ChatMessageCreateRequest,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ChatMessageRead]:
    try:
        chat_uuid = UUID(chat_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid chat id"
        ) from exc

    chat = session.exec(select(Chat).where(Chat.id == chat_uuid)).first()
    if not chat or chat.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")

    org = session.exec(select(Org).where(Org.id == chat.org_id)).first()
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found"
        )

    require_org_member(
        session, chat.org_id, current_user.id, is_super_admin=current_user.is_super_admin
    )

    model_id = chat.model_id
    if payload.model_id:
        try:
            model_id = UUID(payload.model_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid model id"
            ) from exc
        chat.model_id = model_id

    if not model_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Chat model not set"
        )

    model = session.exec(select(ChatModel).where(ChatModel.id == model_id)).first()
    if not model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Model not found"
        )
    enabled = session.exec(
        select(OrgModel).where(
            OrgModel.org_id == chat.org_id,
            OrgModel.model_id == model.id,
            OrgModel.is_enabled.is_(True),
        )
    ).first()
    if not enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Model is not enabled for this organization",
        )

    user_message = ChatMessage(
        chat_id=chat.id,
        role="user",
        content=payload.content,
        status="done",
    )
    session.add(user_message)
    session.commit()
    session.refresh(user_message)

    attachments = []
    if payload.attachments:
        for item in payload.attachments:
            attachments.append(
                ChatMessageAttachment(
                    message_id=user_message.id,
                    file_name=item.file_name,
                    content_type=item.content_type,
                    data_base64=item.data_base64,
                )
            )
        session.add_all(attachments)
        session.commit()
    attachment_reads = [
        ChatMessageAttachmentRead(
            id=str(attachment.id),
            file_name=attachment.file_name,
            content_type=attachment.content_type,
            data_base64=attachment.data_base64,
        )
        for attachment in attachments
    ]

    assistant_message = ChatMessage(
        chat_id=chat.id,
        role="assistant",
        content="",
        model_id=model.id,
    )
    session.add(assistant_message)
    session.commit()
    session.refresh(assistant_message)

    task = ChatGenerationTask(
        chat_id=chat.id,
        user_message_id=user_message.id,
        assistant_message_id=assistant_message.id,
        status=GenerationStatus.queued,
        metadata_json={
            "model_id": str(model.id),
            "model_name": model.display_name,
            "locale": payload.locale,
            "reasoning_effort": payload.reasoning_effort,
        },
    )
    session.add(task)
    session.commit()
    session.refresh(task)

    _enqueue_generation_task(task.id)

    if payload.stream:
        async def event_stream():
            yield (
                f"data: {json.dumps({'user_message_id': str(user_message.id), 'task_id': str(task.id), 'assistant_message_id': str(assistant_message.id)})}\n\n"
            )
            async for chunk in _stream_task_events_sse(task.id):
                yield chunk

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    return [
        ChatMessageRead(
            id=str(user_message.id),
            role=user_message.role,
            content=user_message.content,
            created_at=user_message.created_at,
            attachments=attachment_reads,
        ),
        ChatMessageRead(
            id=str(assistant_message.id),
            role=assistant_message.role,
            content=assistant_message.content,
            created_at=assistant_message.created_at,
            model_id=str(model.id),
            model_name=model.display_name,
            task_id=str(task.id),
            generation_status=task.status.value,
        ),
    ]

    history = session.exec(
        select(ChatMessage)
        .where(ChatMessage.chat_id == chat.id)
        .where(ChatMessage.is_current.is_(True))
        .order_by(ChatMessage.created_at)
    ).all()
    history_attachments = session.exec(
        select(ChatMessageAttachment).where(
            ChatMessageAttachment.message_id.in_([message.id for message in history])
        )
    ).all()
    attachments_by_message: dict[UUID, list[ChatMessageAttachment]] = {}
    for attachment in history_attachments:
        attachments_by_message.setdefault(attachment.message_id, []).append(attachment)

    def build_messages() -> list[dict]:
        items: list[dict] = []
        for msg in history:
            if msg.role != "user":
                items.append({"role": msg.role, "content": msg.content})
                continue
            msg_attachments = attachments_by_message.get(msg.id, [])
            if not msg_attachments:
                items.append({"role": msg.role, "content": msg.content})
                continue
            image_attachments = [
                attachment
                for attachment in msg_attachments
                if attachment.content_type.startswith("image/")
            ]
            file_attachments = [
                attachment
                for attachment in msg_attachments
                if not attachment.content_type.startswith("image/")
            ]
            if image_attachments and model.provider not in {"openai", "azure", "gemini"}:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Images are not supported for this model provider",
                )
            attachment_lines = _attachment_lines(msg_attachments)
            if not image_attachments:
                text = msg.content or ""
                if attachment_lines:
                    text += (
                        "\n\nAttachments (available in /inputs for code execution):\n"
                        + "\n".join(attachment_lines)
                    )
                items.append({"role": msg.role, "content": text})
                continue
            content_parts: list[dict] = []
            if msg.content:
                content_parts.append({"type": "text", "text": msg.content})
            if attachment_lines:
                content_parts.append(
                    {
                        "type": "text",
                        "text": "Attachments (available in /inputs for code execution):\n"
                        + "\n".join(attachment_lines),
                    }
                )
            for attachment in image_attachments:
                content_parts.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{attachment.content_type};base64,{attachment.data_base64}"
                        },
                    }
                )
            items.append({"role": msg.role, "content": content_parts})
        return items

    messages = _truncate_messages(
        _prepend_tool_guidance(build_messages(), locale=payload.locale),
        token_limit=model.context_length,
    )

    provider_config = require_provider_enabled(session, chat.org_id, model.provider)
    config = None
    if provider_config and provider_config.config_json:
        try:
            config = json.loads(provider_config.config_json)
        except json.JSONDecodeError:
            pass
    prompt_cache_key = f"chat:{chat.id}"
    provider = get_provider(
        model.provider,
        api_key=provider_config.api_key_override if provider_config else None,
        base_url=provider_config.base_url_override if provider_config else None,
        endpoint=provider_config.endpoint_override if provider_config else None,
        reasoning_effort=payload.reasoning_effort or model.reasoning_effort,
        prompt_cache_key=prompt_cache_key,
        prompt_cache_retention=settings.openai_prompt_cache_retention,
        config=config,
    )
    grounding_enabled = _grounding_enabled(org, model.provider)
    tool_registry = _build_tool_registry(
        session,
        chat.org_id,
        chat_id=chat.id,
        preferred_provider=model.provider,
        web_tools_enabled=not grounding_enabled,
        web_search_enabled=org.web_search_enabled,
        web_scrape_enabled=org.web_scrape_enabled,
        exec_policy=org.exec_policy,
        exec_network_enabled=org.exec_network_enabled,
        locale=payload.locale,
    )
    tool_attachments: list[dict] | None = None

    if _is_image_output_model(model) and payload.stream:
        async def image_stream():
            image_result = await generate_image(
                ImageToolContext(session=session, org_id=str(chat.org_id)),
                prompt=payload.content,
                model_override=model,
            )
            assistant_message = ChatMessage(
                chat_id=chat.id,
                role="assistant",
                content="",
                model_id=model.id,
            )
            session.add(assistant_message)
            session.commit()
            session.refresh(assistant_message)
            if image_result.attachments:
                session.add_all(
                    [
                        ChatMessageAttachment(
                            message_id=assistant_message.id,
                            file_name=item["file_name"],
                            content_type=item["content_type"],
                            data_base64=item["data_base64"],
                        )
                        for item in image_result.attachments
                    ]
                )
                session.commit()
            usage_event = UsageEvent(
                org_id=chat.org_id,
                user_id=current_user.id,
                chat_id=chat.id,
                message_id=assistant_message.id,
                model_id=model.id,
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                input_tokens=0,
                output_tokens=0,
                cached_tokens=0,
                thinking_tokens=0,
                image_width=image_result.output.get("image_width"),
                image_height=image_result.output.get("image_height"),
                image_count=image_result.output.get("image_count"),
                image_format=image_result.output.get("image_format"),
            )
            session.add(usage_event)
            session.commit()
            await _maybe_update_chat_title(
                session=session,
                chat=chat,
                provider=provider,
                model=model,
                history=history + [assistant_message],
            )
            yield f"data: {json.dumps({'user_message_id': str(user_message.id)})}\n\n"
            yield f"data: {json.dumps({'done': True, 'message_id': str(assistant_message.id), 'content': '', 'model_name': model.display_name, 'model_id': str(model.id), 'attachments': image_result.attachments or []})}\n\n"

        return StreamingResponse(image_stream(), media_type="text/event-stream")

    if _is_image_output_model(model):
        image_result = await generate_image(
            ImageToolContext(session=session, org_id=str(chat.org_id)),
            prompt=payload.content,
            model_override=model,
        )
        assistant_message = ChatMessage(
            chat_id=chat.id,
            role="assistant",
            content="",
            model_id=model.id,
        )
        session.add(assistant_message)
        session.commit()
        session.refresh(assistant_message)
        if image_result.attachments:
            session.add_all(
                [
                    ChatMessageAttachment(
                        message_id=assistant_message.id,
                        file_name=item["file_name"],
                        content_type=item["content_type"],
                        data_base64=item["data_base64"],
                    )
                    for item in image_result.attachments
                ]
            )
            session.commit()
        usage_event = UsageEvent(
            org_id=chat.org_id,
            user_id=current_user.id,
            chat_id=chat.id,
            message_id=assistant_message.id,
            model_id=model.id,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            input_tokens=0,
            output_tokens=0,
            cached_tokens=0,
            thinking_tokens=0,
        )
        session.add(usage_event)
        session.commit()
        await _maybe_update_chat_title(
            session=session,
            chat=chat,
            provider=provider,
            model=model,
            history=history + [assistant_message],
        )
        return [
            ChatMessageRead(
                id=str(user_message.id),
                role=user_message.role,
                content=user_message.content,
                created_at=user_message.created_at,
                attachments=attachment_reads,
            ),
            ChatMessageRead(
                id=str(assistant_message.id),
                role=assistant_message.role,
                content=assistant_message.content,
                created_at=assistant_message.created_at,
                model_id=str(model.id),
                model_name=model.display_name,
                attachments=image_result.attachments
                and [
                    ChatMessageAttachmentRead(
                        id="",
                        file_name=item["file_name"],
                        content_type=item["content_type"],
                        data_base64=item["data_base64"],
                    )
                    for item in image_result.attachments
                ],
            ),
        ]
    if payload.stream:
        async def event_stream():
            assistant_content = ""
            usage = ChatUsage(0, 0, 0, 0, 0, 0, 0)

            yield f"data: {json.dumps({'user_message_id': str(user_message.id)})}\n\n"

            if grounding_enabled and hasattr(provider, "chat_grounded"):
                response = await provider.chat_grounded(model.model_name, messages)
                response.sources = await _normalize_sources(response.sources or [])
                assistant_message = ChatMessage(
                    chat_id=chat.id,
                    role="assistant",
                    content=response.content,
                    model_id=model.id,
                    sources=response.sources,
                )
                session.add(assistant_message)
                session.commit()
                session.refresh(assistant_message)

                usage_event = UsageEvent(
                    org_id=chat.org_id,
                    user_id=current_user.id,
                    chat_id=chat.id,
                    message_id=assistant_message.id,
                    model_id=model.id,
                    prompt_tokens=response.usage.prompt_tokens,
                    completion_tokens=response.usage.completion_tokens,
                    total_tokens=response.usage.total_tokens,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    cached_tokens=response.usage.cached_tokens,
                    thinking_tokens=response.usage.thinking_tokens,
                )
                session.add(usage_event)
                session.commit()
                await _maybe_update_chat_title(
                    session=session,
                    chat=chat,
                    provider=provider,
                    model=model,
                    history=history + [assistant_message],
                )

                yield f"data: {json.dumps({'done': True, 'message_id': str(assistant_message.id), 'content': response.content, 'model_name': model.display_name, 'model_id': str(model.id), 'sources': response.sources or []})}\n\n"
                return

            if tool_registry and hasattr(provider, "chat_with_tools"):
                content, tool_attachments, tool_sources, image_usages, last_usage = (
                    await _run_agentic_loop(
                        provider=provider,
                        model=model,
                        messages=messages,
                        tool_registry=tool_registry,
                    )
                )
                assistant_message = ChatMessage(
                    chat_id=chat.id,
                    role="assistant",
                    content=content,
                    model_id=model.id,
                    sources=tool_sources or None,
                )
                session.add(assistant_message)
                session.commit()
                session.refresh(assistant_message)
                if tool_attachments:
                    session.add_all(
                        [
                            ChatMessageAttachment(
                                message_id=assistant_message.id,
                                file_name=item["file_name"],
                                content_type=item["content_type"],
                                data_base64=item["data_base64"],
                            )
                            for item in tool_attachments
                        ]
                    )
                    session.commit()
                usage = last_usage or ChatUsage(0, 0, 0, 0, 0, 0, 0)
                usage_event = UsageEvent(
                    org_id=chat.org_id,
                    user_id=current_user.id,
                    chat_id=chat.id,
                    message_id=assistant_message.id,
                    model_id=model.id,
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens,
                    total_tokens=usage.total_tokens,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cached_tokens=usage.cached_tokens,
                    thinking_tokens=usage.thinking_tokens,
                )
                session.add(usage_event)
                session.commit()
                if image_usages:
                    for item in image_usages:
                        session.add(
                            UsageEvent(
                                org_id=chat.org_id,
                                user_id=current_user.id,
                                chat_id=chat.id,
                                message_id=assistant_message.id,
                                model_id=UUID(item["model_id"]),
                                prompt_tokens=item["prompt_tokens"],
                                completion_tokens=item["completion_tokens"],
                                total_tokens=item["total_tokens"],
                                input_tokens=item["input_tokens"],
                                output_tokens=item["output_tokens"],
                                cached_tokens=item["cached_tokens"],
                                thinking_tokens=item["thinking_tokens"],
                                image_width=item.get("image_width"),
                                image_height=item.get("image_height"),
                                image_count=item.get("image_count"),
                                image_format=item.get("image_format"),
                            )
                        )
                    session.commit()
                await _maybe_update_chat_title(
                    session=session,
                    chat=chat,
                    provider=provider,
                    model=model,
                    history=history + [assistant_message],
                )
                yield f"data: {json.dumps({'delta': content})}\n\n"
                yield f"data: {json.dumps({'done': True, 'message_id': str(assistant_message.id), 'content': content, 'model_name': model.display_name, 'model_id': str(model.id), 'attachments': tool_attachments or [], 'sources': tool_sources or []})}\n\n"
                return

            response = await provider.chat(model.model_name, messages)
            assistant_content = response.content or ""
            usage = response.usage
            if assistant_content:
                yield f"data: {json.dumps({'delta': assistant_content})}\n\n"

            assistant_message = ChatMessage(
                chat_id=chat.id,
                role="assistant",
                content=assistant_content,
                model_id=model.id,
            )
            session.add(assistant_message)
            session.commit()
            session.refresh(assistant_message)

            usage_event = UsageEvent(
                org_id=chat.org_id,
                user_id=current_user.id,
                chat_id=chat.id,
                message_id=assistant_message.id,
                model_id=model.id,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                total_tokens=usage.total_tokens,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cached_tokens=usage.cached_tokens,
                thinking_tokens=usage.thinking_tokens,
            )
            session.add(usage_event)
            session.commit()

            await _maybe_update_chat_title(
                session=session,
                chat=chat,
                provider=provider,
                model=model,
                history=history + [assistant_message],
            )

            yield f"data: {json.dumps({'done': True, 'message_id': str(assistant_message.id), 'content': assistant_content, 'model_name': model.display_name, 'model_id': str(model.id)})}\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    tool_attachments: list[dict] | None = None
    if grounding_enabled and hasattr(provider, "chat_grounded"):
        response = await provider.chat_grounded(model.model_name, messages)
        response.sources = await _normalize_sources(response.sources or [])
    elif tool_registry and hasattr(provider, "chat_with_tools"):
        content, tool_attachments, tool_sources, image_usages, last_usage = (
            await _run_agentic_loop(
                provider=provider,
                model=model,
                messages=messages,
                tool_registry=tool_registry,
            )
        )
        response = ChatResponse(
            content=content,
            usage=last_usage or ChatUsage(0, 0, 0, 0, 0, 0, 0),
            sources=tool_sources or None,
        )
    else:
        response = await provider.chat(model.model_name, messages)

    assistant_message = ChatMessage(
        chat_id=chat.id,
        role="assistant",
        content=response.content,
        model_id=model.id,
        sources=response.sources,
    )
    session.add(assistant_message)
    session.commit()
    session.refresh(assistant_message)

    if tool_attachments:
        session.add_all(
            [
                ChatMessageAttachment(
                    message_id=assistant_message.id,
                    file_name=item["file_name"],
                    content_type=item["content_type"],
                    data_base64=item["data_base64"],
                )
                for item in tool_attachments
            ]
        )
        session.commit()

    usage_event = UsageEvent(
        org_id=chat.org_id,
        user_id=current_user.id,
        chat_id=chat.id,
        message_id=assistant_message.id,
        model_id=model.id,
        prompt_tokens=response.usage.prompt_tokens,
        completion_tokens=response.usage.completion_tokens,
        total_tokens=response.usage.total_tokens,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        cached_tokens=response.usage.cached_tokens,
        thinking_tokens=response.usage.thinking_tokens,
    )
    session.add(usage_event)
    session.commit()
    await _maybe_update_chat_title(
        session=session,
        chat=chat,
        provider=provider,
        model=model,
        history=history + [assistant_message],
    )

    attachment_reads = [
        ChatMessageAttachmentRead(
            id=str(attachment.id),
            file_name=attachment.file_name,
            content_type=attachment.content_type,
            data_base64=attachment.data_base64,
        )
        for attachment in attachments
    ]
    assistant_attachment_reads = None
    if tool_attachments:
        assistant_attachment_reads = [
            ChatMessageAttachmentRead(
                id="",
                file_name=item["file_name"],
                content_type=item["content_type"],
                data_base64=item["data_base64"],
            )
            for item in tool_attachments
        ]
    return [
        ChatMessageRead(
            id=str(user_message.id),
            role=user_message.role,
            content=user_message.content,
            created_at=user_message.created_at,
            attachments=attachment_reads,
        ),
        ChatMessageRead(
            id=str(assistant_message.id),
            role=assistant_message.role,
            content=assistant_message.content,
            created_at=assistant_message.created_at,
            model_id=str(model.id),
            model_name=model.display_name,
            attachments=assistant_attachment_reads,
            sources=assistant_message.sources,
        ),
    ]


@router.websocket("/{chat_id}/ws")
async def chat_ws(websocket: WebSocket, chat_id: str) -> None:
    token = _extract_ws_token(websocket)
    if not token:
        await websocket.close(code=4401)
        return
    protocols = websocket.headers.get("sec-websocket-protocol", "")
    requested = [item.strip() for item in protocols.split(",") if item.strip()]
    subprotocol = "chatui" if "chatui" in requested else None
    await websocket.accept(subprotocol=subprotocol)
    try:
        with Session(engine) as auth_session:
            auth_user = _get_user_from_token(auth_session, token)
            current_user_id = auth_user.id

        while True:
            payload = await websocket.receive_json()
            message_type = payload.get("type")
            message_payload = payload.get("payload") or {}

            with Session(engine) as session:
                current_user = session.get(User, current_user_id)
                if not current_user or not current_user.is_active:
                    await _ws_send_event(websocket, {"error": "User not found"})
                    await websocket.close(code=4401)
                    return

                if message_type == "send":
                    try:
                        request = ChatMessageCreateRequest(**message_payload)
                    except Exception:
                        await _ws_send_event(websocket, {"error": "Invalid payload"})
                        continue
                    await _stream_message_ws(
                        websocket, session, current_user, chat_id, request
                    )
                elif message_type == "edit":
                    message_id = message_payload.get("message_id")
                    if not message_id:
                        await _ws_send_event(
                            websocket, {"error": "Message id is required"}
                        )
                        continue
                    try:
                        request = ChatMessageEditRequest(
                            **{
                                key: value
                                for key, value in message_payload.items()
                                if key != "message_id"
                            }
                        )
                    except Exception:
                        await _ws_send_event(websocket, {"error": "Invalid payload"})
                        continue
                    await _stream_edit_ws(
                        websocket, session, current_user, chat_id, message_id, request
                    )
                elif message_type == "subscribe":
                    task_id = message_payload.get("task_id")
                    after = message_payload.get("after", 0)
                    if not task_id:
                        await _ws_send_event(websocket, {"error": "Task id is required"})
                        continue
                    try:
                        task_uuid = UUID(task_id)
                        chat_uuid = UUID(chat_id)
                        after_sequence = int(after or 0)
                    except ValueError:
                        await _ws_send_event(websocket, {"error": "Invalid id"})
                        continue
                    task = session.exec(
                        select(ChatGenerationTask).where(
                            ChatGenerationTask.id == task_uuid,
                            ChatGenerationTask.chat_id == chat_uuid,
                        )
                    ).first()
                    if not task:
                        await _ws_send_event(websocket, {"error": "Task not found"})
                        continue
                    await _stream_task_events_ws(
                        websocket, task.id, after_sequence=after_sequence
                    )
                else:
                    await _ws_send_event(websocket, {"error": "Unsupported message type"})
                    continue
    except WebSocketDisconnect:
        return
    except HTTPException as exc:
        await _ws_send_event(
            websocket, {"error": exc.detail, "status": exc.status_code}
        )
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            await websocket.close(code=4401)
        elif exc.status_code == status.HTTP_403_FORBIDDEN:
            await websocket.close(code=4403)
        else:
            await websocket.close(code=4400)
    except Exception:
        logger.exception("Websocket error")
        await _ws_send_event(websocket, {"error": "Websocket error"})
        await websocket.close(code=1011)


@router.patch("/{chat_id}/messages/{message_id}", response_model=ChatMessageEditResponse)
async def edit_message(
    chat_id: str,
    message_id: str,
    payload: ChatMessageEditRequest,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChatMessageRead:
    try:
        chat_uuid = UUID(chat_id)
        message_uuid = UUID(message_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid id"
        ) from exc

    chat = session.exec(select(Chat).where(Chat.id == chat_uuid)).first()
    if not chat or chat.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")
    if chat.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Cannot edit this message"
        )

    org = session.exec(select(Org).where(Org.id == chat.org_id)).first()
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found"
        )

    require_org_member(
        session, chat.org_id, current_user.id, is_super_admin=current_user.is_super_admin
    )

    message = session.exec(
        select(ChatMessage).where(
            ChatMessage.id == message_uuid, ChatMessage.chat_id == chat.id
        )
    ).first()
    if not message:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Message not found"
        )
    if message.role != "user":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only user messages can be edited",
        )

    model_id = chat.model_id
    if not model_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Chat model not set"
        )
    model = session.exec(select(ChatModel).where(ChatModel.id == model_id)).first()
    if not model:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model not found")
    enabled = session.exec(
        select(OrgModel).where(
            OrgModel.org_id == chat.org_id,
            OrgModel.model_id == model.id,
            OrgModel.is_enabled.is_(True),
        )
    ).first()
    if not enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Model is not enabled for this organization",
        )

    to_hide = session.exec(
        select(ChatMessage)
        .where(ChatMessage.chat_id == chat.id)
        .where(ChatMessage.is_current.is_(True))
        .where(ChatMessage.created_at >= message.created_at)
        .order_by(ChatMessage.created_at)
    ).all()
    for item in to_hide:
        item.is_current = False
        session.add(item)
    session.commit()

    new_message = ChatMessage(
        chat_id=chat.id,
        role=message.role,
        content=payload.content,
        parent_id=message.id,
        branch_id=uuid4(),
        is_current=True,
        status="done",
    )
    session.add(new_message)
    session.commit()
    session.refresh(new_message)

    if payload.attachments is None:
        prev_attachments = session.exec(
            select(ChatMessageAttachment).where(
                ChatMessageAttachment.message_id == message.id
            )
        ).all()
        if prev_attachments:
            session.add_all(
                [
                    ChatMessageAttachment(
                        message_id=new_message.id,
                        file_name=attachment.file_name,
                        content_type=attachment.content_type,
                        data_base64=attachment.data_base64,
                    )
                    for attachment in prev_attachments
                ]
            )
            session.commit()
    else:
        if payload.attachments:
            session.add_all(
                [
                    ChatMessageAttachment(
                        message_id=new_message.id,
                        file_name=attachment.file_name,
                        content_type=attachment.content_type,
                        data_base64=attachment.data_base64,
                    )
                    for attachment in payload.attachments
                ]
            )
            session.commit()

    edited_attachments = session.exec(
        select(ChatMessageAttachment).where(
            ChatMessageAttachment.message_id == new_message.id
        )
    ).all()
    attachment_reads = [
        ChatMessageAttachmentRead(
            id=str(attachment.id),
            file_name=attachment.file_name,
            content_type=attachment.content_type,
            data_base64=attachment.data_base64,
        )
        for attachment in edited_attachments
    ]

    assistant_message = ChatMessage(
        chat_id=chat.id,
        role="assistant",
        content="",
        model_id=model.id,
        is_current=True,
        status="generating",
        started_at=datetime.utcnow(),
    )
    session.add(assistant_message)
    session.commit()
    session.refresh(assistant_message)

    task = ChatGenerationTask(
        chat_id=chat.id,
        user_message_id=new_message.id,
        assistant_message_id=assistant_message.id,
        status=GenerationStatus.queued,
        metadata_json={
            "model_id": str(model.id),
            "model_name": model.display_name,
            "locale": payload.locale,
            "reasoning_effort": payload.reasoning_effort,
        },
    )
    session.add(task)
    session.commit()
    session.refresh(task)

    _enqueue_generation_task(task.id)

    return ChatMessageEditResponse(
        user_message=ChatMessageRead(
            id=str(new_message.id),
            role=new_message.role,
            content=new_message.content,
            created_at=new_message.created_at,
            attachments=attachment_reads,
        ),
        assistant_message=ChatMessageRead(
            id=str(assistant_message.id),
            role=assistant_message.role,
            content=assistant_message.content,
            created_at=assistant_message.created_at,
            model_id=str(model.id),
            model_name=model.display_name,
            task_id=str(task.id),
            generation_status=task.status.value,
        ),
    )

    history = session.exec(
        select(ChatMessage)
        .where(ChatMessage.chat_id == chat.id)
        .where(ChatMessage.is_current.is_(True))
        .order_by(ChatMessage.created_at)
    ).all()
    history_attachments = session.exec(
        select(ChatMessageAttachment).where(
            ChatMessageAttachment.message_id.in_([message.id for message in history])
        )
    ).all()
    attachments_by_message: dict[UUID, list[ChatMessageAttachment]] = {}
    for attachment in history_attachments:
        attachments_by_message.setdefault(attachment.message_id, []).append(attachment)

    def build_messages() -> list[dict]:
        items: list[dict] = []
        for msg in history:
            if msg.role != "user":
                items.append({"role": msg.role, "content": msg.content})
                continue
            msg_attachments = attachments_by_message.get(msg.id, [])
            if not msg_attachments:
                items.append({"role": msg.role, "content": msg.content})
                continue
            image_attachments = [
                attachment
                for attachment in msg_attachments
                if attachment.content_type.startswith("image/")
            ]
            file_attachments = [
                attachment
                for attachment in msg_attachments
                if not attachment.content_type.startswith("image/")
            ]
            if image_attachments and model.provider not in {"openai", "azure", "gemini"}:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Images are not supported for this model provider",
                )
            attachment_lines = _attachment_lines(msg_attachments)
            if not image_attachments:
                text = msg.content or ""
                if attachment_lines:
                    text += (
                        "\n\nAttachments (available in /inputs for code execution):\n"
                        + "\n".join(attachment_lines)
                    )
                items.append({"role": msg.role, "content": text})
                continue
            content_parts: list[dict] = []
            if msg.content:
                content_parts.append({"type": "text", "text": msg.content})
            if attachment_lines:
                content_parts.append(
                    {
                        "type": "text",
                        "text": "Attachments (available in /inputs for code execution):\n"
                        + "\n".join(attachment_lines),
                    }
                )
            for attachment in image_attachments:
                content_parts.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{attachment.content_type};base64,{attachment.data_base64}"
                        },
                    }
                )
            items.append({"role": msg.role, "content": content_parts})
        return items

    messages = _truncate_messages(
        _prepend_tool_guidance(build_messages(), locale=payload.locale),
        token_limit=model.context_length,
    )

    provider_config = require_provider_enabled(session, chat.org_id, model.provider)
    config = None
    if provider_config and provider_config.config_json:
        try:
            config = json.loads(provider_config.config_json)
        except json.JSONDecodeError:
            pass
    prompt_cache_key = f"chat:{chat.id}"
    provider = get_provider(
        model.provider,
        api_key=provider_config.api_key_override if provider_config else None,
        base_url=provider_config.base_url_override if provider_config else None,
        endpoint=provider_config.endpoint_override if provider_config else None,
        reasoning_effort=payload.reasoning_effort or model.reasoning_effort,
        prompt_cache_key=prompt_cache_key,
        prompt_cache_retention=settings.openai_prompt_cache_retention,
        config=config,
    )
    grounding_enabled = _grounding_enabled(org, model.provider)
    tool_registry = _build_tool_registry(
        session,
        chat.org_id,
        chat_id=chat.id,
        preferred_provider=model.provider,
        web_tools_enabled=not grounding_enabled,
        web_search_enabled=org.web_search_enabled,
        web_scrape_enabled=org.web_scrape_enabled,
        exec_policy=org.exec_policy,
        exec_network_enabled=org.exec_network_enabled,
        locale=payload.locale,
    )

    if model.supports_image_output:
        image_result = await generate_image(
            ImageToolContext(session=session, org_id=str(chat.org_id)),
            prompt=payload.content,
            model_override=model,
        )
        assistant_message = ChatMessage(
            chat_id=chat.id,
            role="assistant",
            content="",
            model_id=model.id,
            is_current=True,
        )
        session.add(assistant_message)
        session.commit()
        session.refresh(assistant_message)
        if image_result.attachments:
            session.add_all(
                [
                    ChatMessageAttachment(
                        message_id=assistant_message.id,
                        file_name=item["file_name"],
                        content_type=item["content_type"],
                        data_base64=item["data_base64"],
                    )
                    for item in image_result.attachments
                ]
            )
            session.commit()
        usage_event = UsageEvent(
            org_id=chat.org_id,
            user_id=current_user.id,
            chat_id=chat.id,
            message_id=assistant_message.id,
            model_id=model.id,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            input_tokens=0,
            output_tokens=0,
            cached_tokens=0,
            thinking_tokens=0,
        )
        session.add(usage_event)
        session.commit()
        return ChatMessageEditResponse(
            user_message=ChatMessageRead(
                id=str(new_message.id),
                role=new_message.role,
                content=new_message.content,
                created_at=new_message.created_at,
                attachments=attachment_reads,
            ),
            assistant_message=ChatMessageRead(
                id=str(assistant_message.id),
                role=assistant_message.role,
                content=assistant_message.content,
                created_at=assistant_message.created_at,
                model_id=str(model.id),
                model_name=model.display_name,
                attachments=image_result.attachments
                and [
                    ChatMessageAttachmentRead(
                        id="",
                        file_name=item["file_name"],
                        content_type=item["content_type"],
                        data_base64=item["data_base64"],
                    )
                    for item in image_result.attachments
                ],
            ),
        )

    tool_attachments: list[dict] | None = None
    if grounding_enabled and hasattr(provider, "chat_grounded"):
        response = await provider.chat_grounded(model.model_name, messages)
        response.sources = await _normalize_sources(response.sources or [])
    elif tool_registry and hasattr(provider, "chat_with_tools"):
        content, tool_attachments, tool_sources, image_usages, last_usage = (
            await _run_agentic_loop(
                provider=provider,
                model=model,
                messages=messages,
                tool_registry=tool_registry,
            )
        )
        response = ChatResponse(
            content=content,
            usage=last_usage or ChatUsage(0, 0, 0, 0, 0, 0, 0),
            sources=tool_sources or None,
        )
    else:
        response = await provider.chat(model.model_name, messages)

    assistant_message = ChatMessage(
        chat_id=chat.id,
        role="assistant",
        content=response.content,
        model_id=model.id,
        is_current=True,
        sources=response.sources,
    )
    session.add(assistant_message)
    session.commit()
    session.refresh(assistant_message)

    if tool_attachments:
        session.add_all(
            [
                ChatMessageAttachment(
                    message_id=assistant_message.id,
                    file_name=item["file_name"],
                    content_type=item["content_type"],
                    data_base64=item["data_base64"],
                )
                for item in tool_attachments
            ]
        )
        session.commit()

    usage_event = UsageEvent(
        org_id=chat.org_id,
        user_id=current_user.id,
        chat_id=chat.id,
        message_id=assistant_message.id,
        model_id=model.id,
        prompt_tokens=response.usage.prompt_tokens,
        completion_tokens=response.usage.completion_tokens,
        total_tokens=response.usage.total_tokens,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        cached_tokens=response.usage.cached_tokens,
        thinking_tokens=response.usage.thinking_tokens,
    )
    session.add(usage_event)
    session.commit()
    if image_usages:
        for item in image_usages:
            session.add(
                UsageEvent(
                    org_id=chat.org_id,
                    user_id=current_user.id,
                    chat_id=chat.id,
                    message_id=assistant_message.id,
                    model_id=UUID(item["model_id"]),
                    prompt_tokens=item["prompt_tokens"],
                    completion_tokens=item["completion_tokens"],
                    total_tokens=item["total_tokens"],
                    input_tokens=item["input_tokens"],
                    output_tokens=item["output_tokens"],
                    cached_tokens=item["cached_tokens"],
                    thinking_tokens=item["thinking_tokens"],
                    image_width=item.get("image_width"),
                    image_height=item.get("image_height"),
                    image_count=item.get("image_count"),
                    image_format=item.get("image_format"),
                )
            )
        session.commit()

    assistant_attachment_reads = None
    if tool_attachments:
        assistant_attachment_reads = [
            ChatMessageAttachmentRead(
                id="",
                file_name=item["file_name"],
                content_type=item["content_type"],
                data_base64=item["data_base64"],
            )
            for item in tool_attachments
        ]
    return ChatMessageEditResponse(
        user_message=ChatMessageRead(
            id=str(new_message.id),
            role=new_message.role,
            content=new_message.content,
            created_at=new_message.created_at,
            attachments=attachment_reads,
        ),
        assistant_message=ChatMessageRead(
            id=str(assistant_message.id),
            role=assistant_message.role,
            content=assistant_message.content,
            created_at=assistant_message.created_at,
            model_id=str(model.id),
            model_name=model.display_name,
            attachments=assistant_attachment_reads,
            sources=assistant_message.sources,
        ),
    )


@router.delete("/{chat_id}/messages/{message_id}/branch", status_code=status.HTTP_204_NO_CONTENT)
def delete_message_branch(
    chat_id: str,
    message_id: str,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    try:
        chat_uuid = UUID(chat_id)
        message_uuid = UUID(message_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid id"
        ) from exc

    chat = session.exec(select(Chat).where(Chat.id == chat_uuid)).first()
    if not chat or chat.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")
    if chat.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Cannot edit this message"
        )

    require_org_member(
        session, chat.org_id, current_user.id, is_super_admin=current_user.is_super_admin
    )

    message = session.exec(
        select(ChatMessage).where(
            ChatMessage.id == message_uuid, ChatMessage.chat_id == chat.id
        )
    ).first()
    if not message:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Message not found"
        )
    if message.role != "user":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only user messages can be removed",
        )

    to_hide = session.exec(
        select(ChatMessage)
        .where(ChatMessage.chat_id == chat.id)
        .where(ChatMessage.is_current.is_(True))
        .where(ChatMessage.created_at >= message.created_at)
        .order_by(ChatMessage.created_at)
    ).all()
    for item in to_hide:
        item.is_current = False
        session.add(item)
    session.commit()
