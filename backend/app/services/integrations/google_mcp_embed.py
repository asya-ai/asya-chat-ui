"""Embedded Google Workspace MCP (in-process Streamable HTTP).

Accepts the user's Google OAuth access token as Bearer Authorization and
narrows advertised tools via X-Toolsets (gmail, drive, calendar, contacts,
meet, tasks). Celery workers call GOOGLE_MCP_EMBED_URL on the API host.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any, AsyncIterator
from urllib.parse import urlparse

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

EMBEDDED_GOOGLE_MCP_URL = "http://127.0.0.1:8000/internal/google-mcp/mcp"

_access_token: ContextVar[str | None] = ContextVar("google_mcp_token", default=None)
_toolsets: ContextVar[frozenset[str] | None] = ContextVar(
    "google_mcp_toolsets", default=None
)

_ALL_TOOLSETS = frozenset(
    {
        "gmail",
        "drive",
        "calendar",
        "contacts",
        "meet",
        "tasks",
        "docs",
        "sheets",
        "slides",
        "chat",
    }
)

_MIME_DOCS = "application/vnd.google-apps.document"
_MIME_SHEETS = "application/vnd.google-apps.spreadsheet"
_MIME_SLIDES = "application/vnd.google-apps.presentation"


def embedded_google_mcp_url() -> str:
    from app.core.config import settings

    url = (settings.google_mcp_embed_url or "").strip().rstrip("/")
    return url or EMBEDDED_GOOGLE_MCP_URL


def is_embedded_google_mcp_url(url: str | None) -> bool:
    if not url:
        return False
    raw = str(url).strip().rstrip("/")
    if raw == embedded_google_mcp_url():
        return True
    return raw.endswith("/internal/google-mcp/mcp")


def _allowed_hosts_for_embed(resource_url: str) -> list[str]:
    hosts = ["127.0.0.1:*", "localhost:*", "backend:*"]
    try:
        host = urlparse(resource_url).hostname
        if host and f"{host}:*" not in hosts:
            hosts.append(f"{host}:*")
    except Exception:
        pass
    return hosts


def _parse_toolsets(header: str) -> frozenset[str] | None:
    raw = (header or "").strip()
    if not raw or raw.lower() == "all":
        return None
    parts = {p.strip().lower() for p in raw.split(",") if p.strip()}
    if not parts or parts & {"all"}:
        return None
    return frozenset(parts & _ALL_TOOLSETS) or frozenset()


def _tool_allowed(name: str) -> bool:
    allowed = _toolsets.get()
    if allowed is None:
        return True
    prefix = name.split("_", 1)[0]
    return prefix in allowed


async def _google_request(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> Any:
    token = _access_token.get()
    if not token:
        raise ValueError("Missing Authorization bearer token")
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.request(
            method,
            url,
            params=params,
            json=json_body,
            headers={"Authorization": f"Bearer {token}"},
        )
    if response.status_code >= 400:
        detail = response.text[:800]
        raise ValueError(f"Google API {response.status_code}: {detail}")
    if not response.content:
        return {}
    try:
        return response.json()
    except Exception:
        return {"raw": response.text[:2000]}


def _dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _build_server() -> MCPServer:
    server = MCPServer("google-workspace")

    async def gmail_list_messages(
        query: str = "", max_results: int = 20
    ) -> str:
        """List Gmail messages (ids and thread ids)."""
        params: dict[str, Any] = {
            "maxResults": max(1, min(int(max_results or 20), 50)),
        }
        if query.strip():
            params["q"] = query.strip()
        data = await _google_request(
            "GET",
            "https://gmail.googleapis.com/gmail/v1/users/me/messages",
            params=params,
        )
        return _dumps(data)

    async def gmail_get_message(message_id: str, format: str = "metadata") -> str:
        """Get one Gmail message by id."""
        mid = message_id.strip()
        if not mid:
            raise ValueError("message_id is required")
        fmt = format.strip() or "metadata"
        data = await _google_request(
            "GET",
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{mid}",
            params={"format": fmt},
        )
        return _dumps(data)

    async def drive_list_files(
        query: str = "", page_size: int = 20
    ) -> str:
        """List Google Drive files."""
        params: dict[str, Any] = {
            "pageSize": max(1, min(int(page_size or 20), 50)),
            "fields": "files(id,name,mimeType,modifiedTime,webViewLink),nextPageToken",
        }
        if query.strip():
            params["q"] = query.strip()
        data = await _google_request(
            "GET",
            "https://www.googleapis.com/drive/v3/files",
            params=params,
        )
        return _dumps(data)

    async def drive_get_file(file_id: str) -> str:
        """Get Google Drive file metadata."""
        fid = file_id.strip()
        if not fid:
            raise ValueError("file_id is required")
        data = await _google_request(
            "GET",
            f"https://www.googleapis.com/drive/v3/files/{fid}",
            params={
                "fields": "id,name,mimeType,modifiedTime,webViewLink,owners,size",
            },
        )
        return _dumps(data)

    async def calendar_list_calendars() -> str:
        """List the user's calendars."""
        data = await _google_request(
            "GET",
            "https://www.googleapis.com/calendar/v3/users/me/calendarList",
            params={"maxResults": 50},
        )
        return _dumps(data)

    async def calendar_list_events(
        calendar_id: str = "primary",
        max_results: int = 20,
        time_min: str = "",
        time_max: str = "",
        query: str = "",
    ) -> str:
        """List calendar events."""
        cid = calendar_id.strip() or "primary"
        params: dict[str, Any] = {
            "maxResults": max(1, min(int(max_results or 20), 50)),
            "singleEvents": "true",
            "orderBy": "startTime",
        }
        if time_min.strip():
            params["timeMin"] = time_min.strip()
        if time_max.strip():
            params["timeMax"] = time_max.strip()
        if query.strip():
            params["q"] = query.strip()
        data = await _google_request(
            "GET",
            f"https://www.googleapis.com/calendar/v3/calendars/{cid}/events",
            params=params,
        )
        return _dumps(data)

    async def contacts_list(page_size: int = 20) -> str:
        """List Google Contacts (People API)."""
        data = await _google_request(
            "GET",
            "https://people.googleapis.com/v1/people/me/connections",
            params={
                "pageSize": max(1, min(int(page_size or 20), 50)),
                "personFields": "names,emailAddresses,phoneNumbers,organizations",
            },
        )
        return _dumps(data)

    async def contacts_search(query: str, page_size: int = 20) -> str:
        """Search contacts by name or email."""
        q = query.strip()
        if not q:
            raise ValueError("query is required")
        data = await _google_request(
            "GET",
            "https://people.googleapis.com/v1/people:searchContacts",
            params={
                "query": q,
                "pageSize": max(1, min(int(page_size or 20), 30)),
                "readMask": "names,emailAddresses,phoneNumbers,organizations",
            },
        )
        return _dumps(data)

    async def meet_list_upcoming(
        calendar_id: str = "primary",
        max_results: int = 20,
        time_min: str = "",
    ) -> str:
        """List upcoming calendar events that have a Meet / conference link."""
        cid = calendar_id.strip() or "primary"
        params: dict[str, Any] = {
            "maxResults": max(1, min(int(max_results or 20), 50)),
            "singleEvents": "true",
            "orderBy": "startTime",
            "conferenceDataVersion": 1,
        }
        if time_min.strip():
            params["timeMin"] = time_min.strip()
        data = await _google_request(
            "GET",
            f"https://www.googleapis.com/calendar/v3/calendars/{cid}/events",
            params=params,
        )
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return _dumps(data)
        meet_events = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("hangoutLink") or item.get("conferenceData"):
                meet_events.append(
                    {
                        "id": item.get("id"),
                        "summary": item.get("summary"),
                        "start": item.get("start"),
                        "end": item.get("end"),
                        "hangoutLink": item.get("hangoutLink"),
                        "conferenceData": item.get("conferenceData"),
                        "htmlLink": item.get("htmlLink"),
                    }
                )
        return _dumps({"items": meet_events})

    async def tasks_list_lists() -> str:
        """List Google Tasks task lists."""
        data = await _google_request(
            "GET",
            "https://tasks.googleapis.com/tasks/v1/users/@me/lists",
            params={"maxResults": 50},
        )
        return _dumps(data)

    async def tasks_list_tasks(
        tasklist_id: str = "@default",
        max_results: int = 40,
        show_completed: bool = False,
    ) -> str:
        """List tasks in a Google Tasks list."""
        tid = tasklist_id.strip() or "@default"
        data = await _google_request(
            "GET",
            f"https://tasks.googleapis.com/tasks/v1/lists/{tid}/tasks",
            params={
                "maxResults": max(1, min(int(max_results or 40), 100)),
                "showCompleted": "true" if show_completed else "false",
            },
        )
        return _dumps(data)

    async def _drive_list_by_mime(
        mime_type: str, *, query: str = "", page_size: int = 20
    ) -> Any:
        parts = [f"mimeType='{mime_type}'", "trashed=false"]
        if query.strip():
            safe = query.strip().replace("\\", "\\\\").replace("'", "\\'")
            parts.append(f"name contains '{safe}'")
        return await _google_request(
            "GET",
            "https://www.googleapis.com/drive/v3/files",
            params={
                "q": " and ".join(parts),
                "pageSize": max(1, min(int(page_size or 20), 50)),
                "fields": "files(id,name,mimeType,modifiedTime,webViewLink),nextPageToken",
            },
        )

    async def docs_list_documents(query: str = "", page_size: int = 20) -> str:
        """List Google Docs in Drive."""
        return _dumps(await _drive_list_by_mime(_MIME_DOCS, query=query, page_size=page_size))

    async def docs_get_document(document_id: str) -> str:
        """Get a Google Doc (title + plain text)."""
        did = document_id.strip()
        if not did:
            raise ValueError("document_id is required")
        data = await _google_request(
            "GET",
            f"https://docs.googleapis.com/v1/documents/{did}",
        )
        texts: list[str] = []
        body = data.get("body") if isinstance(data, dict) else None
        content = body.get("content") if isinstance(body, dict) else None
        if isinstance(content, list):
            for el in content:
                if not isinstance(el, dict):
                    continue
                para = el.get("paragraph")
                if not isinstance(para, dict):
                    continue
                for pe in para.get("elements") or []:
                    if not isinstance(pe, dict):
                        continue
                    run = pe.get("textRun")
                    if isinstance(run, dict) and run.get("content"):
                        texts.append(str(run["content"]))
        return _dumps(
            {
                "documentId": data.get("documentId") if isinstance(data, dict) else did,
                "title": data.get("title") if isinstance(data, dict) else None,
                "text": "".join(texts),
            }
        )

    async def sheets_list_spreadsheets(query: str = "", page_size: int = 20) -> str:
        """List Google Sheets in Drive."""
        return _dumps(
            await _drive_list_by_mime(_MIME_SHEETS, query=query, page_size=page_size)
        )

    async def sheets_get_values(
        spreadsheet_id: str,
        a1_range: str = "A1:Z100",
    ) -> str:
        """Read cell values from a spreadsheet range (A1 notation)."""
        from urllib.parse import quote

        sid = spreadsheet_id.strip()
        if not sid:
            raise ValueError("spreadsheet_id is required")
        rng = a1_range.strip() or "A1:Z100"
        data = await _google_request(
            "GET",
            "https://sheets.googleapis.com/v4/spreadsheets/"
            f"{quote(sid, safe='')}/values/{quote(rng, safe='')}",
        )
        return _dumps(data)

    async def slides_list_presentations(query: str = "", page_size: int = 20) -> str:
        """List Google Slides in Drive."""
        return _dumps(
            await _drive_list_by_mime(_MIME_SLIDES, query=query, page_size=page_size)
        )

    async def slides_get_presentation(presentation_id: str) -> str:
        """Get a Google Slides presentation (title + slide text)."""
        pid = presentation_id.strip()
        if not pid:
            raise ValueError("presentation_id is required")
        data = await _google_request(
            "GET",
            f"https://slides.googleapis.com/v1/presentations/{pid}",
        )
        slides_out: list[dict[str, Any]] = []
        for slide in (data.get("slides") if isinstance(data, dict) else None) or []:
            if not isinstance(slide, dict):
                continue
            texts: list[str] = []
            for pe in slide.get("pageElements") or []:
                if not isinstance(pe, dict):
                    continue
                shape = pe.get("shape")
                if not isinstance(shape, dict):
                    continue
                text = shape.get("text")
                if not isinstance(text, dict):
                    continue
                for te in text.get("textElements") or []:
                    if not isinstance(te, dict):
                        continue
                    run = te.get("textRun")
                    if isinstance(run, dict) and run.get("content"):
                        texts.append(str(run["content"]))
            slides_out.append(
                {
                    "objectId": slide.get("objectId"),
                    "text": "".join(texts).strip(),
                }
            )
        return _dumps(
            {
                "presentationId": data.get("presentationId")
                if isinstance(data, dict)
                else pid,
                "title": data.get("title") if isinstance(data, dict) else None,
                "slides": slides_out,
            }
        )

    async def chat_list_spaces(page_size: int = 30) -> str:
        """List Google Chat spaces the user can see."""
        data = await _google_request(
            "GET",
            "https://chat.googleapis.com/v1/spaces",
            params={"pageSize": max(1, min(int(page_size or 30), 100))},
        )
        return _dumps(data)

    async def chat_list_messages(
        space: str,
        page_size: int = 30,
    ) -> str:
        """List recent messages in a Google Chat space (spaces/xxx)."""
        parent = space.strip()
        if not parent:
            raise ValueError("space is required (e.g. spaces/AAAA)")
        if not parent.startswith("spaces/"):
            parent = f"spaces/{parent}"
        data = await _google_request(
            "GET",
            f"https://chat.googleapis.com/v1/{parent}/messages",
            params={"pageSize": max(1, min(int(page_size or 30), 100))},
        )
        return _dumps(data)

    specs: list[tuple[Any, str, str]] = [
        (gmail_list_messages, "gmail_list_messages", "List Gmail messages"),
        (gmail_get_message, "gmail_get_message", "Get a Gmail message"),
        (drive_list_files, "drive_list_files", "List Drive files"),
        (drive_get_file, "drive_get_file", "Get Drive file metadata"),
        (calendar_list_calendars, "calendar_list_calendars", "List calendars"),
        (calendar_list_events, "calendar_list_events", "List calendar events"),
        (contacts_list, "contacts_list", "List contacts"),
        (contacts_search, "contacts_search", "Search contacts"),
        (meet_list_upcoming, "meet_list_upcoming", "List upcoming Meet events"),
        (tasks_list_lists, "tasks_list_lists", "List task lists"),
        (tasks_list_tasks, "tasks_list_tasks", "List tasks"),
        (docs_list_documents, "docs_list_documents", "List Google Docs"),
        (docs_get_document, "docs_get_document", "Get a Google Doc"),
        (sheets_list_spreadsheets, "sheets_list_spreadsheets", "List Google Sheets"),
        (sheets_get_values, "sheets_get_values", "Read spreadsheet values"),
        (slides_list_presentations, "slides_list_presentations", "List Google Slides"),
        (slides_get_presentation, "slides_get_presentation", "Get a Google Slides deck"),
        (chat_list_spaces, "chat_list_spaces", "List Google Chat spaces"),
        (chat_list_messages, "chat_list_messages", "List Google Chat messages"),
    ]
    for fn, name, description in specs:
        server.add_tool(fn, name=name, description=description)

    _orig_list = server.list_tools

    async def _filtered_list_tools() -> list[Any]:
        tools = await _orig_list()
        return [t for t in tools if _tool_allowed(t.name)]

    server.list_tools = _filtered_list_tools  # type: ignore[method-assign]

    _tool_manager = server._tool_manager
    _orig_call = _tool_manager.call_tool

    async def _gated_call_tool(
        name: str,
        arguments: dict[str, Any],
        context: Any,
        convert_result: bool = False,
    ) -> Any:
        if not _tool_allowed(name):
            raise ValueError(f"Tool {name!r} is not enabled for this connection")
        return await _orig_call(
            name, arguments, context, convert_result=convert_result
        )

    _tool_manager.call_tool = _gated_call_tool  # type: ignore[method-assign]
    return server


class _BearerToolsetMiddleware:
    """ASGI middleware: require Bearer, set token + toolset context vars."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers") or []
        }
        auth = headers.get("authorization", "")
        if not auth.lower().startswith("bearer ") or not auth[7:].strip():
            response = JSONResponse(
                {"error": "Bearer access token required"},
                status_code=401,
            )
            await response(scope, receive, send)
            return

        token_tokens = [_access_token.set(auth[7:].strip())]
        toolset_tokens = [_toolsets.set(_parse_toolsets(headers.get("x-toolsets", "")))]
        try:
            await self.app(scope, receive, send)
        finally:
            for tok in reversed(toolset_tokens):
                _toolsets.reset(tok)
            for tok in reversed(token_tokens):
                _access_token.reset(tok)


class _UnavailableApp:
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return
        response = Response("Google MCP unavailable", status_code=503)
        await response(scope, receive, send)


class GoogleMcpEmbed:
    def __init__(self) -> None:
        self._lock: Any = None
        self._lock_loop: Any = None
        self._app: ASGIApp = _UnavailableApp()
        self._lifespan_cm: Any = None
        self._started = False

    def _loop_lock(self) -> Any:
        import asyncio

        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    async def _swap(self) -> None:
        if self._lifespan_cm is not None:
            try:
                await self._lifespan_cm.__aexit__(None, None, None)
            except Exception:
                logger.exception("Failed to shut down previous Google MCP app")
            self._lifespan_cm = None

        server = _build_server()
        resource_url = embedded_google_mcp_url()
        starlette_app = server.streamable_http_app(
            streamable_http_path="/mcp",
            json_response=True,
            stateless_http=True,
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=_allowed_hosts_for_embed(resource_url),
            ),
        )
        cm = starlette_app.router.lifespan_context(starlette_app)
        await cm.__aenter__()
        self._app = _BearerToolsetMiddleware(starlette_app)
        self._lifespan_cm = cm
        logger.info("Embedded Google MCP ready")

    async def ensure(self) -> None:
        async with self._loop_lock():
            if self._started:
                return
            await self._swap()
            self._started = True

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        await self.ensure()
        try:
            yield
        finally:
            async with self._loop_lock():
                if self._lifespan_cm is not None:
                    try:
                        await self._lifespan_cm.__aexit__(None, None, None)
                    except Exception:
                        logger.exception("Failed to shut down Google MCP app")
                    self._lifespan_cm = None
                self._app = _UnavailableApp()
                self._started = False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
            return

        await self.ensure()
        await self._app(scope, receive, send)


google_mcp_embed = GoogleMcpEmbed()


def sync_google_catalog_servers(session: Any) -> None:
    from app.services.integrations.catalog import list_catalog, is_google_catalog
    from app.services.integrations.materialize import ensure_catalog_mcp_server

    for entry in list_catalog():
        if not is_google_catalog(entry.catalog_id):
            continue
        ensure_catalog_mcp_server(session, entry, mcp_url=embedded_google_mcp_url())
