from datetime import datetime, timedelta, timezone
import base64
import html
import logging
import re
import secrets
from io import BytesIO
from typing import Any, Iterable
from urllib.parse import urlparse
from uuid import UUID, uuid4

import json
import httpx
import anyio
from json_repair import loads as repair_json_loads
from sqlalchemy import func
from fastapi import APIRouter, Depends, HTTPException, status, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, StreamingResponse
import jwt
from jwt.exceptions import InvalidTokenError
from pydantic import BaseModel, ValidationError, field_validator, model_validator
from sqlmodel import Session, select
from PIL import ExifTags, Image

from app.api.deps import get_current_user, get_db
from app.core.config import settings
from app.core.security import decode_access_token_claims
from app.db.session import engine
from app.models import (
    Agent,
    AgentAccess,
    Chat,
    ChatCoworkDocument,
    ChatGenerationEvent,
    ChatGenerationTask,
    ChatMessage,
    ChatMessageAttachment,
    ChatViewEvent,
    ChatUpload,
    ChatModel,
    GenerationStatus,
    Org,
    UsageEvent,
    User,
    UserMemory,
)
from app.services.org_service import require_org_member, require_provider_enabled
from app.services.team_service import allowed_model_ids
from app.services.model_capabilities import (
    ensure_model_capabilities,
    persist_responses_api_discovery,
    supports_image_input,
    supports_image_output,
)
from app.services.providers.base import ChatResponse, ChatUsage
from app.services.providers.registry import get_provider
from app.services.system_prompts import build_system_prompt_messages
from app.services.tools.image_tool import (
    ImageToolContext,
    edit_image,
    generate_image,
    get_image_model,
    image_usage_token_fields,
)
from app.services.tools.code_execution import (
    ALLOWED_IMPORTS_HINT,
    CodeExecutionContext,
    run_code_execution,
)
from app.services.tools.registry import ToolRegistry, ToolSpec, ToolResult
from app.services.tools.pdf_tool import PdfToolContext, extract_pdf
from app.services.tools.memory_tools import (
    MemoryToolContext,
    search_past_chats,
    store_memory,
    remove_memory,
)
from app.services.tools.agent_tools import (
    AgentToolContext,
    list_project_sources,
    read_project_source,
    search_project_sources,
    _coerce_bool as _coerce_include_chats,
)
from app.services.tools.web_tools import (
    WebToolContext,
    download_attachments,
    web_scrape,
    web_search,
)
from app.services.tools.cowork_tools import (
    CoworkToolContext,
    activate_document,
    apply_user_patch,
    cowork_append,
    cowork_read,
    cowork_str_replace,
    cowork_write,
    delete_document,
    document_payload,
    get_active_document,
    get_document,
    list_documents,
    mime_for_document,
    start_coworking,
)
from app.services.mcp import register_mcp_tools
from app.services.generation_event_bus import iter_generation_notifications
from app.services.model_pricing import estimate_token_cost_usd
from app.services.usage_limits import enforce_chat_usage_limits
from app.workers.celery_app import celery_app

router = APIRouter(prefix="/chats", tags=["chats"])
logger = logging.getLogger(__name__)

MAX_CONTEXT_MESSAGES = 60
HEAD_CONTEXT_MESSAGES = 4
TAIL_CONTEXT_MESSAGES = 12
MAX_TOOL_STEPS = 25
MAX_WEB_SEARCH_CALLS = 3
MAX_WEB_SCRAPE_CALLS = 10
WEB_SCRAPE_ANSWER_MARKDOWN_LIMIT = 12000
WEB_SCRAPE_ANSWER_HEAD_RATIO = 0.7
CHAT_NOT_SHARED_DETAIL = "CHAT_NOT_SHARED"


def _user_can_use_model(session: Session, org_id: UUID, user_id: UUID, model_id: UUID) -> bool:
    return model_id in allowed_model_ids(session, org_id, user_id)


def _viewer_label(user: User | None) -> str:
    if not user:
        return "Anonymous user"
    if user.display_name and user.display_name.strip():
        return user.display_name.strip()
    if user.username and user.username.strip():
        return user.username.strip()
    if user.email and user.email.strip():
        return user.email.strip()
    return "Anonymous user"


def _attachment_access_token(attachment_id: UUID) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.attachment_url_expire_minutes
    )
    payload = {"att": str(attachment_id), "exp": expire}
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def _decode_attachment_access_token(token: str) -> UUID:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
        attachment_id = payload.get("att")
        if not attachment_id:
            raise ValueError("Missing attachment id")
        return UUID(str(attachment_id))
    except (InvalidTokenError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid attachment token",
        ) from exc


def _public_api_base_url() -> str | None:
    configured = settings.public_api_base_url.strip()
    if configured:
        return configured.rstrip("/")
    return None


def _attachment_image_url(attachment: ChatMessageAttachment) -> str:
    base_url = _public_api_base_url()
    if base_url:
        token = _attachment_access_token(attachment.id)
        return f"{base_url}/chats/attachments/{attachment.id}/content?token={token}"
    return f"data:{attachment.content_type};base64,{attachment.data_base64}"


def _attachment_content_url(attachment_id: UUID) -> str:
    token = _attachment_access_token(attachment_id)
    base_url = _public_api_base_url()
    if base_url:
        return f"{base_url}/chats/attachments/{attachment_id}/content?token={token}"
    return f"/api/chats/attachments/{attachment_id}/content?token={token}"


def _chat_share_url(chat: Chat) -> str | None:
    if not chat.share_token:
        return None
    return f"/chat/{chat.id}"


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


def _ensure_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _coerce_optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    return None


def _base64_size_bytes(value: str | None) -> int:
    if not value:
        return 0
    padding = value.count("=")
    return max(len(value) * 3 // 4 - padding, 0)


def _resolve_exec_policy(org_exec_policy: str, code_execution_enabled: object) -> str:
    enabled = _coerce_optional_bool(code_execution_enabled)
    if enabled is False:
        return "off"
    return org_exec_policy


def _effective_web_tool_enabled(org_tool_enabled: bool, request_enabled: object) -> bool:
    enabled = _coerce_optional_bool(request_enabled)
    if enabled is False:
        return False
    return org_tool_enabled


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


def _load_user_memories(session: Session, user: User) -> list[dict[str, str]] | None:
    if not user.memory_enabled:
        return None
    rows = session.exec(
        select(UserMemory)
        .where(UserMemory.user_id == user.id)
        .order_by(UserMemory.created_at)
    ).all()
    if not rows:
        return None
    return [{"id": str(m.id), "content": m.content} for m in rows]


def _prepend_tool_guidance(
    messages: list[dict],
    *,
    locale: str | None = None,
    timezone: str | None = None,
    enabled_tool_names: list[str] | None = None,
    memories: list[dict[str, str]] | None = None,
) -> list[dict]:
    system_messages = build_system_prompt_messages(
        locale=locale,
        timezone=timezone,
        enabled_tool_names=enabled_tool_names,
        memories=memories,
    )
    enabled = set(enabled_tool_names or [])
    if "code_execution" in enabled:
        system_messages.append(
            {
                "role": "system",
                "content": (
                    "For code_execution, available third-party imports are: "
                    f"{ALLOWED_IMPORTS_HINT}."
                ),
            }
        )
    if "code_execution" in enabled and "start_coworking" in enabled:
        system_messages.append(
            {
                "role": "system",
                "content": (
                    "Co-editing documents are available inside code_execution at "
                    "/workspace/cowork/ (paths listed in the tool result as cowork_files, "
                    "and in /workspace/cowork/manifest.json). That directory is on sys.path, "
                    "so .py cowork files can be imported by module name. subprocess is not "
                    "available. You may read and overwrite those files; changes sync back to "
                    "the shared editor. Use code_execution for analysis, CSV/Excel transforms, "
                    "plotting from cowork data, and other programmatic edits; keep using "
                    "cowork_str_replace for small textual patches."
                ),
            }
        )
    if "start_coworking" in enabled:
        system_messages.append(
            {
                "role": "system",
                "content": (
                    "Do not open a co-editing document by default. Answer in the chat for "
                    "questions, comparisons, explanations, research, short snippets, and analysis. "
                    "Call start_coworking only when the user clearly wants a shared, editable, or "
                    "downloadable artifact — for example they ask you to write/create a file, "
                    "report, spreadsheet, slide deck, or substantial code they will keep editing. "
                    "Never use coworking as a scratchpad for your own notes or as a place to dump "
                    "an answer that belongs in the chat. "
                    "If a coworking document is already open and the user is iterating on it, keep "
                    "editing that document. "
                    "When you do use coworking, edit it with cowork tools. "
                    "Editing policy (strict): after the document exists, default to small "
                    "cowork_str_replace patches (or cowork_append for new material at the end). "
                    "Call cowork_read first when you need the exact current text. "
                    "Each old_str must be a unique snippet with enough surrounding context. "
                    "Make several small replacements instead of one giant rewrite. "
                    "Do NOT call cowork_write to re-send the whole file for routine edits, "
                    "typo fixes, wording changes, or single-section updates — only use "
                    "cowork_write for a brand-new empty doc or a true full-structure rewrite "
                    "the user explicitly asked for. "
                    "For slide decks / presentations, use format=presentation and Marp markdown "
                    "(separate slides with a line containing only ---). "
                    "Marp quality rules (important — slides are a fixed 16:9 box): "
                    "always start with YAML front matter using theme: gaia (or uncover), "
                    "paginate: true, size: 16:9; prefer short headlines + ≤5 bullets per slide; "
                    "never cram wide comparison tables onto one slide — split by topic, use "
                    "short phrases, or two-column lists instead; leave breathing room "
                    "(generous whitespace); do not stack title + dense table + long footer "
                    "on the same slide (footers collide with content); one idea per slide; "
                    "use <!-- fit --> only on short title slides. "
                    "When editing presentations, replace one slide or one HTML/card block at a "
                    "time with cowork_str_replace — never rewrite the whole deck unless asked. "
                    "For charts/diagrams in slides, use a mermaid fenced block "
                    "(```mermaid … ```) — e.g. xychart-beta, flowchart, pie — so the preview "
                    "can render them; do not leave chart DSL as a plain ```xychart-beta fence "
                    "unless necessary (that still works in preview, but ```mermaid is preferred). "
                    "The co-editing document opens in the chat UI side panel (Document tab on "
                    "mobile). Never invent URLs, markdown links, /chat/... paths, or download "
                    "links for the document or its file_name — those are not real pages. Refer "
                    "to the document by title as plain text; the user downloads via the panel "
                    "Download button (presentations export as PDF or PPTX; other formats "
                    "download as the real source file)."
                ),
            }
        )
    return [*system_messages, *messages]


def _is_image_output_model(model: ChatModel) -> bool:
    return supports_image_output(model)


def _ensure_model_supports_image_attachments(
    model: ChatModel,
    attachments: Iterable[Any],
) -> None:
    has_image_attachments = any(
        (getattr(attachment, "content_type", "") or "").startswith("image/")
        for attachment in attachments
    )
    if has_image_attachments and not supports_image_input(model):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Images are not supported for this model",
        )


def _grounding_enabled(org: Org, provider: str) -> bool:
    if provider == "openai":
        return org.web_grounding_openai
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
    locale: str | None = None,
    memory_enabled: bool = False,
    user_id: UUID | None = None,
    agent_id: UUID | None = None,
    pending_attachments: list[dict[str, Any]] | None = None,
) -> ToolRegistry:
    image_model = get_image_model(
        session,
        str(org_id),
        preferred_provider=preferred_provider,
        user_id=user_id,
    )
    if not image_model:
        logger.info("No image model enabled for org_id=%s (tool still exposed)", org_id)
    registry = ToolRegistry()
    async def _handler(args: dict) -> object:
        return await generate_image(
            ImageToolContext(
                session=session,
                org_id=str(org_id),
                chat_id=str(chat_id) if chat_id else None,
                user_id=str(user_id) if user_id else None,
            ),
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
            ImageToolContext(
                session=session,
                org_id=str(org_id),
                chat_id=str(chat_id) if chat_id else None,
                user_id=str(user_id) if user_id else None,
            ),
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
            description=(
                "Edit an existing image with a prompt and optional mask. "
                "If no image_id or image_base64 is provided, the latest image attachment "
                "from the current chat is used."
            ),
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
    async def _extract_pdf_handler(args: dict) -> object:
        if not chat_id:
            return ToolResult(
                name="extract_pdf",
                output={"error": "No active chat context for PDF extraction."},
            )
        return await extract_pdf(
            PdfToolContext(
                session=session,
                chat_id=str(chat_id),
                pending_attachments=pending_attachments,
            ),
            attachment_id=args.get("attachment_id"),
            file_name=args.get("file_name"),
            page=args.get("page"),
            page_from=args.get("page_from"),
            page_to=args.get("page_to"),
        )

    registry.register(
        ToolSpec(
            name="extract_pdf",
            description=(
                "Extract text from a PDF attachment by page number or page range. "
                "Always returns total page_count so you can plan next calls."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "attachment_id": {
                        "type": "string",
                        "description": "Optional specific PDF attachment id from this chat",
                    },
                    "file_name": {
                        "type": "string",
                        "description": "Optional PDF file name match from this chat",
                    },
                    "page": {
                        "type": "integer",
                        "description": "1-based page number to extract",
                    },
                    "page_from": {
                        "type": "integer",
                        "description": "1-based start page for a range",
                    },
                    "page_to": {
                        "type": "integer",
                        "description": "1-based end page for a range",
                    },
                },
            },
        ),
        _extract_pdf_handler,
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
                description="Search the web for relevant results, gives you list of links and page summaries. Prefer to check your answers not imagine facts.",
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
                question=args.get("question"),
            )

        registry.register(
            ToolSpec(
                name="web_scrape",
                description=(
                    "Fetch a web page and return markdown, a full-page screenshot, "
                    "or a grounded answer from that page. For output=answer, include question. "
                    "Prefer using answer instead of poluting context with full page info unless it is needed to accomplish the task. "
                    "If you want to explore subpages, I reccomend first asking about all of the links that lead from the source page not guessing them."
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
                            "enum": ["markdown", "screenshot", "answer"],
                            "description": "Choose markdown text, screenshot, or grounded answer",
                        },
                        "question": {
                            "type": "string",
                            "description": "Required when output=answer. Question to answer from the page only.",
                        },
                    },
                },
            ),
            _scrape_handler,
        )
        async def _download_attachments_handler(args: dict) -> object:
            return await download_attachments(
                WebToolContext(org_id=str(org_id), locale=locale),
                url=args.get("url"),
                urls=args.get("urls"),
            )

        registry.register(
            ToolSpec(
                name="download_attachments",
                description=(
                    "Download one or more direct file URLs and attach them to the chat."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "Single direct file URL to download",
                        },
                        "urls": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Multiple direct file URLs to download",
                        },
                    },
                },
            ),
            _download_attachments_handler,
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
                    agent_id=str(agent_id) if agent_id else None,
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
                    "The sandbox has no network access; use web_search/web_scrape for internet data."
                    "Do not probe host system resources (CPU, RAM, cgroup, kernel, network interfaces)."
                    "All files from this chat are available as read-only under /inputs."
                    "When this chat belongs to a project, project source files are also available "
                    "as read-only under /inputs/project/ (original uploads when present, otherwise "
                    "extracted text). Filenames are <source_id>_<sanitized_name>."
                    "Co-editing documents for this chat are mounted read/write under /workspace/cowork/ "
                    "(see cowork_files in the tool result and /workspace/cowork/manifest.json). "
                    "That directory is on sys.path, so .py cowork files can be imported by module name "
                    "(import process_trs). subprocess is not available in the sandbox. "
                    "Read/analyze/transform them with pandas/openpyxl/etc., then overwrite the same "
                    "path to update the live document in the UI. Prefer this for spreadsheet math, "
                    "CSV transforms, chart data prep; use cowork_str_replace for small text edits."
                    "Write any output files to /outputs to return them to the user (images, resulting csv etc.)."
                    "YOu dont need to tell user where the file was created, it will be sent together with your response to them."
                    "You can call this tool multiple times. Chat attachment filenames are <attachment_id>_<sanitized_name>."
                    "Calls do not reuse same sandbox, so any created files will be lost after the call "
                    "(except cowork files you overwrite under /workspace/cowork/, which sync back)."
                    f"Allowed third-party imports: {ALLOWED_IMPORTS_HINT}."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "purpose": {
                            "type": "string",
                            "description": (
                                "Short human-readable purpose of running this code "
                                "(e.g. 'Summarize sales CSV by region'). "
                                "Do not list imports or paste code here."
                            ),
                        },
                        "code": {
                            "type": "string",
                            "description": "Python code to execute",
                        },
                        "language": {
                            "type": "string",
                            "description": "Execution language (python only)",
                        },
                    },
                    "required": ["purpose", "code"],
                },
            ),
            _exec_handler,
        )
    if memory_enabled and user_id:
        mem_ctx = MemoryToolContext(
            session=session,
            user_id=user_id,
            current_chat_id=chat_id,
            agent_id=agent_id,
        )

        async def _store_memory_handler(args: dict) -> object:
            return await store_memory(mem_ctx, content=args.get("content", ""))

        registry.register(
            ToolSpec(
                name="store_memory",
                description=(
                    "Store an important fact, preference, or instruction that the user wants "
                    "you to remember across conversations. Only store truly global/important "
                    "information or things the user explicitly asks you to remember. "
                    "Do NOT store transient chat-specific details."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "The memory to store (concise factual statement)",
                        },
                    },
                    "required": ["content"],
                },
            ),
            _store_memory_handler,
        )

        async def _remove_memory_handler(args: dict) -> object:
            return await remove_memory(mem_ctx, memory_id=args.get("memory_id", ""))

        registry.register(
            ToolSpec(
                name="remove_memory",
                description=(
                    "Remove a previously stored memory by its ID. Use when the user asks you "
                    "to forget something or when a stored fact is no longer accurate."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "memory_id": {
                            "type": "string",
                            "description": "The UUID of the memory to remove",
                        },
                    },
                    "required": ["memory_id"],
                },
            ),
            _remove_memory_handler,
        )

        async def _search_past_chats_handler(args: dict) -> object:
            return await search_past_chats(
                mem_ctx, query=args.get("query", ""), limit=args.get("limit", 10)
            )

        project_scope = (
            "Scoped to chats in the current project only. "
            if agent_id
            else "Scoped to personal chats outside projects. "
        )
        registry.register(
            ToolSpec(
                name="search_past_chats",
                description=(
                    "Search the user's past chat conversations by keyword. "
                    f"{project_scope}"
                    "Call this when prior context may matter instead of guessing. "
                    "Returns matching chat titles, chat IDs, created_at, last_activity_at "
                    "(last message / last modified), and message previews. "
                    "Results are ordered by most recently active chat first. "
                    "Results are shown as references the user can click to navigate to. "
                    "You can link to a found chat using /chat/{chat_id} in your response."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search keywords",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max results (default 10, max 20)",
                        },
                    },
                    "required": ["query"],
                },
            ),
            _search_past_chats_handler,
        )

    if agent_id:
        agent_ctx = AgentToolContext(
            session=session, agent_id=agent_id, user_id=user_id
        )

        try:
            from app.services.agents.chat_index import enqueue_missing_project_chat_indexes

            if user_id:
                enqueue_missing_project_chat_indexes(
                    session, agent_id=agent_id, user_id=user_id, limit=15
                )
        except Exception:
            logger.debug("Project chat backfill enqueue failed", exc_info=True)

        async def _list_project_sources_handler(args: dict) -> object:
            return await list_project_sources(
                agent_ctx,
                include_chats=_coerce_include_chats(args.get("include_chats")),
            )

        registry.register(
            ToolSpec(
                name="list_project_sources",
                description=(
                    "List sources in this project with numeric id, title, kind, summary, and "
                    "length. Includes indexed prior chats (kind=chat) by default. Set "
                    "include_chats=false to list only uploaded files/URLs/text when the user "
                    "wants answers grounded only in documents. Refer to sources by numeric id."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "include_chats": {
                            "type": "boolean",
                            "description": (
                                "Include indexed prior chats (default true). Set false for "
                                "documents only."
                            ),
                        },
                    },
                },
            ),
            _list_project_sources_handler,
        )

        async def _search_project_sources_handler(args: dict) -> object:
            return await search_project_sources(
                agent_ctx,
                query=args.get("query", ""),
                limit=args.get("limit", 8),
                include_chats=_coerce_include_chats(args.get("include_chats")),
            )

        registry.register(
            ToolSpec(
                name="search_project_sources",
                description=(
                    "Semantic search across this project's documents and, by default, indexed "
                    "prior chats. Returns passages with numeric source id, title, kind, and "
                    "snippet. Set include_chats=false when the user wants answers based only "
                    "on uploaded sources/files (ignore prior chat memory). Then call "
                    "read_project_source with that numeric id when you need more."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "What to look for"},
                        "limit": {
                            "type": "integer",
                            "description": "Max passages to return (default 8, max 20)",
                        },
                        "include_chats": {
                            "type": "boolean",
                            "description": (
                                "Include indexed prior chats (default true). Set false to "
                                "search uploaded documents only."
                            ),
                        },
                    },
                    "required": ["query"],
                },
            ),
            _search_project_sources_handler,
        )

        async def _read_project_source_handler(args: dict) -> object:
            return await read_project_source(
                agent_ctx,
                source_id=args.get("source_id", ""),
                offset=args.get("offset", 0),
                max_chars=args.get("max_chars", 8000),
            )

        registry.register(
            ToolSpec(
                name="read_project_source",
                description=(
                    "Read the full text of a project document by its numeric id. Returns a "
                    "chunk of characters starting at offset along with total_length_chars and "
                    "next_offset. If has_more is true, call again with next_offset to keep "
                    "reading. Use this to actually review a document rather than relying on "
                    "search snippets alone."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "source_id": {
                            "type": "integer",
                            "description": "The numeric source id from list/search results",
                        },
                        "offset": {
                            "type": "integer",
                            "description": "Character offset to start reading from (default 0)",
                        },
                        "max_chars": {
                            "type": "integer",
                            "description": "Max characters to return (default/max 8000)",
                        },
                    },
                    "required": ["source_id"],
                },
            ),
            _read_project_source_handler,
        )

    if chat_id:
        cowork_ctx = CoworkToolContext(session=session, chat_id=chat_id)

        async def _start_coworking_handler(args: dict) -> object:
            return await start_coworking(
                cowork_ctx,
                title=args.get("title"),
                file_name=args.get("file_name"),
                format=args.get("format"),
                language=args.get("language"),
                content=args.get("content"),
            )

        registry.register(
            ToolSpec(
                name="start_coworking",
                description=(
                    "Open a shared co-editing document in the chat UI (right panel on "
                    "desktop, Document tab on mobile). Use sparingly: only when the user "
                    "wants a persistent editable or downloadable artifact (a file, report, "
                    "presentation, spreadsheet, or substantial code). Do not use for ordinary "
                    "Q&A, comparisons, explanations, or scratch notes — those belong in the "
                    "chat. Creates a new active document and deactivates any previous one. "
                    "For presentations use format=presentation with Marp markdown. "
                    "Start with front matter like: ---\\nmarp: true\\ntheme: gaia\\n"
                    "paginate: true\\nsize: 16:9\\n--- then slides separated by a line "
                    "with only ---. Keep each slide sparse (headline + ≤5 short bullets); "
                    "never put large multi-column tables or long footers that overflow the "
                    "slide; split comparisons across slides. "
                    "After starting, put the first full draft in start_coworking's content "
                    "(or one cowork_write if the doc was empty). For every later change, "
                    "prefer cowork_str_replace / cowork_append — not full-file cowork_write. "
                    "Re-read with cowork_read when needed. "
                    "Do not invent URLs or markdown links to the file_name — it is not a "
                    "web path; the panel is the only UI for viewing/downloading."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "Short display title for the document",
                        },
                        "file_name": {
                            "type": "string",
                            "description": (
                                "Suggested download filename with extension only "
                                "(e.g. report.md, deck.md, app.py). Not a URL or chat path."
                            ),
                        },
                        "format": {
                            "type": "string",
                            "enum": [
                                "markdown",
                                "code",
                                "text",
                                "json",
                                "csv",
                                "presentation",
                            ],
                            "description": (
                                "Document format for editor + download MIME. "
                                "Use presentation for Marp slide decks."
                            ),
                        },
                        "language": {
                            "type": "string",
                            "description": "Language hint when format is code (python, ts, …)",
                        },
                        "content": {
                            "type": "string",
                            "description": "Optional initial content",
                        },
                    },
                },
            ),
            _start_coworking_handler,
        )

        async def _cowork_read_handler(args: dict) -> object:
            return await cowork_read(
                cowork_ctx,
                offset=args.get("offset"),
                limit=args.get("limit"),
            )

        registry.register(
            ToolSpec(
                name="cowork_read",
                description=(
                    "Read the active coworking document. Optionally pass line offset/limit "
                    "for large files (0-based line offset)."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "offset": {
                            "type": "integer",
                            "description": "0-based line offset (default 0)",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max lines to return (default all, max 2000)",
                        },
                    },
                },
            ),
            _cowork_read_handler,
        )

        async def _cowork_str_replace_handler(args: dict) -> object:
            return await cowork_str_replace(
                cowork_ctx,
                old_str=args.get("old_str", ""),
                new_str=args.get("new_str", ""),
                replace_all=bool(args.get("replace_all", False)),
            )

        registry.register(
            ToolSpec(
                name="cowork_str_replace",
                description=(
                    "PREFERRED edit tool. Exact search/replace in the active coworking "
                    "document for small, targeted changes (one slide, one function, one "
                    "paragraph, one HTML card, etc.). old_str must match exactly once "
                    "unless replace_all=true — include unique surrounding context. "
                    "Call cowork_read first if unsure. Prefer multiple str_replace calls "
                    "over cowork_write. Fails if not found or ambiguous."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "old_str": {
                            "type": "string",
                            "description": "Exact text to find",
                        },
                        "new_str": {
                            "type": "string",
                            "description": "Replacement text",
                        },
                        "replace_all": {
                            "type": "boolean",
                            "description": "Replace every match (default false)",
                        },
                    },
                    "required": ["old_str", "new_str"],
                },
            ),
            _cowork_str_replace_handler,
        )

        async def _cowork_append_handler(args: dict) -> object:
            return await cowork_append(cowork_ctx, text=args.get("text", ""))

        registry.register(
            ToolSpec(
                name="cowork_append",
                description=(
                    "Append text to the end of the active coworking document. Prefer this "
                    "over cowork_write when adding a new slide, section, or trailing content."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": "Text to append",
                        },
                    },
                    "required": ["text"],
                },
            ),
            _cowork_append_handler,
        )

        async def _cowork_write_handler(args: dict) -> object:
            return await cowork_write(cowork_ctx, content=args.get("content", ""))

        registry.register(
            ToolSpec(
                name="cowork_write",
                description=(
                    "LAST RESORT: replace the entire active coworking document. "
                    "Do not use for normal edits. Prefer cowork_str_replace (or "
                    "cowork_append) for any change that touches only part of the file. "
                    "Allowed only when (1) the document is empty / you are creating the "
                    "first full draft and did not pass content to start_coworking, or "
                    "(2) the user explicitly asked for a full rewrite / restructure."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "Full new document content",
                        },
                    },
                    "required": ["content"],
                },
            ),
            _cowork_write_handler,
        )

    register_mcp_tools(registry)

    logger.info("Registered tools: %s", [tool.name for tool in registry.list_specs()])
    return registry


def _sanitize_attachment_filename(name: str) -> str:
    base = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", base).strip("._")
    return cleaned or "file"


def _attachment_exec_path(attachment: ChatMessageAttachment) -> str:
    safe_name = _sanitize_attachment_filename(attachment.file_name)
    return f"/inputs/{attachment.id}_{safe_name}"


def _image_attachment_metadata(attachment: ChatMessageAttachment) -> str | None:
    if not (attachment.content_type or "").lower().startswith("image/"):
        return None
    if Image is None:
        return None
    try:
        payload = base64.b64decode(attachment.data_base64)
        with Image.open(BytesIO(payload)) as image:
            width, height = image.size
            parts = [f"width={width}", f"height={height}"]
            exif_text = ""
            try:
                exif_raw = image.getexif()
                if exif_raw:
                    tag_map = ExifTags.TAGS if ExifTags else {}
                    preferred = [
                        "Make",
                        "Model",
                        "DateTime",
                        "DateTimeOriginal",
                        "LensModel",
                        "Orientation",
                    ]
                    picked: dict[str, str] = {}
                    for key, value in exif_raw.items():
                        name = tag_map.get(key, str(key)) if isinstance(key, int) else str(key)
                        if name in preferred and value not in (None, ""):
                            picked[name] = str(value)
                    if picked:
                        exif_text = ", ".join(f"{k}={v}" for k, v in picked.items())
                    else:
                        exif_text = f"{len(exif_raw)} tags"
            except Exception:
                exif_text = ""
            if exif_text:
                parts.append(f"exif={exif_text}")
            return "; ".join(parts)
    except Exception:
        return None


def _attachment_lines(attachments: list[ChatMessageAttachment]) -> list[str]:
    lines: list[str] = []
    has_non_image = any(
        not (attachment.content_type or "").lower().startswith("image/")
        for attachment in attachments
    )
    if has_non_image:
        lines.append(
            "Use the code_execution tool to read/analyze these files before answering."
        )
    elif attachments:
        lines.append(
            "Image metadata is already provided below; avoid code_execution unless deeper pixel-level analysis is required."
        )
    for attachment in attachments:
        metadata = _image_attachment_metadata(attachment)
        metadata_suffix = f"; metadata: {metadata}" if metadata else ""
        lines.append(
            f"- {attachment.file_name} ({attachment.content_type}) at {_attachment_exec_path(attachment)}{metadata_suffix}"
        )
    return lines


def _source_item(url: str, title: str | None = None) -> dict:
    host = ""
    if url.startswith("/chat/"):
        host = "chat"
    else:
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


def _source_identity(item: dict) -> str | None:
    source_id = item.get("source_id")
    if source_id is not None and str(source_id).strip():
        return f"id:{str(source_id).strip()}"
    url = item.get("url")
    if isinstance(url, str) and url.strip():
        return f"url:{url.strip()}"
    title = item.get("title")
    if isinstance(title, str) and title.strip():
        return f"title:{title.strip().casefold()}"
    return None


def _dedupe_sources(items: list[dict] | None) -> list[dict]:
    """Keep first occurrence of each source; same site/file counted once."""
    if not items:
        return []
    seen: set[str] = set()
    unique: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        key = _source_identity(item)
        if key is None:
            unique.append(item)
            continue
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _limit_sources(items: list[dict] | None, max_items: int | None = None) -> list[dict]:
    """Dedupe sources. max_items is accepted for callers but not applied."""
    del max_items
    if not items:
        return []
    return _dedupe_sources(items)


def _sanitize_tool_output_for_context(value: object) -> object:
    if isinstance(value, dict):
        result: dict[str, object] = {}
        for key, item in value.items():
            if key == "data_base64":
                result[key] = "[omitted_base64]"
                continue
            result[key] = _sanitize_tool_output_for_context(item)
        return result
    if isinstance(value, list):
        limited = value[:50]
        return [_sanitize_tool_output_for_context(item) for item in limited]
    if isinstance(value, str):
        if len(value) > 4000:
            return value[:4000] + "...[truncated]"
        return value
    return value


def _tool_call_input_preview(name: str, arguments: dict[str, Any]) -> str:
    from app.services.tools.previews import tool_call_input_preview

    return tool_call_input_preview(name, arguments)


def _tool_call_output_preview(name: str, output: dict[str, Any]) -> str:
    from app.services.tools.previews import tool_call_output_preview

    return tool_call_output_preview(name, output)


def _parse_web_answer_payload(content: str) -> tuple[str, list[str], bool]:
    text = (content or "").strip()
    if not text:
        return "", [], True
    try:
        payload = repair_json_loads(text)
    except Exception:
        return text, [], False
    if not isinstance(payload, dict):
        return text, [], False
    answer = str(payload.get("answer") or "").strip()
    insufficient = bool(payload.get("insufficient_information"))
    quotes_raw = payload.get("quotes")
    quotes: list[str] = []
    if isinstance(quotes_raw, list):
        for item in quotes_raw:
            if isinstance(item, str):
                quote = item.strip()
                if quote:
                    quotes.append(quote)
    return answer, quotes, insufficient


def _build_web_answer_markdown_context(markdown: str) -> str:
    if len(markdown) <= WEB_SCRAPE_ANSWER_MARKDOWN_LIMIT:
        return markdown
    head_len = int(WEB_SCRAPE_ANSWER_MARKDOWN_LIMIT * WEB_SCRAPE_ANSWER_HEAD_RATIO)
    head_len = max(head_len, 1)
    tail_len = max(WEB_SCRAPE_ANSWER_MARKDOWN_LIMIT - head_len, 1)
    return (
        markdown[:head_len].rstrip()
        + "\n\n[... source content omitted for length ...]\n\n"
        + markdown[-tail_len:].lstrip()
    )


def _merge_chat_usage(
    first: ChatUsage | None,
    second: ChatUsage | None,
) -> ChatUsage | None:
    if first is None and second is None:
        return None
    if first is None:
        return second
    if second is None:
        return first
    return ChatUsage(
        prompt_tokens=(first.prompt_tokens or 0) + (second.prompt_tokens or 0),
        completion_tokens=(first.completion_tokens or 0)
        + (second.completion_tokens or 0),
        total_tokens=(first.total_tokens or 0) + (second.total_tokens or 0),
        input_tokens=(first.input_tokens or 0) + (second.input_tokens or 0),
        output_tokens=(first.output_tokens or 0) + (second.output_tokens or 0),
        cached_tokens=(first.cached_tokens or 0) + (second.cached_tokens or 0),
        thinking_tokens=(first.thinking_tokens or 0) + (second.thinking_tokens or 0),
    )


def _validation_error_text(exc: ValidationError) -> str:
    try:
        entries: list[str] = []
        for item in exc.errors()[:5]:
            location = ".".join(str(part) for part in item.get("loc", [])) or "payload"
            message = str(item.get("msg", "invalid value"))
            entries.append(f"{location}: {message}")
        if entries:
            return "Invalid payload: " + "; ".join(entries)
    except Exception:
        pass
    return "Invalid payload"


async def _generate_web_scrape_answer(
    *,
    provider,
    model: ChatModel,
    result_item: dict[str, Any],
) -> tuple[dict[str, Any], ChatUsage | None]:
    analysis_input = result_item.get("analysis_input")
    if not isinstance(analysis_input, dict):
        return result_item, None
    question = str(result_item.get("question") or "").strip()
    if not question:
        return {**result_item, "error": "Question missing for output=answer"}, None
    markdown = str(analysis_input.get("markdown") or "").strip()
    if not markdown:
        return {**result_item, "error": "No markdown extracted for answering"}, None

    instructions = (
        "Analyze the provided website content and answer the question.\n"
        f'Question: "{question}"\n\n'
        "Rules:\n"
        "1) Use only the provided source material (markdown and screenshot if present).\n"
        "2) If the source material is insufficient, say so clearly.\n"
        "3) Do not use prior/personal knowledge.\n"
        "4) Return strict JSON with keys: answer (string), insufficient_information (boolean), quotes (array of strings).\n"
        "5) quotes must contain direct quotes from the source, or be [] when insufficient."
    )
    markdown_block = _build_web_answer_markdown_context(markdown)

    user_content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": instructions + "\n\nSource markdown:\n" + markdown_block,
        }
    ]
    screenshot_base64 = str(analysis_input.get("screenshot_base64") or "")
    screenshot_content_type = (
        str(analysis_input.get("screenshot_content_type") or "image/png").strip()
        or "image/png"
    )
    include_image = screenshot_base64 and supports_image_input(model)
    if include_image:
        user_content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{screenshot_content_type};base64,{screenshot_base64}"
                },
            }
        )
    prompt_messages = [
        {
            "role": "system",
            "content": (
                "You are a strict evidence-grounded website analyzer. "
                "Never rely on outside knowledge."
            ),
        },
        {"role": "user", "content": user_content},
    ]
    try:
        response = await provider.chat(model.model_name, prompt_messages)
    except Exception:
        if include_image:
            prompt_messages[1] = {"role": "user", "content": user_content[:1]}
            response = await provider.chat(model.model_name, prompt_messages)
        else:
            raise
    answer, quotes, insufficient = _parse_web_answer_payload(response.content or "")
    normalized: dict[str, Any] = {k: v for k, v in result_item.items() if k != "analysis_input"}
    normalized["answer"] = answer
    normalized["quotes"] = quotes
    normalized["insufficient_information"] = insufficient
    if analysis_input.get("screenshot_error") and not screenshot_base64:
        normalized["note"] = "Screenshot could not be used; answer is based on markdown only."
    return normalized, response.usage


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


_PLACEHOLDER_CHAT_TITLES = frozenset(
    {
        "new chat",
        "untitled",
        "untitled chat",
        "jauns čats",
        "bez nosaukuma",
        "新しいチャット",
        "無題",
    }
)


async def _maybe_update_chat_title(
    *,
    session: Session,
    chat: Chat,
    model: ChatModel,
    history: list[ChatMessage],
) -> str | None:
    session.refresh(chat)
    existing_title = (chat.title or "").strip()
    if existing_title and existing_title.casefold() not in _PLACEHOLDER_CHAT_TITLES:
        return None
    user_message = next(
        (item for item in history if item.role == "user"),
        None,
    )
    if user_message is None:
        return None
    title_prompt = (user_message.content or "").strip()
    if not title_prompt:
        title_prompt = "[image attached]"

    title_model = model
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
        if not fallback:
            logger.warning(
                "No chat model found for title generation (provider=%s)", model.provider
            )
            return None
        title_model = fallback

    try:
        provider_config = require_provider_enabled(
            session, chat.org_id, title_model.provider
        )
        config = None
        if provider_config and provider_config.config_json:
            try:
                config = json.loads(provider_config.config_json)
            except json.JSONDecodeError:
                pass
        # Fresh client: not the reply stream, no reasoning, no chat prompt cache.
        title_provider = get_provider(
            title_model.provider,
            api_key=provider_config.api_key_override if provider_config else None,
            base_url=provider_config.base_url_override if provider_config else None,
            endpoint=provider_config.endpoint_override if provider_config else None,
            prefer_responses_api=title_model.uses_responses_api is True,
            config=config,
            openrouter_endpoint=title_model.openrouter_endpoint,
            prompt_cache_enabled=False,
        )
    except Exception:
        logger.warning(
            "Could not build title provider for chat_id=%s", chat.id, exc_info=True
        )
        return None

    title_messages = [
        {
            "role": "system",
            "content": "Create a concise chat title (max 6 words) that summarizes the user's question. Reply with the title only. Don't use markdown or other formatting.",
        },
        {"role": "user", "content": title_prompt},
    ]
    try:
        title_response = await title_provider.chat(title_model.model_name, title_messages)
        title_usage = getattr(title_response, "usage", None)
        if title_usage:
            latest_assistant = next(
                (
                    item
                    for item in reversed(history)
                    if isinstance(item, ChatMessage) and item.role == "assistant"
                ),
                None,
            )
            session.add(
                UsageEvent(
                    org_id=chat.org_id,
                    user_id=chat.user_id,
                    chat_id=chat.id,
                    message_id=latest_assistant.id if latest_assistant else None,
                    model_id=title_model.id,
                    prompt_tokens=title_usage.prompt_tokens,
                    completion_tokens=title_usage.completion_tokens,
                    total_tokens=title_usage.total_tokens,
                    input_tokens=title_usage.input_tokens,
                    output_tokens=title_usage.output_tokens,
                    cached_tokens=title_usage.cached_tokens,
                    thinking_tokens=title_usage.thinking_tokens,
                )
            )
        title = title_response.content.strip().strip('"').strip("'")
        if title:
            chat.title = title
        session.add(chat)
        session.commit()
        persist_responses_api_discovery(session, title_model, title_provider)
        return title or None
    except Exception:
        logger.warning(
            "Failed to generate chat title for chat_id=%s model=%s",
            chat.id,
            title_model.model_name,
            exc_info=True,
        )
        return None


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
    claims = decode_access_token_claims(token)
    subject = claims.get("sub")
    if not subject:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        )
    user_id = UUID(str(subject))
    user = session.exec(select(User).where(User.id == user_id)).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found"
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Inactive user"
        )
    token_version = claims.get("ver", 0)
    if int(token_version) != int(user.token_version):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        )
    return user


async def _ws_send_event(websocket: WebSocket, payload: dict) -> None:
    try:
        await websocket.send_json(payload)
    except WebSocketDisconnect:
        raise
    except RuntimeError as exc:
        # Client already closed; Starlette raises this after a close frame.
        if "close message has been sent" in str(exc):
            raise WebSocketDisconnect() from exc
        raise


async def _ws_try_send_event(websocket: WebSocket, payload: dict) -> bool:
    """Send a WS payload; return False if the client already disconnected."""
    try:
        await _ws_send_event(websocket, payload)
        return True
    except WebSocketDisconnect:
        return False


def _format_model_error(exc: Exception) -> str:
    message = str(exc)
    lowered = message.lower()
    if "not a chat model" in lowered:
        return "Selected model does not support chat completions. Choose a chat-capable model."
    return f"Model error: {message}"


def _enqueue_generation_task(task_id: UUID) -> None:
    celery_app.send_task(
        "chatui.generate_chat_response",
        args=[str(task_id)],
        task_id=str(task_id),
    )


def _is_timeline_action_label(label: str) -> bool:
    if re.match(r"^Step \d+/\d+$", label):
        return False
    return label not in {"Thinking", "Answering"}


def _append_stream_text_part(parts: list[dict[str, Any]], delta: str) -> None:
    if not delta:
        return
    if parts and parts[-1].get("type") == "text":
        parts[-1]["text"] = f"{parts[-1].get('text', '')}{delta}"
    else:
        parts.append({"type": "text", "text": delta})


def _attach_stream_action_attachments(
    parts: list[dict[str, Any]],
    label: str,
    attachments: list[dict[str, Any]],
) -> None:
    if not attachments:
        return
    # Prefer an empty matching action so repeated labels (e.g. three
    # "Generating image" steps) each get their own attachments.
    for index in range(len(parts) - 1, -1, -1):
        part = parts[index]
        if part.get("type") != "action" or part.get("label") != label:
            continue
        existing = part.get("attachments")
        if isinstance(existing, list) and existing:
            continue
        part["attachments"] = attachments
        return
    for index in range(len(parts) - 1, -1, -1):
        part = parts[index]
        if part.get("type") != "action" or part.get("label") != label:
            continue
        existing = part.get("attachments")
        if isinstance(existing, list) and existing:
            part["attachments"] = [*existing, *attachments]
        else:
            part["attachments"] = attachments
        return
    parts.append({"type": "action", "label": label, "attachments": attachments})


def _is_specialized_tool_event(payload: dict[str, Any]) -> bool:
    return payload.get("type") in {
        "code_execution",
        "url_attachments",
        "context_summary",
        "coworking",
        "reasoning",
    }


def _tool_event_action_label(payload: dict[str, Any]) -> str:
    event_type = payload.get("type")
    if event_type == "reasoning":
        return "Thoughts"
    if event_type == "tool_call":
        summary = payload.get("action_summary")
        if isinstance(summary, str) and summary.strip():
            return summary.strip()
        tool_name = payload.get("tool_name")
        if tool_name == "generate_image":
            return "Generating image"
        if tool_name == "edit_image":
            return "Editing image"
        if tool_name == "download_attachments":
            return "Downloading attachments"
        if tool_name == "code_execution":
            return "Running code"
        if tool_name == "extract_pdf":
            return "Extracting PDF"
        if isinstance(tool_name, str) and tool_name.strip():
            return f"Running {tool_name}"
        return "Running tool"
    if event_type == "code_execution":
        return "Running code"
    if event_type == "url_attachments":
        return "Downloading attachments"
    if event_type == "context_summary":
        return "Summarizing context"
    if event_type == "coworking":
        action = payload.get("action")
        if action == "open":
            return "Opening co-editing"
        if action == "update":
            return "Updating document"
        if action == "close":
            return "Closing co-editing"
        return "Co-editing"
    return "Running tool"


def _action_label_matches_tool_event(label: str, payload: dict[str, Any]) -> bool:
    event_type = payload.get("type")
    if event_type == "reasoning":
        return label == "Thoughts"
    if event_type == "tool_call":
        summary = payload.get("action_summary")
        if isinstance(summary, str) and summary.strip():
            return label == summary.strip()
        tool_name = payload.get("tool_name")
        if tool_name == "generate_image":
            return label == "Generating image"
        if tool_name == "edit_image":
            return label == "Editing image"
        if tool_name == "download_attachments":
            return label == "Downloading attachments"
        if tool_name == "code_execution":
            return label == "Running code" or label.startswith("Running code (")
        if tool_name == "extract_pdf":
            return label == "Extracting PDF"
        return False
    if event_type == "code_execution":
        return label == "Running code" or label.startswith("Running code (")
    if event_type == "url_attachments":
        return label == "Downloading attachments"
    if event_type == "context_summary":
        return label == "Summarizing context" or "summar" in label.lower()
    if event_type == "coworking":
        return (
            label in {"Opening co-editing", "Updating document", "Closing co-editing", "Co-editing"}
            or "co-edit" in label.lower()
            or "cowork" in label.lower()
        )
    return False


def _attach_stream_action_tool_event(
    parts: list[dict[str, Any]],
    payload: dict[str, Any],
) -> None:
    event_id = payload.get("id")
    if isinstance(event_id, str) and event_id:
        for index in range(len(parts) - 1, -1, -1):
            part = parts[index]
            if part.get("type") != "action":
                continue
            existing = part.get("tool_event")
            if isinstance(existing, dict) and existing.get("id") == event_id:
                part["tool_event"] = payload
                return
    # Reasoning episodes must not coalesce by label — each distinct id is its own action.
    if payload.get("type") == "reasoning":
        parts.append(
            {
                "type": "action",
                "label": _tool_event_action_label(payload),
                "tool_event": payload,
            }
        )
        return
    for index in range(len(parts) - 1, -1, -1):
        part = parts[index]
        if part.get("type") != "action":
            continue
        label = part.get("label")
        if not isinstance(label, str) or not _action_label_matches_tool_event(label, payload):
            continue
        existing = part.get("tool_event")
        if (
            isinstance(existing, dict)
            and _is_specialized_tool_event(existing)
            and payload.get("type") == "tool_call"
        ):
            return
        if (
            isinstance(existing, dict)
            and not _is_specialized_tool_event(payload)
            and existing.get("id") != event_id
        ):
            continue
        part["tool_event"] = payload
        return
    parts.append(
        {
            "type": "action",
            "label": _tool_event_action_label(payload),
            "tool_event": payload,
        }
    )


def _normalize_timeline_attachments(
    raw_attachments: Any,
    message_attachments: list["ChatMessageAttachmentRead"] | None = None,
) -> list[dict[str, Any]]:
    """Map tool-event attachment stubs onto message attachments.

    ``message_attachments`` is treated as a consumable pool: each match is
    removed so repeated names like ``generated.png`` remap 1:1 in order
    instead of all collapsing onto the last file.
    """
    if not isinstance(raw_attachments, list):
        return []
    pool = message_attachments if message_attachments is not None else []
    normalized: list[dict[str, Any]] = []
    for item in raw_attachments:
        if not isinstance(item, dict):
            continue
        file_name = str(item.get("file_name") or "").strip()
        content_type = str(item.get("content_type") or "").strip() or "application/octet-stream"
        raw_id = item.get("id")
        matched = None
        if isinstance(raw_id, str) and raw_id.strip():
            wanted = raw_id.strip()
            for index, candidate in enumerate(pool):
                if candidate.id == wanted:
                    matched = pool.pop(index)
                    break
        if matched is None and file_name:
            for index, candidate in enumerate(pool):
                if candidate.file_name == file_name:
                    matched = pool.pop(index)
                    break
        entry: dict[str, Any] = {
            "file_name": file_name or (matched.file_name if matched else "attachment"),
            "content_type": matched.content_type if matched else content_type,
        }
        if matched:
            entry["id"] = matched.id
            if matched.content_url:
                entry["content_url"] = matched.content_url
        data_base64 = item.get("data_base64")
        if isinstance(data_base64, str) and data_base64 and "content_url" not in entry:
            entry["data_base64"] = data_base64
        if entry.get("content_url") or entry.get("data_base64"):
            normalized.append(entry)
    return normalized


def _build_stream_parts_from_events(
    events: list[ChatGenerationEvent],
    *,
    message_content: str = "",
    message_attachments: list["ChatMessageAttachmentRead"] | None = None,
) -> tuple[list[dict[str, Any]] | None, list[str]]:
    """Rebuild interleaved timeline + action labels from persisted generation events."""
    parts: list[dict[str, Any]] = []
    thinking_steps: list[str] = []
    # Consumable copy so duplicate file names (e.g. generated.png) remap uniquely.
    attachment_pool = list(message_attachments or [])
    for event in events:
        payload = event.payload_json if isinstance(event.payload_json, dict) else {}
        if event.event_type == "delta":
            delta = payload.get("delta")
            if isinstance(delta, str) and delta:
                _append_stream_text_part(parts, delta)
            continue
        if event.event_type == "activity":
            label = payload.get("label")
            state = payload.get("state")
            if not isinstance(label, str) or not label:
                continue
            if state == "start":
                if re.match(r"^Step \d+/\d+$", label):
                    thinking_steps = [
                        item for item in thinking_steps if not re.match(r"^Step \d+/\d+$", item)
                    ]
                    thinking_steps.append(label)
                else:
                    thinking_steps.append(label)
                    if _is_timeline_action_label(label):
                        parts.append({"type": "action", "label": label})
            continue
        if event.event_type != "tool_event":
            continue
        _attach_stream_action_tool_event(parts, payload)
        if payload.get("type") != "tool_call" or payload.get("state") != "end":
            continue
        output = payload.get("output")
        raw_attachments = output.get("attachments") if isinstance(output, dict) else None
        attachments = _normalize_timeline_attachments(raw_attachments, attachment_pool)
        if not attachments:
            continue
        label = payload.get("action_summary")
        if not isinstance(label, str) or not label.strip():
            label = _tool_event_action_label(payload)
        _attach_stream_action_attachments(parts, label, attachments)

    parts_text = "".join(
        str(part.get("text") or "") for part in parts if part.get("type") == "text"
    )
    content = message_content or ""
    if content and parts_text != content:
        if not parts_text:
            parts.insert(0, {"type": "text", "text": content})
        elif content.startswith(parts_text):
            remainder = content[len(parts_text) :]
            if remainder:
                _append_stream_text_part(parts, remainder)
        elif not any(part.get("type") == "action" for part in parts):
            parts = [{"type": "text", "text": content}]

    thinking_steps = [
        label for label in thinking_steps if _is_timeline_action_label(label)
    ]
    return (parts or None, thinking_steps)


def _event_payload_from_record(event: ChatGenerationEvent) -> dict:
    payload = event.payload_json or {}
    if event.event_type == "activity":
        payload = {"activity": payload}
    elif event.event_type == "tool_event":
        payload = {"tool_event": payload}
    payload.setdefault("task_id", str(event.task_id))
    return payload


def _event_payload_from_notification(
    task_id: UUID, notification: dict[str, Any]
) -> dict | None:
    event_type = notification.get("event_type")
    if not isinstance(event_type, str):
        return None
    payload = notification.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    if event_type == "activity":
        payload = {"activity": payload}
    elif event_type == "tool_event":
        payload = {"tool_event": payload}
    else:
        payload = dict(payload)
    payload.setdefault("task_id", str(task_id))
    return payload


def _fetch_generation_events_after(
    session: Session, task_id: UUID, after_sequence: int
) -> list[ChatGenerationEvent]:
    return session.exec(
        select(ChatGenerationEvent)
        .where(ChatGenerationEvent.task_id == task_id)
        .where(ChatGenerationEvent.sequence > after_sequence)
        .order_by(ChatGenerationEvent.sequence)
    ).all()


def _task_is_terminal(session: Session, task_id: UUID) -> bool:
    task = session.exec(
        select(ChatGenerationTask).where(ChatGenerationTask.id == task_id)
    ).first()
    return bool(
        task
        and task.status
        in {
            GenerationStatus.completed,
            GenerationStatus.failed,
            GenerationStatus.cancelled,
        }
    )


async def _stream_task_events_ws(
    websocket: WebSocket, task_id: UUID, *, after_sequence: int = 0
) -> None:
    last_sequence = after_sequence
    # Initial catch-up from DB, then prefer Redis pub/sub with periodic DB fallback.
    with Session(engine) as stream_session:
        events = _fetch_generation_events_after(stream_session, task_id, last_sequence)
        for event in events:
            last_sequence = event.sequence
            await _ws_send_event(websocket, _event_payload_from_record(event))
        if _task_is_terminal(stream_session, task_id) and not events:
            return

    try:
        async for notification in iter_generation_notifications(task_id):
            if notification is not None:
                sequence = notification.get("sequence")
                if isinstance(sequence, int) and sequence > last_sequence:
                    # Prefer contiguous notify payloads; fall back to DB on gaps.
                    if sequence == last_sequence + 1:
                        payload = _event_payload_from_notification(task_id, notification)
                        if payload is not None:
                            last_sequence = sequence
                            await _ws_send_event(websocket, payload)
                            if notification.get("event_type") in {"done", "error"}:
                                with Session(engine) as stream_session:
                                    events = _fetch_generation_events_after(
                                        stream_session, task_id, last_sequence
                                    )
                                    for event in events:
                                        last_sequence = event.sequence
                                        await _ws_send_event(
                                            websocket, _event_payload_from_record(event)
                                        )
                                return
                            continue
                    with Session(engine) as stream_session:
                        events = _fetch_generation_events_after(
                            stream_session, task_id, last_sequence
                        )
                        for event in events:
                            last_sequence = event.sequence
                            await _ws_send_event(
                                websocket, _event_payload_from_record(event)
                            )
                        if notification.get("event_type") in {"done", "error"}:
                            return
                continue

            with Session(engine) as stream_session:
                events = _fetch_generation_events_after(
                    stream_session, task_id, last_sequence
                )
                for event in events:
                    last_sequence = event.sequence
                    await _ws_send_event(websocket, _event_payload_from_record(event))
                if _task_is_terminal(stream_session, task_id) and not events:
                    return
    except WebSocketDisconnect:
        raise
    except Exception:
        logging.getLogger(__name__).debug(
            "Generation pub/sub unavailable; falling back to DB poll task=%s",
            task_id,
            exc_info=True,
        )
        while True:
            with Session(engine) as stream_session:
                events = _fetch_generation_events_after(
                    stream_session, task_id, last_sequence
                )
                for event in events:
                    last_sequence = event.sequence
                    await _ws_send_event(websocket, _event_payload_from_record(event))
                if _task_is_terminal(stream_session, task_id) and not events:
                    return
            await anyio.sleep(0.5)


async def _stream_task_events_sse(task_id: UUID, *, after_sequence: int = 0):
    last_sequence = after_sequence
    with Session(engine) as stream_session:
        events = _fetch_generation_events_after(stream_session, task_id, last_sequence)
        for event in events:
            last_sequence = event.sequence
            payload = _event_payload_from_record(event)
            yield f"data: {json.dumps(payload)}\n\n"
        if _task_is_terminal(stream_session, task_id) and not events:
            return

    try:
        async for notification in iter_generation_notifications(task_id):
            if notification is not None:
                sequence = notification.get("sequence")
                if isinstance(sequence, int) and sequence > last_sequence:
                    if sequence == last_sequence + 1:
                        payload = _event_payload_from_notification(task_id, notification)
                        if payload is not None:
                            last_sequence = sequence
                            yield f"data: {json.dumps(payload)}\n\n"
                            if notification.get("event_type") in {"done", "error"}:
                                with Session(engine) as stream_session:
                                    events = _fetch_generation_events_after(
                                        stream_session, task_id, last_sequence
                                    )
                                    for event in events:
                                        last_sequence = event.sequence
                                        payload = _event_payload_from_record(event)
                                        yield f"data: {json.dumps(payload)}\n\n"
                                return
                            continue
                    with Session(engine) as stream_session:
                        events = _fetch_generation_events_after(
                            stream_session, task_id, last_sequence
                        )
                        for event in events:
                            last_sequence = event.sequence
                            payload = _event_payload_from_record(event)
                            yield f"data: {json.dumps(payload)}\n\n"
                        if notification.get("event_type") in {"done", "error"}:
                            return
                continue

            with Session(engine) as stream_session:
                events = _fetch_generation_events_after(
                    stream_session, task_id, last_sequence
                )
                for event in events:
                    last_sequence = event.sequence
                    payload = _event_payload_from_record(event)
                    yield f"data: {json.dumps(payload)}\n\n"
                if _task_is_terminal(stream_session, task_id) and not events:
                    return
    except Exception:
        logging.getLogger(__name__).debug(
            "Generation pub/sub unavailable; falling back to DB poll task=%s",
            task_id,
            exc_info=True,
        )
        while True:
            with Session(engine) as stream_session:
                events = _fetch_generation_events_after(
                    stream_session, task_id, last_sequence
                )
                for event in events:
                    last_sequence = event.sequence
                    payload = _event_payload_from_record(event)
                    yield f"data: {json.dumps(payload)}\n\n"
                if _task_is_terminal(stream_session, task_id) and not events:
                    return
            await anyio.sleep(0.5)


async def _run_agentic_loop(
    *,
    provider,
    model: ChatModel,
    messages: list[dict],
    tool_registry: ToolRegistry,
    pending_attachments: list[dict[str, Any]] | None = None,
    activity_sender: anyio.abc.ObjectSendStream | None = None,
    tool_event_sender: anyio.abc.ObjectSendStream | None = None,
    delta_sender: anyio.abc.ObjectSendStream | None = None,
) -> tuple[str, list[dict], list[dict], list[dict], ChatUsage | None]:
    from app.services.langchain_runtime import run_agentic_loop_langchain

    return await run_agentic_loop_langchain(
        provider=provider,
        model_name=model.model_name,
        messages=messages,
        tool_registry=tool_registry,
        max_steps=MAX_TOOL_STEPS,
        pending_attachments=pending_attachments,
        activity_sender=activity_sender,
        tool_event_sender=tool_event_sender,
        delta_sender=delta_sender,
    )

    tool_specs = tool_registry.list_specs()
    attachments: list[dict] = pending_attachments if pending_attachments is not None else []
    sources: list[dict] = []
    image_usages: list[dict] = []
    last_usage: ChatUsage | None = None
    additional_usage: ChatUsage | None = None
    last_tool_error: str | None = None
    search_calls = 0
    scrape_calls = 0
    failed_scrape_urls: set[str] = set()
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
            if str(arguments.get("output") or "").strip().lower() == "answer":
                return ["Reading sources", "Analyzing source"]
            return ["Reading sources"]
        if name == "download_attachments":
            return ["Downloading attachments"]
        if name == "extract_pdf":
            return ["Extracting PDF pages"]
        if name == "generate_image":
            return ["Generating image"]
        if name == "edit_image":
            return ["Editing image"]
        if name == "code_execution":
            return ["Executing code"]
        return [f"Running {name}"]
    consecutive_max_tokens = 0
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
            last_usage = _merge_chat_usage(last_usage, response.usage)
            tool_calls = response.tool_calls or []
            logger.info(
                "Agentic step %s tool_calls=%s finish_reason=%s response_len=%s",
                step_label,
                len(tool_calls),
                response.finish_reason,
                len(response.content or ""),
            )
            if str(response.finish_reason or "") in ("max_tokens", "length") and not response.content:
                consecutive_max_tokens += 1
                if consecutive_max_tokens >= 3:
                    logger.warning(
                        "Agentic loop aborting after %s consecutive max_tokens truncations with no content",
                        consecutive_max_tokens,
                    )
                    messages.append(
                        {"role": "user", "content": "Your previous responses were truncated. Please provide a brief final answer now."}
                    )
                    response = await provider.chat_with_tools(
                        model.model_name, messages, tool_specs
                    )
                    last_usage = _merge_chat_usage(last_usage, response.usage)
                    total_usage = _merge_chat_usage(last_usage, additional_usage)
                    return (
                        response.content or "",
                        attachments,
                        sources,
                        image_usages,
                        total_usage,
                    )
            else:
                consecutive_max_tokens = 0
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
                        parsed = repair_json_loads(response.content)
                    except Exception:
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
                        total_usage = _merge_chat_usage(last_usage, additional_usage)
                        return "", attachments, sources, image_usages, total_usage
                    await _emit("Answering", "start")
                    total_usage = _merge_chat_usage(last_usage, additional_usage)
                    return (
                        response.content,
                        attachments,
                        sources,
                        image_usages,
                        total_usage,
                    )
                logger.info("No tool calls and empty content; forcing final answer")
                messages.append(
                    {"role": "user", "content": "Please provide the final answer now."}
                )
                response = await provider.chat_with_tools(
                    model.model_name, messages, tool_specs
                )
                last_usage = _merge_chat_usage(last_usage, response.usage)
                await _emit("Answering", "start")
                total_usage = _merge_chat_usage(last_usage, additional_usage)
                return (
                    response.content or "",
                    attachments,
                    sources,
                    image_usages,
                    total_usage,
                )
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
                await _emit_tool_event(
                    {
                        "type": "tool_call",
                        "id": f"call:{call.id}",
                        "tool_name": call.name,
                        "state": "start",
                        "input_preview": _tool_call_input_preview(
                            call.name,
                            call.arguments if isinstance(call.arguments, dict) else {},
                        ),
                        "output": {},
                    }
                )
                labels: list[str] = []
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
                        requested_urls = _ensure_list(call.arguments.get("urls")) or _ensure_list(
                            call.arguments.get("url")
                        )
                        if requested_urls and all(url in failed_scrape_urls for url in requested_urls):
                            result = ToolResult(
                                name="web_scrape",
                                output={
                                    "error": "Skipped repeated web_scrape attempt for previously failed URL(s)",
                                    "results": [
                                        {
                                            "url": url,
                                            "error": "Repeated scrape failure cached",
                                        }
                                        for url in requested_urls
                                    ],
                                },
                            )
                        else:
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
                elif call.name == "download_attachments":
                    await _emit_tool_event(
                        {
                            "type": "url_attachments",
                            "id": call.id,
                            "urls": _ensure_list(call.arguments.get("urls"))
                            or _ensure_list(call.arguments.get("url")),
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
                if (
                    call.name == "web_scrape"
                    and str(call.arguments.get("output") or "").strip().lower() == "answer"
                ):
                    scrape_results = result.output.get("results", []) or []
                    answered_results: list[dict[str, Any]] = []
                    for item in scrape_results:
                        if not isinstance(item, dict):
                            continue
                        if item.get("error"):
                            answered_results.append(item)
                            continue
                        try:
                            answered_item, answer_usage = await _generate_web_scrape_answer(
                                provider=provider, model=model, result_item=item
                            )
                            answered_results.append(answered_item)
                            additional_usage = _merge_chat_usage(
                                additional_usage, answer_usage
                            )
                        except Exception as exc:
                            logger.warning(
                                "web_scrape answer pass failed url=%s err=%s",
                                item.get("url"),
                                exc,
                            )
                            fallback_item = {
                                **{k: v for k, v in item.items() if k != "analysis_input"},
                                "error": f"Answer generation failed: {exc}",
                            }
                            answered_results.append(fallback_item)
                    result.output["results"] = answered_results
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
                elif call.name == "download_attachments":
                    await _emit_tool_event(
                        {
                            "type": "url_attachments",
                            "id": call.id,
                            "urls": _ensure_list(call.arguments.get("urls"))
                            or _ensure_list(call.arguments.get("url")),
                            "output": result.output,
                        }
                    )
                if isinstance(result.output, dict) and result.output.get("error"):
                    logger.info(
                        "Tool error name=%s error=%s",
                        call.name,
                        result.output.get("error"),
                    )
                    error_text = result.output.get("error")
                    if isinstance(error_text, str) and error_text:
                        last_tool_error = error_text
                if isinstance(result.output, dict) and result.output.get("requires_approval"):
                    last_tool_error = "Execution requires approval."
                await _emit_tool_event(
                    {
                        "type": "tool_call",
                        "id": f"call:{call.id}",
                        "tool_name": call.name,
                        "state": "end",
                        "input_preview": _tool_call_input_preview(
                            call.name,
                            call.arguments if isinstance(call.arguments, dict) else {},
                        ),
                        "output": {
                            "status": (
                                "error"
                                if isinstance(result.output, dict) and result.output.get("error")
                                else "ok"
                            ),
                            "result_preview": _tool_call_output_preview(
                                call.name,
                                result.output if isinstance(result.output, dict) else {},
                            ),
                            "error": (
                                str(result.output.get("error"))
                                if isinstance(result.output, dict)
                                and result.output.get("error") is not None
                                else None
                            ),
                        },
                    }
                )
                if result.attachments:
                    attachments.extend(result.attachments)
                if call.name in {"generate_image", "edit_image"}:
                    model_id = result.output.get("model_id")
                    if model_id:
                        image_usages.append(
                            {
                                "model_id": model_id,
                                **image_usage_token_fields(
                                    result.output if isinstance(result.output, dict) else None
                                ),
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
                    scrape_results = result.output.get("results", []) or []
                    for item in scrape_results:
                        if isinstance(item, dict):
                            url = item.get("url")
                            if isinstance(url, str) and item.get("error"):
                                failed_scrape_urls.add(url)
                    for item in scrape_results:
                        url = item.get("url")
                        if url:
                            sources.append(_source_item(url, item.get("title")))
                if call.name == "search_past_chats":
                    chat_results = result.output.get("results", []) or []
                    for item in chat_results:
                        if isinstance(item, dict):
                            cid = item.get("chat_id")
                            if cid:
                                sources.append(
                                    _source_item(
                                        f"/chat/{cid}",
                                        item.get("chat_title"),
                                    )
                                )
                from app.services.mcp import mcp_source_items_from_tool_result

                for source in mcp_source_items_from_tool_result(
                    call.name, result.output
                ):
                    sources.append(source)
                messages.append(
                    {
                        "role": "tool",
                        "name": call.name,
                        "tool_call_id": call.id,
                        "content": json.dumps(
                            _sanitize_tool_output_for_context(result.output),
                            ensure_ascii=False,
                        ),
                    }
                )
                for label in labels:
                    await _emit(label, "end")
        finally:
            await _emit(step_label, "end")
    unique_sources = _dedupe_sources(sources)
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
        last_usage = _merge_chat_usage(last_usage, response.usage)
        await _emit("Answering", "start")
        if not response.content:
            fallback = last_tool_error or "No response generated."
            total_usage = _merge_chat_usage(last_usage, additional_usage)
            return (
                fallback,
                attachments,
                _limit_sources(unique_sources),
                image_usages,
                total_usage,
            )
    total_usage = _merge_chat_usage(last_usage, additional_usage)
    return (
        response.content,
        attachments,
        _limit_sources(unique_sources),
        image_usages,
        total_usage,
    )


class ChatCreateRequest(BaseModel):
    org_id: str
    model_id: str | None = None
    title: str | None = None
    agent_id: str | None = None
    is_incognito: bool = False


class ChatUpdateRequest(BaseModel):
    title: str | None = None
    is_pinned: bool | None = None


class ChatRead(BaseModel):
    id: str
    title: str | None
    model_id: str | None
    agent_id: str | None
    is_shared: bool = False
    is_incognito: bool = False
    is_pinned: bool = False
    created_at: datetime
    last_activity_at: datetime


def _chat_read(chat: Chat) -> ChatRead:
    return ChatRead(
        id=str(chat.id),
        title=chat.title,
        model_id=str(chat.model_id) if chat.model_id else None,
        agent_id=str(chat.agent_id) if chat.agent_id else None,
        is_shared=bool(chat.share_token),
        is_incognito=bool(chat.is_incognito),
        is_pinned=bool(chat.is_pinned),
        created_at=chat.created_at,
        last_activity_at=chat.last_activity_at or chat.created_at,
    )


class ChatShareRead(BaseModel):
    chat_id: str
    is_shared: bool
    share_token: str | None = None
    share_url: str | None = None


class SharedChatResolveRead(BaseModel):
    chat_id: str


class ChatMessageAttachmentCreate(BaseModel):
    upload_id: str | None = None
    file_name: str | None = None
    content_type: str | None = None
    data_base64: str | None = None

    @field_validator("data_base64")
    @classmethod
    def _validate_attachment_size(cls, value: str | None) -> str | None:
        if not value:
            return value
        estimated_bytes = _base64_size_bytes(value)
        if estimated_bytes > settings.attachments_max_file_bytes:
            raise ValueError("Attachment exceeds maximum size.")
        return value

    @model_validator(mode="after")
    def _validate_source(self) -> "ChatMessageAttachmentCreate":
        if (self.upload_id or "").strip():
            return self
        if not (self.file_name and self.content_type and self.data_base64):
            raise ValueError(
                "Attachment requires upload_id or file_name/content_type/data_base64."
            )
        return self


class ChatUploadCreateRequest(BaseModel):
    file_name: str
    content_type: str
    data_base64: str

    @field_validator("data_base64")
    @classmethod
    def _validate_attachment_size(cls, value: str) -> str:
        if _base64_size_bytes(value) > settings.attachments_max_file_bytes:
            raise ValueError("Attachment exceeds maximum size.")
        return value


class ChatUploadRead(BaseModel):
    id: str
    file_name: str
    content_type: str
    size_bytes: int
    created_at: datetime


class CoworkDocumentRead(BaseModel):
    document_id: str
    chat_id: str
    title: str
    file_name: str
    format: str
    language: str | None = None
    content: str | None = None
    version: int
    is_active: bool
    last_assistant_version: int
    user_edited: bool
    updated_at: datetime | None = None
    created_at: datetime | None = None


class CoworkDocumentPatchRequest(BaseModel):
    content: str
    base_version: int


class CoworkDocumentConflictRead(BaseModel):
    detail: str = "version_conflict"
    document: CoworkDocumentRead


class ChatMessageAttachmentRead(BaseModel):
    id: str
    file_name: str
    content_type: str
    data_base64: str | None = None
    content_url: str | None = None


class ChatMessageCreateRequest(BaseModel):
    content: str
    model_id: str | None = None
    stream: bool | None = False
    attachments: list[ChatMessageAttachmentCreate] | None = None
    reasoning_effort: str | None = None
    web_search_enabled: bool | None = None
    code_execution_enabled: bool | None = None
    locale: str | None = None
    timezone: str | None = None

    @model_validator(mode="after")
    def _validate_attachments(self) -> "ChatMessageCreateRequest":
        items = self.attachments or []
        if len(items) > settings.attachments_max_files:
            raise ValueError("Too many attachments.")
        total_bytes = 0
        for item in items:
            total_bytes += _base64_size_bytes(item.data_base64)
        if total_bytes > settings.attachments_max_total_bytes:
            raise ValueError("Total attachments size exceeded.")
        return self


class MessageUsageRead(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    thinking_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float | None = None
    time_to_first_token_ms: float | None = None
    generation_time_ms: float | None = None
    tokens_per_second: float | None = None


def _duration_ms(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    delta_ms = (end - start).total_seconds() * 1000.0
    if delta_ms < 0:
        return None
    return delta_ms


def _attach_generation_timing(
    session: Session, usage_by_message: dict[UUID, MessageUsageRead]
) -> None:
    if not usage_by_message:
        return
    message_ids = list(usage_by_message.keys())
    messages = session.exec(
        select(ChatMessage).where(ChatMessage.id.in_(message_ids))
    ).all()
    message_map = {message.id: message for message in messages}
    tasks = session.exec(
        select(ChatGenerationTask).where(
            ChatGenerationTask.assistant_message_id.in_(message_ids)
        )
    ).all()
    task_by_message = {task.assistant_message_id: task for task in tasks}
    task_ids = [task.id for task in tasks]
    first_token_by_task: dict[UUID, datetime] = {}
    if task_ids:
        rows = session.exec(
            select(
                ChatGenerationEvent.task_id,
                func.min(ChatGenerationEvent.created_at),
            )
            .where(ChatGenerationEvent.task_id.in_(task_ids))
            .where(ChatGenerationEvent.event_type == "delta")
            .group_by(ChatGenerationEvent.task_id)
        ).all()
        for task_id, first_at in rows:
            if task_id is not None and first_at is not None:
                first_token_by_task[task_id] = first_at

    for message_id, usage in usage_by_message.items():
        message = message_map.get(message_id)
        if message is None:
            continue
        task = task_by_message.get(message_id)
        started_at = message.started_at or (task.started_at if task else None)
        completed_at = message.completed_at or (task.completed_at if task else None)
        first_token_at = (
            first_token_by_task.get(task.id) if task is not None else None
        )

        usage.generation_time_ms = _duration_ms(started_at, completed_at)
        usage.time_to_first_token_ms = _duration_ms(started_at, first_token_at)

        tokens_per_second: float | None = None
        output_tokens = usage.output_tokens or 0
        if output_tokens > 0 and completed_at is not None:
            if first_token_at is not None:
                decode_s = (completed_at - first_token_at).total_seconds()
                if decode_s >= 0.05:
                    tokens_per_second = output_tokens / decode_s
            if tokens_per_second is None and started_at is not None:
                total_s = (completed_at - started_at).total_seconds()
                if total_s > 0:
                    tokens_per_second = output_tokens / total_s
        usage.tokens_per_second = tokens_per_second


def _estimate_message_usage_cost(
    *,
    provider: str | None,
    model_name: str | None,
    display_name: str | None,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int,
    thinking_tokens: int,
) -> float | None:
    cost = estimate_token_cost_usd(
        provider,
        model_name,
        input_tokens,
        output_tokens,
        cached_tokens,
        thinking_tokens,
    )
    if cost is not None or not display_name:
        return cost
    return estimate_token_cost_usd(
        provider,
        display_name,
        input_tokens,
        output_tokens,
        cached_tokens,
        thinking_tokens,
    )


def build_message_usage_map(
    session: Session, message_ids: list[UUID]
) -> dict[UUID, MessageUsageRead]:
    if not message_ids:
        return {}
    usage_rows = session.exec(
        select(
            UsageEvent.message_id,
            UsageEvent.model_id,
            func.sum(UsageEvent.input_tokens),
            func.sum(UsageEvent.output_tokens),
            func.sum(UsageEvent.cached_tokens),
            func.sum(UsageEvent.thinking_tokens),
            func.sum(UsageEvent.total_tokens),
        )
        .where(UsageEvent.message_id.in_(message_ids))
        .group_by(UsageEvent.message_id, UsageEvent.model_id)
    ).all()
    model_ids = {row[1] for row in usage_rows if row[1] is not None}
    models = (
        session.exec(select(ChatModel).where(ChatModel.id.in_(model_ids))).all()
        if model_ids
        else []
    )
    model_map = {model.id: model for model in models}

    usage_by_message: dict[UUID, MessageUsageRead] = {}
    missing_cost: set[UUID] = set()
    for row in usage_rows:
        (
            message_id,
            model_id,
            input_tokens,
            output_tokens,
            cached_tokens,
            thinking_tokens,
            total_tokens,
        ) = row
        if message_id is None:
            continue
        input_tokens = int(input_tokens or 0)
        output_tokens = int(output_tokens or 0)
        cached_tokens = int(cached_tokens or 0)
        thinking_tokens = int(thinking_tokens or 0)
        total_tokens = int(total_tokens or 0)
        usage = usage_by_message.get(message_id)
        if usage is None:
            usage = MessageUsageRead()
            usage_by_message[message_id] = usage
        usage.input_tokens += input_tokens
        usage.output_tokens += output_tokens
        usage.cached_tokens += cached_tokens
        usage.thinking_tokens += thinking_tokens
        usage.total_tokens += total_tokens

        token_total = input_tokens + output_tokens + cached_tokens + thinking_tokens
        if token_total <= 0:
            continue
        model = model_map.get(model_id) if model_id is not None else None
        cost = _estimate_message_usage_cost(
            provider=model.provider if model else None,
            model_name=model.model_name if model else None,
            display_name=model.display_name if model else None,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            thinking_tokens=thinking_tokens,
        )
        if cost is None:
            missing_cost.add(message_id)
            continue
        usage.cost_usd = (usage.cost_usd or 0.0) + cost

    for message_id in missing_cost:
        if message_id in usage_by_message:
            usage_by_message[message_id].cost_usd = None
    _attach_generation_timing(session, usage_by_message)
    return usage_by_message


class ChatMessageRead(BaseModel):
    id: str
    role: str
    content: str
    created_at: datetime
    model_id: str | None = None
    model_name: str | None = None
    attachments: list[ChatMessageAttachmentRead] | None = None
    sources: list[dict] | None = None
    thinking_steps: list[str] | None = None
    stream_parts: list[dict[str, Any]] | None = None
    tool_event: dict | None = None
    activity_event: dict | None = None
    task_id: str | None = None
    generation_status: str | None = None
    usage: MessageUsageRead | None = None


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
    model_id: str | None = None
    attachments: list[ChatMessageAttachmentCreate] | None = None
    reasoning_effort: str | None = None
    web_search_enabled: bool | None = None
    code_execution_enabled: bool | None = None
    locale: str | None = None
    timezone: str | None = None

    @model_validator(mode="after")
    def _validate_attachments(self) -> "ChatMessageEditRequest":
        items = self.attachments or []
        if len(items) > settings.attachments_max_files:
            raise ValueError("Too many attachments.")
        total_bytes = 0
        for item in items:
            total_bytes += _base64_size_bytes(item.data_base64)
        if total_bytes > settings.attachments_max_total_bytes:
            raise ValueError("Total attachments size exceeded.")
        return self


class ChatMessageEditResponse(BaseModel):
    user_message: ChatMessageRead
    assistant_message: ChatMessageRead


def _resolve_attachment_inputs(
    session: Session,
    *,
    chat: Chat,
    current_user: User,
    items: list[ChatMessageAttachmentCreate] | None,
) -> list[ChatMessageAttachmentCreate]:
    if not items:
        return []
    if len(items) > settings.attachments_max_files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Too many attachments."
        )

    resolved: list[ChatMessageAttachmentCreate] = []
    total_bytes = 0
    for item in items:
        upload_id = (item.upload_id or "").strip()
        if upload_id:
            try:
                upload_uuid = UUID(upload_id)
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid upload_id.",
                ) from exc
            upload = session.exec(
                select(ChatUpload).where(
                    ChatUpload.id == upload_uuid,
                    ChatUpload.chat_id == chat.id,
                    ChatUpload.user_id == current_user.id,
                )
            ).first()
            if upload:
                resolved_item = ChatMessageAttachmentCreate(
                    file_name=upload.file_name,
                    content_type=upload.content_type,
                    data_base64=upload.data_base64,
                )
            else:
                # Backward/compat path: allow existing message attachment IDs too.
                existing_attachment = session.exec(
                    select(ChatMessageAttachment)
                    .join(ChatMessage, ChatMessage.id == ChatMessageAttachment.message_id)
                    .where(ChatMessage.chat_id == chat.id)
                    .where(ChatMessageAttachment.id == upload_uuid)
                ).first()
                if not existing_attachment:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="Upload not found for this chat.",
                    )
                resolved_item = ChatMessageAttachmentCreate(
                    file_name=existing_attachment.file_name,
                    content_type=existing_attachment.content_type,
                    data_base64=existing_attachment.data_base64,
                )
        else:
            resolved_item = item
        total_bytes += _base64_size_bytes(resolved_item.data_base64)
        resolved.append(resolved_item)

    if total_bytes > settings.attachments_max_total_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Total attachments size exceeded.",
        )
    return resolved


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
    try:
        enforce_chat_usage_limits(
            session, org_id=chat.org_id, user_id=current_user.id
        )
    except HTTPException as exc:
        await _ws_send_event(websocket, {"error": str(exc.detail)})
        return

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
    model = ensure_model_capabilities(session, model)
    if not _user_can_use_model(session, chat.org_id, chat.user_id, model.id):
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
    chat.last_activity_at = datetime.utcnow()
    session.add(chat)
    session.commit()
    session.refresh(user_message)

    attachments = []
    if payload.attachments:
        try:
            resolved_attachments = _resolve_attachment_inputs(
                session,
                chat=chat,
                current_user=current_user,
                items=payload.attachments,
            )
        except HTTPException as exc:
            await _ws_send_event(websocket, {"error": str(exc.detail)})
            return
        try:
            _ensure_model_supports_image_attachments(model, resolved_attachments)
        except HTTPException as exc:
            await _ws_send_event(websocket, {"error": str(exc.detail)})
            return
        for item in resolved_attachments:
            attachments.append(
                ChatMessageAttachment(
                    message_id=user_message.id,
                    file_name=str(item.file_name or ""),
                    content_type=str(item.content_type or ""),
                    data_base64=str(item.data_base64 or ""),
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
            "timezone": payload.timezone,
            "reasoning_effort": payload.reasoning_effort,
            "web_search_enabled": payload.web_search_enabled,
            "code_execution_enabled": _coerce_optional_bool(
                payload.code_execution_enabled
            ),
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
    try:
        enforce_chat_usage_limits(
            session, org_id=chat.org_id, user_id=current_user.id
        )
    except HTTPException as exc:
        await _ws_send_event(websocket, {"error": str(exc.detail)})
        return

    message = session.exec(
        select(ChatMessage).where(
            ChatMessage.id == message_uuid, ChatMessage.chat_id == chat.id
        )
    ).first()
    if not message:
        await _ws_send_event(websocket, {"error": "Message not found"})
        return

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
    model = ensure_model_capabilities(session, model)
    if not _user_can_use_model(session, chat.org_id, chat.user_id, model.id):
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
        try:
            _ensure_model_supports_image_attachments(model, prev_attachments)
        except HTTPException as exc:
            await _ws_send_event(websocket, {"error": str(exc.detail)})
            return
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
            try:
                resolved_attachments = _resolve_attachment_inputs(
                    session,
                    chat=chat,
                    current_user=current_user,
                    items=payload.attachments,
                )
            except HTTPException as exc:
                await _ws_send_event(websocket, {"error": str(exc.detail)})
                return
            try:
                _ensure_model_supports_image_attachments(model, resolved_attachments)
            except HTTPException as exc:
                await _ws_send_event(websocket, {"error": str(exc.detail)})
                return
            session.add_all(
                [
                    ChatMessageAttachment(
                        message_id=new_message.id,
                        file_name=str(attachment.file_name or ""),
                        content_type=str(attachment.content_type or ""),
                        data_base64=str(attachment.data_base64 or ""),
                    )
                    for attachment in resolved_attachments
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
            "timezone": payload.timezone,
            "reasoning_effort": payload.reasoning_effort,
            "web_search_enabled": payload.web_search_enabled,
            "code_execution_enabled": _coerce_optional_bool(
                payload.code_execution_enabled
            ),
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
    agent_id = None
    if payload.agent_id:
        try:
            agent_id = UUID(payload.agent_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid agent id"
            ) from exc
        agent = session.exec(
            select(Agent).where(Agent.id == agent_id, Agent.org_id == org_id)
        ).first()
        if not agent:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found"
            )
        access = session.exec(
            select(AgentAccess).where(
                AgentAccess.agent_id == agent.id,
                AgentAccess.user_id == current_user.id,
            )
        ).first()
        if not access:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Agent access required",
            )
        if model_id is None and agent.preferred_model_id:
            try:
                model_id = UUID(str(agent.preferred_model_id))
            except ValueError:
                model_id = None

    chat = Chat(
        org_id=org_id,
        user_id=current_user.id,
        model_id=model_id,
        agent_id=agent_id,
        title=payload.title,
        is_incognito=payload.is_incognito,
    )
    session.add(chat)
    session.commit()
    session.refresh(chat)
    return _chat_read(chat)


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

    chats = session.exec(
        select(Chat).where(
            Chat.org_id == org_uuid,
            Chat.user_id == current_user.id,
            Chat.is_deleted.is_(False),
            Chat.is_incognito.is_(False),
        )
    ).all()
    return [_chat_read(chat) for chat in chats]


@router.get("/search", response_model=list[ChatRead])
def search_chats(
    q: str,
    org_id: str | None = None,
    limit: int = 50,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ChatRead]:
    org_uuid: UUID | None = None
    if org_id:
        try:
            org_uuid = UUID(org_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid org id"
            ) from exc
        require_org_member(
            session, org_uuid, current_user.id, is_super_admin=current_user.is_super_admin
        )
    query = (q or "").strip()
    if not query:
        return []
    capped_limit = max(1, min(limit, 100))
    base_chat_filters = [
        Chat.user_id == current_user.id,
        Chat.is_deleted.is_(False),
        Chat.is_incognito.is_(False),
    ]
    if org_uuid:
        base_chat_filters.append(Chat.org_id == org_uuid)
    eligible_chats_subq = select(Chat.id).where(*base_chat_filters).subquery()
    search_query = func.plainto_tsquery("simple", query)
    title_vector = func.to_tsvector("simple", func.coalesce(Chat.title, ""))
    title_rank = func.ts_rank_cd(title_vector, search_query)
    title_match = title_vector.op("@@")(search_query)
    message_rank_subq = (
        select(
            ChatMessage.chat_id.label("chat_id"),
            func.max(
                func.ts_rank_cd(
                    func.to_tsvector("simple", func.coalesce(ChatMessage.content, "")),
                    search_query,
                )
            ).label("message_rank"),
        )
        .where(
            ChatMessage.is_current.is_(True),
            ChatMessage.chat_id.in_(select(eligible_chats_subq.c.id)),
            func.to_tsvector("simple", func.coalesce(ChatMessage.content, "")).op("@@")(
                search_query
            ),
        )
        .group_by(ChatMessage.chat_id)
        .subquery()
    )
    chats = session.exec(
        select(
            Chat,
            (title_rank * 2.0 + func.coalesce(message_rank_subq.c.message_rank, 0.0)).label(
                "rank"
            ),
        )
        .outerjoin(message_rank_subq, message_rank_subq.c.chat_id == Chat.id)
        .where(*base_chat_filters)
        .where(title_match | (message_rank_subq.c.message_rank.is_not(None)))
        .order_by(
            (
                title_rank * 2.0 + func.coalesce(message_rank_subq.c.message_rank, 0.0)
            ).desc(),
            Chat.last_activity_at.desc(),
        )
        .limit(capped_limit)
    ).all()
    return [_chat_read(chat) for chat, _rank in chats]


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
    is_owner = chat.user_id == current_user.id
    if not is_owner:
        # Shared chats are reachable via the normal /chat/{id} link while share_token is set.
        if not (chat.share_token or "").strip():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=CHAT_NOT_SHARED_DETAIL,
            )
        viewer_label = _viewer_label(current_user)
        session.add(
            ChatViewEvent(
                chat_id=chat.id,
                viewer_user_id=current_user.id,
                viewer_label=viewer_label,
            )
        )
        session.commit()
    else:
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
            select(ChatMessageAttachment)
            .where(
                ChatMessageAttachment.message_id.in_(
                    [message.id for message in messages]
                )
            )
            .order_by(ChatMessageAttachment.created_at)
        ).all()
    task_map: dict[UUID, ChatGenerationTask] = {}
    task_by_id: dict[UUID, ChatGenerationTask] = {}
    if messages:
        tasks = session.exec(
            select(ChatGenerationTask).where(
                ChatGenerationTask.assistant_message_id.in_(
                    [message.id for message in messages]
                )
            )
        ).all()
        task_map = {task.assistant_message_id: task for task in tasks}
        task_by_id = {task.id: task for task in tasks}
    attachments_by_message: dict[UUID, list[ChatMessageAttachmentRead]] = {}
    for attachment in attachments:
        attachments_by_message.setdefault(attachment.message_id, []).append(
            ChatMessageAttachmentRead(
                id=str(attachment.id),
                file_name=attachment.file_name,
                content_type=attachment.content_type,
                content_url=_attachment_content_url(attachment.id),
            )
        )
    tool_events_by_assistant: dict[UUID, list[ChatGenerationEvent]] = {}
    timeline_events_by_assistant: dict[UUID, list[ChatGenerationEvent]] = {}
    if task_by_id:
        generation_events = session.exec(
            select(ChatGenerationEvent)
            .where(ChatGenerationEvent.task_id.in_(list(task_by_id.keys())))
            .where(
                ChatGenerationEvent.event_type.in_(
                    ["tool_event", "activity", "delta"]
                )
            )
            .order_by(ChatGenerationEvent.sequence, ChatGenerationEvent.created_at)
        ).all()
        for event in generation_events:
            task = task_by_id.get(event.task_id)
            if not task:
                continue
            if not isinstance(event.payload_json, dict):
                continue
            timeline_events_by_assistant.setdefault(
                task.assistant_message_id, []
            ).append(event)
            if event.event_type == "tool_event":
                tool_events_by_assistant.setdefault(
                    task.assistant_message_id, []
                ).append(event)
    view_events = session.exec(
        select(ChatViewEvent)
        .where(ChatViewEvent.chat_id == chat.id)
        .order_by(ChatViewEvent.created_at)
    ).all()

    usage_by_message: dict[UUID, MessageUsageRead] = {}
    if messages:
        usage_by_message = build_message_usage_map(
            session, [message.id for message in messages]
        )

    results: list[ChatMessageRead] = []
    for message in messages:
        message_attachments = attachments_by_message.get(message.id)
        stream_parts = None
        thinking_steps = None
        if message.role == "assistant":
            timeline_events = timeline_events_by_assistant.get(message.id, [])
            if timeline_events:
                stream_parts, thinking_steps = _build_stream_parts_from_events(
                    timeline_events,
                    message_content=message.content or "",
                    message_attachments=message_attachments,
                )
        results.append(
            ChatMessageRead(
                id=str(message.id),
                role=message.role,
                content=message.content,
                created_at=message.created_at,
                model_id=str(message.model_id) if message.model_id else None,
                model_name=model_map.get(message.model_id),
                attachments=message_attachments,
                sources=message.sources,
                thinking_steps=thinking_steps or None,
                stream_parts=stream_parts,
                task_id=str(task_map[message.id].id) if message.id in task_map else None,
                generation_status=task_map[message.id].status.value
                if message.id in task_map
                else None,
                usage=usage_by_message.get(message.id),
            )
        )
        if message.role != "assistant":
            continue
        raw_events = tool_events_by_assistant.get(message.id, [])
        if raw_events:
            # Collapse repeated tool updates (e.g. code_execution pre/post output)
            # into a single persisted tool bubble, keeping the latest payload per tool id.
            deduped: dict[str, ChatGenerationEvent] = {}
            for event in raw_events:
                payload = event.payload_json if isinstance(event.payload_json, dict) else None
                tool_event_id = payload.get("id") if isinstance(payload, dict) else None
                key = str(tool_event_id) if isinstance(tool_event_id, str) and tool_event_id else str(event.id)
                deduped[key] = event
            for event in sorted(deduped.values(), key=lambda item: item.sequence):
                payload = event.payload_json if isinstance(event.payload_json, dict) else None
                if payload is None:
                    continue
                results.append(
                    ChatMessageRead(
                        id=str(event.id),
                        role="tool",
                        content="",
                        created_at=event.created_at,
                        tool_event=payload,
                        task_id=str(task_map[message.id].id) if message.id in task_map else None,
                    )
                )
    for event in view_events:
        results.append(
            ChatMessageRead(
                id=f"view-{event.id}",
                role="event",
                content="",
                created_at=event.created_at,
                activity_event={
                    "type": "chat_view",
                    "count": 1,
                    "opens": [
                        {
                            "viewer": event.viewer_label,
                            "opened_at": event.created_at.isoformat(),
                        }
                    ],
                },
            )
        )
    return sorted(results, key=lambda item: item.created_at)


@router.get("/attachments/{attachment_id}/content")
def get_attachment_content(
    attachment_id: str,
    token: str,
    session: Session = Depends(get_db),
) -> Response:
    try:
        attachment_uuid = UUID(attachment_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid attachment id"
        ) from exc
    token_attachment_id = _decode_attachment_access_token(token)
    if token_attachment_id != attachment_uuid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid attachment token"
        )
    attachment = session.exec(
        select(ChatMessageAttachment).where(ChatMessageAttachment.id == attachment_uuid)
    ).first()
    if not attachment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found"
        )
    try:
        data = base64.b64decode(attachment.data_base64)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Attachment decode failed",
        ) from exc
    return Response(
        content=data,
        media_type=attachment.content_type or "application/octet-stream",
        headers={
            "Cache-Control": "private, max-age=300",
            "Content-Disposition": f'inline; filename="{attachment.file_name}"',
        },
    )


@router.post("/{chat_id}/uploads", response_model=ChatUploadRead)
def upload_chat_attachment(
    chat_id: str,
    payload: ChatUploadCreateRequest,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChatUploadRead:
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

    upload = ChatUpload(
        chat_id=chat.id,
        user_id=current_user.id,
        file_name=payload.file_name,
        content_type=payload.content_type,
        data_base64=payload.data_base64,
    )
    session.add(upload)
    session.commit()
    session.refresh(upload)
    return ChatUploadRead(
        id=str(upload.id),
        file_name=upload.file_name,
        content_type=upload.content_type,
        size_bytes=_base64_size_bytes(upload.data_base64),
        created_at=upload.created_at,
    )


def _require_chat_for_cowork(
    session: Session, chat_id: str, current_user: User
) -> Chat:
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
    require_org_member(
        session, chat.org_id, current_user.id, is_super_admin=current_user.is_super_admin
    )
    return chat


def _cowork_read_model(doc: ChatCoworkDocument, *, include_content: bool = True) -> CoworkDocumentRead:
    payload = document_payload(doc, include_content=include_content)
    return CoworkDocumentRead(**payload)


@router.get("/{chat_id}/cowork", response_model=list[CoworkDocumentRead])
def list_cowork_documents(
    chat_id: str,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[CoworkDocumentRead]:
    chat = _require_chat_for_cowork(session, chat_id, current_user)
    docs = list_documents(session, chat.id)
    return [_cowork_read_model(doc, include_content=False) for doc in docs]


@router.get("/{chat_id}/cowork/active", response_model=CoworkDocumentRead | None)
def get_active_cowork_document(
    chat_id: str,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CoworkDocumentRead | None:
    chat = _require_chat_for_cowork(session, chat_id, current_user)
    doc = get_active_document(session, chat.id)
    if not doc:
        return None
    return _cowork_read_model(doc, include_content=True)


@router.get("/{chat_id}/cowork/{doc_id}", response_model=CoworkDocumentRead)
def get_cowork_document(
    chat_id: str,
    doc_id: str,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CoworkDocumentRead:
    chat = _require_chat_for_cowork(session, chat_id, current_user)
    try:
        document_id = UUID(doc_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid document id"
        ) from exc
    doc = get_document(session, chat.id, document_id)
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return _cowork_read_model(doc, include_content=True)


@router.post("/{chat_id}/cowork/{doc_id}/activate", response_model=CoworkDocumentRead)
def activate_cowork_document(
    chat_id: str,
    doc_id: str,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CoworkDocumentRead:
    chat = _require_chat_for_cowork(session, chat_id, current_user)
    try:
        document_id = UUID(doc_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid document id"
        ) from exc
    doc = activate_document(session, chat.id, document_id)
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return _cowork_read_model(doc, include_content=True)


@router.patch("/{chat_id}/cowork/{doc_id}", response_model=CoworkDocumentRead)
def patch_cowork_document(
    chat_id: str,
    doc_id: str,
    payload: CoworkDocumentPatchRequest,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CoworkDocumentRead:
    chat = _require_chat_for_cowork(session, chat_id, current_user)
    try:
        document_id = UUID(doc_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid document id"
        ) from exc
    doc = get_document(session, chat.id, document_id)
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    updated, latest = apply_user_patch(
        session,
        doc,
        content=payload.content,
        base_version=payload.base_version,
    )
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "detail": "version_conflict",
                "document": document_payload(latest, include_content=True),
            },
        )
    return _cowork_read_model(updated, include_content=True)


@router.delete("/{chat_id}/cowork/{doc_id}", response_model=CoworkDocumentRead | None)
def delete_cowork_document(
    chat_id: str,
    doc_id: str,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CoworkDocumentRead | None:
    chat = _require_chat_for_cowork(session, chat_id, current_user)
    try:
        document_id = UUID(doc_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid document id"
        ) from exc
    try:
        active = delete_document(session, chat.id, document_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
        ) from exc
    if not active:
        return None
    return _cowork_read_model(active, include_content=True)


@router.get("/{chat_id}/cowork/{doc_id}/download")
def download_cowork_document(
    chat_id: str,
    doc_id: str,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    chat = _require_chat_for_cowork(session, chat_id, current_user)
    try:
        document_id = UUID(doc_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid document id"
        ) from exc
    doc = get_document(session, chat.id, document_id)
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    data = (doc.content or "").encode("utf-8")
    safe_name = doc.file_name.replace('"', "")
    return Response(
        content=data,
        media_type=mime_for_document(doc),
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'attachment; filename="{safe_name}"',
        },
    )


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


@router.post("/{chat_id}/generation/{task_id}/cancel", status_code=status.HTTP_204_NO_CONTENT)
def cancel_generation_task(
    chat_id: str,
    task_id: str,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
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
    if task.status in {
        GenerationStatus.completed,
        GenerationStatus.failed,
        GenerationStatus.cancelled,
    }:
        return

    task.status = GenerationStatus.cancelled
    task.error = "Generation cancelled by user"
    task.completed_at = datetime.utcnow()
    session.add(task)

    assistant_message = session.get(ChatMessage, task.assistant_message_id)
    if assistant_message:
        assistant_message.status = "cancelled"
        assistant_message.completed_at = datetime.utcnow()
        if not (assistant_message.content or "").strip():
            assistant_message.content = "Generation cancelled by user"
        assistant_message.error_message = "Generation cancelled by user"
        session.add(assistant_message)
    session.commit()


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
    try:
        from app.services.agents.chat_index import delete_project_chat_source

        delete_project_chat_source(session, chat.id)
    except Exception:
        logger.exception("Failed to delete project chat index for chat_id=%s", chat.id)
    session.commit()


@router.patch("/{chat_id}", response_model=ChatRead)
def update_chat(
    chat_id: str,
    payload: ChatUpdateRequest,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChatRead:
    try:
        chat_uuid = UUID(chat_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid chat id"
        ) from exc

    chat = session.exec(select(Chat).where(Chat.id == chat_uuid)).first()
    if not chat or chat.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")
    if chat.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Cannot update this chat"
        )
    require_org_member(
        session, chat.org_id, current_user.id, is_super_admin=current_user.is_super_admin
    )
    if payload.title is None and payload.is_pinned is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No updates provided"
        )
    if payload.title is not None:
        title = payload.title.strip()
        if not title:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Title is required"
            )
        if len(title) > 200:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Title is too long"
            )
        chat.title = title
    if payload.is_pinned is not None:
        chat.is_pinned = payload.is_pinned
    session.add(chat)
    session.commit()
    session.refresh(chat)
    return _chat_read(chat)


@router.get("/shared/{share_token}", response_model=SharedChatResolveRead)
def resolve_shared_chat(
    share_token: str,
    session: Session = Depends(get_db),
) -> SharedChatResolveRead:
    token = (share_token or "").strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid share token"
        )
    chat = session.exec(
        select(Chat)
        .where(Chat.share_token == token)
        .where(Chat.is_deleted.is_(False))
        .where(Chat.is_incognito.is_(False))
    ).first()
    if not chat:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=CHAT_NOT_SHARED_DETAIL
        )
    return SharedChatResolveRead(chat_id=str(chat.id))


@router.post("/{chat_id}/share", response_model=ChatShareRead)
def share_chat(
    chat_id: str,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChatShareRead:
    try:
        chat_uuid = UUID(chat_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid chat id"
        ) from exc

    chat = session.exec(select(Chat).where(Chat.id == chat_uuid)).first()
    if not chat or chat.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")
    if chat.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Cannot share this chat"
        )
    if chat.is_incognito:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incognito chats cannot be shared",
        )
    if not chat.share_token:
        chat.share_token = secrets.token_urlsafe(24)
        session.add(chat)
        session.commit()
        session.refresh(chat)
    return ChatShareRead(
        chat_id=str(chat.id),
        is_shared=True,
        share_url=_chat_share_url(chat),
    )


@router.delete("/{chat_id}/share", response_model=ChatShareRead)
def unshare_chat(
    chat_id: str,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChatShareRead:
    try:
        chat_uuid = UUID(chat_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid chat id"
        ) from exc

    chat = session.exec(select(Chat).where(Chat.id == chat_uuid)).first()
    if not chat or chat.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")
    if chat.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Cannot unshare this chat"
        )
    chat.share_token = None
    session.add(chat)
    session.commit()
    return ChatShareRead(chat_id=str(chat.id), is_shared=False)


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
    enforce_chat_usage_limits(session, org_id=chat.org_id, user_id=current_user.id)

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
    model = ensure_model_capabilities(session, model)
    if not _user_can_use_model(session, chat.org_id, chat.user_id, model.id):
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
    chat.last_activity_at = datetime.utcnow()
    session.add(chat)
    session.commit()
    session.refresh(user_message)

    attachments = []
    if payload.attachments:
        resolved_attachments = _resolve_attachment_inputs(
            session,
            chat=chat,
            current_user=current_user,
            items=payload.attachments,
        )
        _ensure_model_supports_image_attachments(model, resolved_attachments)
        for item in resolved_attachments:
            attachments.append(
                ChatMessageAttachment(
                    message_id=user_message.id,
                    file_name=str(item.file_name or ""),
                    content_type=str(item.content_type or ""),
                    data_base64=str(item.data_base64 or ""),
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
            "timezone": payload.timezone,
            "reasoning_effort": payload.reasoning_effort,
            "web_search_enabled": payload.web_search_enabled,
            "code_execution_enabled": _coerce_optional_bool(
                payload.code_execution_enabled
            ),
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
        latest_user_image_id: UUID | None = None
        for msg in reversed(history):
            if msg.role != "user":
                continue
            msg_attachments = attachments_by_message.get(msg.id, [])
            latest_image = next(
                (
                    attachment
                    for attachment in reversed(msg_attachments)
                    if attachment.content_type.startswith("image/")
                ),
                None,
            )
            if latest_image:
                latest_user_image_id = latest_image.id
                break
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
            # Keep only one latest image across the whole history to reduce remote fetch timeouts.
            if latest_user_image_id is not None:
                image_attachments = [
                    attachment
                    for attachment in image_attachments
                    if attachment.id == latest_user_image_id
                ]
            else:
                image_attachments = []
            attachment_lines = _attachment_lines(msg_attachments)
            if not image_attachments or not supports_image_input(model):
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
                            "url": _attachment_image_url(attachment)
                        },
                    }
                )
            items.append({"role": msg.role, "content": content_parts})
        return items

    messages = _truncate_messages(
        _prepend_tool_guidance(build_messages(), locale=payload.locale, timezone=payload.timezone),
        token_limit=model.context_length,
    )

    provider_config = require_provider_enabled(session, chat.org_id, model.provider)
    config = None
    if provider_config and provider_config.config_json:
        try:
            config = json.loads(provider_config.config_json)
        except json.JSONDecodeError:
            pass
    prompt_cache_enabled = not chat.is_incognito
    provider = get_provider(
        model.provider,
        api_key=provider_config.api_key_override if provider_config else None,
        base_url=provider_config.base_url_override if provider_config else None,
        endpoint=provider_config.endpoint_override if provider_config else None,
        reasoning_effort=payload.reasoning_effort or model.reasoning_effort,
        prompt_cache_key=f"chat:{chat.id}" if prompt_cache_enabled else None,
        prompt_cache_retention=(
            settings.openai_prompt_cache_retention if prompt_cache_enabled else None
        ),
        prompt_cache_enabled=prompt_cache_enabled,
        prefer_responses_api=model.uses_responses_api is True,
        config=config,
        openrouter_endpoint=model.openrouter_endpoint,
    )
    grounding_enabled = _grounding_enabled(org, model.provider)
    effective_web_search_enabled = _effective_web_tool_enabled(
        org.web_search_enabled,
        payload.web_search_enabled,
    )
    effective_web_scrape_enabled = _effective_web_tool_enabled(
        org.web_scrape_enabled,
        payload.web_search_enabled,
    )
    effective_exec_policy = _resolve_exec_policy(
        org.exec_policy, payload.code_execution_enabled
    )
    pending_tool_attachments: list[dict[str, Any]] = []
    tool_registry = _build_tool_registry(
        session,
        chat.org_id,
        chat_id=chat.id,
        preferred_provider=model.provider,
        web_tools_enabled=not grounding_enabled,
        web_search_enabled=effective_web_search_enabled,
        web_scrape_enabled=effective_web_scrape_enabled,
        exec_policy=effective_exec_policy,
        locale=payload.locale,
        agent_id=chat.agent_id,
        pending_attachments=pending_tool_attachments,
    )
    tool_attachments: list[dict] | None = None

    if _is_image_output_model(model) and payload.stream:
        async def image_stream():
            image_result = await generate_image(
                ImageToolContext(
                    session=session, org_id=str(chat.org_id), chat_id=str(chat.id), user_id=str(chat.user_id)
                ),
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
                **image_usage_token_fields(image_result.output),
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
                model=model,
                history=history + [assistant_message],
            )
            yield f"data: {json.dumps({'user_message_id': str(user_message.id)})}\n\n"
            yield f"data: {json.dumps({'done': True, 'message_id': str(assistant_message.id), 'content': '', 'model_name': model.display_name, 'model_id': str(model.id), 'attachments': image_result.attachments or []})}\n\n"

        return StreamingResponse(image_stream(), media_type="text/event-stream")

    if _is_image_output_model(model):
        image_result = await generate_image(
            ImageToolContext(
                session=session, org_id=str(chat.org_id), chat_id=str(chat.id), user_id=str(chat.user_id)
            ),
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
            **image_usage_token_fields(image_result.output),
        )
        session.add(usage_event)
        session.commit()
        await _maybe_update_chat_title(
            session=session,
            chat=chat,
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
            try:
                async for chunk in _create_message_event_stream():
                    yield chunk
            finally:
                persist_responses_api_discovery(session, model, provider)

        async def _create_message_event_stream():
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
                        pending_attachments=pending_tool_attachments,
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
                pending_attachments=pending_tool_attachments,
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
    persist_responses_api_discovery(session, model, provider)
    await _maybe_update_chat_title(
        session=session,
        chat=chat,
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
            raw_payload = payload.get("payload")
            if isinstance(raw_payload, str):
                try:
                    parsed_payload = json.loads(raw_payload)
                    message_payload = parsed_payload if isinstance(parsed_payload, dict) else {}
                except Exception:
                    message_payload = {}
            elif isinstance(raw_payload, dict):
                message_payload = raw_payload
            else:
                message_payload = {}

            with Session(engine) as session:
                current_user = session.get(User, current_user_id)
                if not current_user or not current_user.is_active:
                    await _ws_send_event(websocket, {"error": "User not found"})
                    await websocket.close(code=4401)
                    return

                if message_type == "send":
                    try:
                        request = ChatMessageCreateRequest(**message_payload)
                    except ValidationError as exc:
                        await _ws_send_event(
                            websocket, {"error": _validation_error_text(exc)}
                        )
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
                    except ValidationError as exc:
                        await _ws_send_event(
                            websocket, {"error": _validation_error_text(exc)}
                        )
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
        if not await _ws_try_send_event(
            websocket, {"error": exc.detail, "status": exc.status_code}
        ):
            return
        try:
            if exc.status_code == status.HTTP_401_UNAUTHORIZED:
                await websocket.close(code=4401)
            elif exc.status_code == status.HTTP_403_FORBIDDEN:
                await websocket.close(code=4403)
            else:
                await websocket.close(code=4400)
        except (WebSocketDisconnect, RuntimeError):
            return
    except Exception:
        logger.exception("Websocket error")
        if not await _ws_try_send_event(websocket, {"error": "Websocket error"}):
            return
        try:
            await websocket.close(code=1011)
        except (WebSocketDisconnect, RuntimeError):
            return


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
    enforce_chat_usage_limits(session, org_id=chat.org_id, user_id=current_user.id)

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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model not found")
    model = ensure_model_capabilities(session, model)
    if not _user_can_use_model(session, chat.org_id, chat.user_id, model.id):
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
        _ensure_model_supports_image_attachments(model, prev_attachments)
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
            resolved_attachments = _resolve_attachment_inputs(
                session,
                chat=chat,
                current_user=current_user,
                items=payload.attachments,
            )
            _ensure_model_supports_image_attachments(model, resolved_attachments)
            session.add_all(
                [
                    ChatMessageAttachment(
                        message_id=new_message.id,
                        file_name=str(attachment.file_name or ""),
                        content_type=str(attachment.content_type or ""),
                        data_base64=str(attachment.data_base64 or ""),
                    )
                    for attachment in resolved_attachments
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
            "timezone": payload.timezone,
            "reasoning_effort": payload.reasoning_effort,
            "web_search_enabled": payload.web_search_enabled,
            "code_execution_enabled": _coerce_optional_bool(
                payload.code_execution_enabled
            ),
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
        latest_user_image_id: UUID | None = None
        for msg in reversed(history):
            if msg.role != "user":
                continue
            msg_attachments = attachments_by_message.get(msg.id, [])
            latest_image = next(
                (
                    attachment
                    for attachment in reversed(msg_attachments)
                    if attachment.content_type.startswith("image/")
                ),
                None,
            )
            if latest_image:
                latest_user_image_id = latest_image.id
                break
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
            # Keep only one latest image across the whole history to reduce remote fetch timeouts.
            if latest_user_image_id is not None:
                image_attachments = [
                    attachment
                    for attachment in image_attachments
                    if attachment.id == latest_user_image_id
                ]
            else:
                image_attachments = []
            attachment_lines = _attachment_lines(msg_attachments)
            if not image_attachments or not supports_image_input(model):
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
                            "url": _attachment_image_url(attachment)
                        },
                    }
                )
            items.append({"role": msg.role, "content": content_parts})
        return items

    messages = _truncate_messages(
        _prepend_tool_guidance(build_messages(), locale=payload.locale, timezone=payload.timezone),
        token_limit=model.context_length,
    )

    provider_config = require_provider_enabled(session, chat.org_id, model.provider)
    config = None
    if provider_config and provider_config.config_json:
        try:
            config = json.loads(provider_config.config_json)
        except json.JSONDecodeError:
            pass
    prompt_cache_enabled = not chat.is_incognito
    provider = get_provider(
        model.provider,
        api_key=provider_config.api_key_override if provider_config else None,
        base_url=provider_config.base_url_override if provider_config else None,
        endpoint=provider_config.endpoint_override if provider_config else None,
        reasoning_effort=payload.reasoning_effort or model.reasoning_effort,
        prompt_cache_key=f"chat:{chat.id}" if prompt_cache_enabled else None,
        prompt_cache_retention=(
            settings.openai_prompt_cache_retention if prompt_cache_enabled else None
        ),
        prompt_cache_enabled=prompt_cache_enabled,
        prefer_responses_api=model.uses_responses_api is True,
        config=config,
        openrouter_endpoint=model.openrouter_endpoint,
    )
    grounding_enabled = _grounding_enabled(org, model.provider)
    effective_web_search_enabled = _effective_web_tool_enabled(
        org.web_search_enabled,
        payload.web_search_enabled,
    )
    effective_web_scrape_enabled = _effective_web_tool_enabled(
        org.web_scrape_enabled,
        payload.web_search_enabled,
    )
    effective_exec_policy = _resolve_exec_policy(
        org.exec_policy, payload.code_execution_enabled
    )
    pending_tool_attachments: list[dict[str, Any]] = []
    tool_registry = _build_tool_registry(
        session,
        chat.org_id,
        chat_id=chat.id,
        preferred_provider=model.provider,
        web_tools_enabled=not grounding_enabled,
        web_search_enabled=effective_web_search_enabled,
        web_scrape_enabled=effective_web_scrape_enabled,
        exec_policy=effective_exec_policy,
        locale=payload.locale,
        agent_id=chat.agent_id,
        pending_attachments=pending_tool_attachments,
    )

    if model.supports_image_output:
        image_result = await generate_image(
            ImageToolContext(
                session=session, org_id=str(chat.org_id), chat_id=str(chat.id), user_id=str(chat.user_id)
            ),
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
            **image_usage_token_fields(image_result.output),
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
                pending_attachments=pending_tool_attachments,
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
    persist_responses_api_discovery(session, model, provider)
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
