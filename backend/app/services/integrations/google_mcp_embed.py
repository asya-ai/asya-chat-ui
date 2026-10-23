"""Embedded Google Workspace MCP (in-process Streamable HTTP).

Accepts the user's Google OAuth access token as Bearer Authorization and
narrows advertised tools via X-Toolsets (gmail, drive, calendar, contacts,
meet, tasks, docs, sheets, slides, chat). Celery workers call
GOOGLE_MCP_EMBED_URL on the API host.
"""

from __future__ import annotations

import base64
import json
import logging
from contextlib import asynccontextmanager
from contextvars import ContextVar
from email.mime.text import MIMEText
from typing import Any, AsyncIterator
from urllib.parse import quote, urlparse

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
_MIME_FOLDER = "application/vnd.google-apps.folder"
_EXPORT_MAX_CHARS = 80_000


def _gmail_raw_message(
    *,
    to: str,
    subject: str,
    body: str,
    cc: str = "",
    bcc: str = "",
    in_reply_to: str = "",
    references: str = "",
) -> str:
    msg = MIMEText(body or "", _charset="utf-8")
    msg["To"] = to.strip()
    msg["Subject"] = subject.strip() or "(no subject)"
    if cc.strip():
        msg["Cc"] = cc.strip()
    if bcc.strip():
        msg["Bcc"] = bcc.strip()
    if in_reply_to.strip():
        msg["In-Reply-To"] = in_reply_to.strip()
    if references.strip():
        msg["References"] = references.strip()
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii").rstrip("=")


def _header_value(payload: dict[str, Any], name: str) -> str:
    headers = payload.get("headers") if isinstance(payload, dict) else None
    if not isinstance(headers, list):
        return ""
    want = name.lower()
    for h in headers:
        if isinstance(h, dict) and str(h.get("name", "")).lower() == want:
            return str(h.get("value") or "")
    return ""


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
    content: bytes | None = None,
    extra_headers: dict[str, str] | None = None,
    as_bytes: bool = False,
) -> Any:
    token = _access_token.get()
    if not token:
        raise ValueError("Missing Authorization bearer token")
    headers = {"Authorization": f"Bearer {token}"}
    if extra_headers:
        headers.update(extra_headers)
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.request(
            method,
            url,
            params=params,
            json=json_body,
            content=content,
            headers=headers,
        )
    if response.status_code >= 400:
        detail = response.text[:800]
        raise ValueError(f"Google API {response.status_code}: {detail}")
    if not response.content:
        return {}
    if as_bytes:
        raw = response.content
        ctype = response.headers.get("content-type", "")
        text: str | None = None
        if "text/" in ctype or "json" in ctype or "xml" in ctype:
            text = raw.decode("utf-8", errors="replace")
            if len(text) > _EXPORT_MAX_CHARS:
                text = text[:_EXPORT_MAX_CHARS] + "\n…[truncated]"
        return {
            "contentType": ctype,
            "size": len(raw),
            "text": text,
            "base64": base64.b64encode(raw[: min(len(raw), 48_000)]).decode("ascii")
            if text is None
            else None,
            "truncated": len(raw) > 48_000 and text is None,
        }
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

    async def gmail_send_message(
        to: str,
        subject: str,
        body: str,
        cc: str = "",
        bcc: str = "",
    ) -> str:
        """Send an email."""
        if not to.strip():
            raise ValueError("to is required")
        raw = _gmail_raw_message(
            to=to, subject=subject, body=body, cc=cc, bcc=bcc
        )
        data = await _google_request(
            "POST",
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            json_body={"raw": raw},
        )
        return _dumps(data)

    async def gmail_create_draft(
        to: str,
        subject: str,
        body: str,
        cc: str = "",
        bcc: str = "",
    ) -> str:
        """Create a Gmail draft."""
        if not to.strip():
            raise ValueError("to is required")
        raw = _gmail_raw_message(
            to=to, subject=subject, body=body, cc=cc, bcc=bcc
        )
        data = await _google_request(
            "POST",
            "https://gmail.googleapis.com/gmail/v1/users/me/drafts",
            json_body={"message": {"raw": raw}},
        )
        return _dumps(data)

    async def gmail_reply_message(
        message_id: str,
        body: str,
        reply_all: bool = False,
    ) -> str:
        """Reply in-thread to a Gmail message."""
        mid = message_id.strip()
        if not mid:
            raise ValueError("message_id is required")
        if not (body or "").strip():
            raise ValueError("body is required")
        original = await _google_request(
            "GET",
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{mid}",
            params={"format": "metadata", "metadataHeaders": ["From", "To", "Cc", "Subject", "Message-ID", "References"]},
        )
        payload = original.get("payload") if isinstance(original, dict) else {}
        thread_id = original.get("threadId") if isinstance(original, dict) else None
        from_addr = _header_value(payload or {}, "From")
        to_addr = _header_value(payload or {}, "To")
        cc_addr = _header_value(payload or {}, "Cc")
        subject = _header_value(payload or {}, "Subject")
        msg_id = _header_value(payload or {}, "Message-ID")
        refs = _header_value(payload or {}, "References")
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}" if subject else "Re:"
        reply_to = from_addr
        reply_cc = ""
        if reply_all:
            # Keep original To/Cc minus obvious duplicates; From is the reply target.
            parts = [p.strip() for p in f"{to_addr},{cc_addr}".split(",") if p.strip()]
            reply_cc = ", ".join(parts)
        references = " ".join(x for x in (refs, msg_id) if x).strip()
        raw = _gmail_raw_message(
            to=reply_to,
            subject=subject,
            body=body,
            cc=reply_cc,
            in_reply_to=msg_id,
            references=references,
        )
        body_json: dict[str, Any] = {"raw": raw}
        if thread_id:
            body_json["threadId"] = thread_id
        data = await _google_request(
            "POST",
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            json_body=body_json,
        )
        return _dumps(data)

    async def gmail_trash_message(message_id: str) -> str:
        """Move a Gmail message to trash."""
        mid = message_id.strip()
        if not mid:
            raise ValueError("message_id is required")
        data = await _google_request(
            "POST",
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{mid}/trash",
        )
        return _dumps(data)

    async def gmail_modify_labels(
        message_id: str,
        add_label_ids: str = "",
        remove_label_ids: str = "",
    ) -> str:
        """Add/remove Gmail labels (comma-separated ids, e.g. STARRED,UNREAD)."""
        mid = message_id.strip()
        if not mid:
            raise ValueError("message_id is required")
        add = [x.strip() for x in add_label_ids.split(",") if x.strip()]
        remove = [x.strip() for x in remove_label_ids.split(",") if x.strip()]
        if not add and not remove:
            raise ValueError("add_label_ids or remove_label_ids is required")
        data = await _google_request(
            "POST",
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{mid}/modify",
            json_body={"addLabelIds": add, "removeLabelIds": remove},
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
                "fields": "id,name,mimeType,modifiedTime,webViewLink,owners,size,parents",
            },
        )
        return _dumps(data)

    async def drive_create_folder(name: str, parent_id: str = "") -> str:
        """Create a Drive folder."""
        title = name.strip()
        if not title:
            raise ValueError("name is required")
        body: dict[str, Any] = {"name": title, "mimeType": _MIME_FOLDER}
        if parent_id.strip():
            body["parents"] = [parent_id.strip()]
        data = await _google_request(
            "POST",
            "https://www.googleapis.com/drive/v3/files",
            params={"fields": "id,name,mimeType,webViewLink,parents"},
            json_body=body,
        )
        return _dumps(data)

    async def drive_move_file(
        file_id: str,
        new_parent_id: str,
        previous_parent_id: str = "",
    ) -> str:
        """Move a Drive file into a folder."""
        fid = file_id.strip()
        dest = new_parent_id.strip()
        if not fid or not dest:
            raise ValueError("file_id and new_parent_id are required")
        params: dict[str, Any] = {
            "addParents": dest,
            "fields": "id,name,parents,webViewLink",
        }
        prev = previous_parent_id.strip()
        if not prev:
            meta = await _google_request(
                "GET",
                f"https://www.googleapis.com/drive/v3/files/{fid}",
                params={"fields": "parents"},
            )
            parents = meta.get("parents") if isinstance(meta, dict) else None
            if isinstance(parents, list) and parents:
                prev = ",".join(str(p) for p in parents)
        if prev:
            params["removeParents"] = prev
        data = await _google_request(
            "PATCH",
            f"https://www.googleapis.com/drive/v3/files/{fid}",
            params=params,
            json_body={},
        )
        return _dumps(data)

    async def drive_trash_file(file_id: str) -> str:
        """Move a Drive file to trash."""
        fid = file_id.strip()
        if not fid:
            raise ValueError("file_id is required")
        data = await _google_request(
            "PATCH",
            f"https://www.googleapis.com/drive/v3/files/{fid}",
            params={"fields": "id,name,trashed"},
            json_body={"trashed": True},
        )
        return _dumps(data)

    async def drive_copy_file(file_id: str, name: str = "") -> str:
        """Copy a Drive file."""
        fid = file_id.strip()
        if not fid:
            raise ValueError("file_id is required")
        body: dict[str, Any] = {}
        if name.strip():
            body["name"] = name.strip()
        data = await _google_request(
            "POST",
            f"https://www.googleapis.com/drive/v3/files/{fid}/copy",
            params={"fields": "id,name,mimeType,webViewLink"},
            json_body=body or None,
        )
        return _dumps(data)

    async def drive_export_file(
        file_id: str,
        mime_type: str = "text/plain",
    ) -> str:
        """Export a Google Docs/Sheets/Slides file (or download binary)."""
        fid = file_id.strip()
        if not fid:
            raise ValueError("file_id is required")
        meta = await _google_request(
            "GET",
            f"https://www.googleapis.com/drive/v3/files/{fid}",
            params={"fields": "id,name,mimeType"},
        )
        mime = str((meta or {}).get("mimeType") or "")
        export_mime = mime_type.strip() or "text/plain"
        if mime.startswith("application/vnd.google-apps."):
            data = await _google_request(
                "GET",
                f"https://www.googleapis.com/drive/v3/files/{fid}/export",
                params={"mimeType": export_mime},
                as_bytes=True,
            )
        else:
            data = await _google_request(
                "GET",
                f"https://www.googleapis.com/drive/v3/files/{fid}",
                params={"alt": "media"},
                as_bytes=True,
            )
        return _dumps({"file": meta, "content": data})

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
            f"https://www.googleapis.com/calendar/v3/calendars/{quote(cid, safe='')}/events",
            params=params,
        )
        return _dumps(data)

    async def calendar_create_event(
        summary: str,
        start: str,
        end: str,
        calendar_id: str = "primary",
        description: str = "",
        location: str = "",
        attendees: str = "",
        timezone: str = "",
        create_meet_link: bool = False,
    ) -> str:
        """Create a calendar event. start/end: RFC3339 or YYYY-MM-DD for all-day."""
        cid = calendar_id.strip() or "primary"
        title = summary.strip()
        if not title or not start.strip() or not end.strip():
            raise ValueError("summary, start, and end are required")

        def _when(value: str) -> dict[str, str]:
            v = value.strip()
            if "T" in v:
                out = {"dateTime": v}
                if timezone.strip():
                    out["timeZone"] = timezone.strip()
                return out
            return {"date": v}

        body: dict[str, Any] = {
            "summary": title,
            "start": _when(start),
            "end": _when(end),
        }
        if description.strip():
            body["description"] = description.strip()
        if location.strip():
            body["location"] = location.strip()
        emails = [e.strip() for e in attendees.split(",") if e.strip()]
        if emails:
            body["attendees"] = [{"email": e} for e in emails]
        params: dict[str, Any] = {}
        if create_meet_link:
            params["conferenceDataVersion"] = 1
            body["conferenceData"] = {
                "createRequest": {
                    "requestId": f"chatui-{abs(hash((title, start, end))) % 10**12}",
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            }
        data = await _google_request(
            "POST",
            f"https://www.googleapis.com/calendar/v3/calendars/{quote(cid, safe='')}/events",
            params=params or None,
            json_body=body,
        )
        return _dumps(data)

    async def calendar_update_event(
        event_id: str,
        calendar_id: str = "primary",
        summary: str = "",
        start: str = "",
        end: str = "",
        description: str = "",
        location: str = "",
        timezone: str = "",
    ) -> str:
        """Patch fields on a calendar event."""
        cid = calendar_id.strip() or "primary"
        eid = event_id.strip()
        if not eid:
            raise ValueError("event_id is required")
        body: dict[str, Any] = {}
        if summary.strip():
            body["summary"] = summary.strip()
        if description:
            body["description"] = description
        if location:
            body["location"] = location
        if start.strip():
            v = start.strip()
            body["start"] = (
                {"dateTime": v, **({"timeZone": timezone.strip()} if timezone.strip() else {})}
                if "T" in v
                else {"date": v}
            )
        if end.strip():
            v = end.strip()
            body["end"] = (
                {"dateTime": v, **({"timeZone": timezone.strip()} if timezone.strip() else {})}
                if "T" in v
                else {"date": v}
            )
        if not body:
            raise ValueError("Provide at least one field to update")
        data = await _google_request(
            "PATCH",
            f"https://www.googleapis.com/calendar/v3/calendars/{quote(cid, safe='')}/events/{quote(eid, safe='')}",
            json_body=body,
        )
        return _dumps(data)

    async def calendar_delete_event(
        event_id: str,
        calendar_id: str = "primary",
    ) -> str:
        """Delete a calendar event."""
        cid = calendar_id.strip() or "primary"
        eid = event_id.strip()
        if not eid:
            raise ValueError("event_id is required")
        await _google_request(
            "DELETE",
            f"https://www.googleapis.com/calendar/v3/calendars/{quote(cid, safe='')}/events/{quote(eid, safe='')}",
        )
        return _dumps({"deleted": True, "eventId": eid, "calendarId": cid})

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

    async def meet_create_event(
        summary: str,
        start: str,
        end: str,
        calendar_id: str = "primary",
        description: str = "",
        attendees: str = "",
        timezone: str = "",
    ) -> str:
        """Create a calendar event with a Google Meet link."""
        return await calendar_create_event(
            summary=summary,
            start=start,
            end=end,
            calendar_id=calendar_id,
            description=description,
            attendees=attendees,
            timezone=timezone,
            create_meet_link=True,
        )

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

    async def tasks_create_task(
        title: str,
        tasklist_id: str = "@default",
        notes: str = "",
        due: str = "",
    ) -> str:
        """Create a task."""
        tid = tasklist_id.strip() or "@default"
        name = title.strip()
        if not name:
            raise ValueError("title is required")
        body: dict[str, Any] = {"title": name}
        if notes.strip():
            body["notes"] = notes.strip()
        if due.strip():
            body["due"] = due.strip()
        data = await _google_request(
            "POST",
            f"https://tasks.googleapis.com/tasks/v1/lists/{quote(tid, safe='')}/tasks",
            json_body=body,
        )
        return _dumps(data)

    async def tasks_complete_task(
        task_id: str,
        tasklist_id: str = "@default",
        completed: bool = True,
    ) -> str:
        """Mark a task completed or reopen it."""
        tid = tasklist_id.strip() or "@default"
        kid = task_id.strip()
        if not kid:
            raise ValueError("task_id is required")
        data = await _google_request(
            "PATCH",
            f"https://tasks.googleapis.com/tasks/v1/lists/{quote(tid, safe='')}/tasks/{quote(kid, safe='')}",
            json_body={"status": "completed" if completed else "needsAction"},
        )
        return _dumps(data)

    async def tasks_delete_task(
        task_id: str,
        tasklist_id: str = "@default",
    ) -> str:
        """Delete a task."""
        tid = tasklist_id.strip() or "@default"
        kid = task_id.strip()
        if not kid:
            raise ValueError("task_id is required")
        await _google_request(
            "DELETE",
            f"https://tasks.googleapis.com/tasks/v1/lists/{quote(tid, safe='')}/tasks/{quote(kid, safe='')}",
        )
        return _dumps({"deleted": True, "taskId": kid, "tasklistId": tid})

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

    async def docs_create_document(title: str = "Untitled document") -> str:
        """Create a new Google Doc."""
        name = title.strip() or "Untitled document"
        data = await _google_request(
            "POST",
            "https://docs.googleapis.com/v1/documents",
            json_body={"title": name},
        )
        return _dumps(
            {
                "documentId": data.get("documentId")
                if isinstance(data, dict)
                else None,
                "title": data.get("title") if isinstance(data, dict) else name,
            }
        )

    async def docs_append_text(document_id: str, text: str) -> str:
        """Append plain text at the end of a Google Doc."""
        did = document_id.strip()
        body_text = text if text is not None else ""
        if not did:
            raise ValueError("document_id is required")
        if not body_text:
            raise ValueError("text is required")
        doc = await _google_request(
            "GET",
            f"https://docs.googleapis.com/v1/documents/{did}",
            params={"fields": "body(content(endIndex))"},
        )
        end_index = 1
        content = (
            (doc.get("body") or {}).get("content")
            if isinstance(doc, dict)
            else None
        )
        if isinstance(content, list):
            for el in content:
                if isinstance(el, dict) and isinstance(el.get("endIndex"), int):
                    end_index = max(end_index, el["endIndex"])
        # Insert before the final newline of the document body.
        insert_at = max(1, end_index - 1)
        data = await _google_request(
            "POST",
            f"https://docs.googleapis.com/v1/documents/{did}:batchUpdate",
            json_body={
                "requests": [
                    {
                        "insertText": {
                            "location": {"index": insert_at},
                            "text": body_text,
                        }
                    }
                ]
            },
        )
        return _dumps(data)

    async def docs_replace_text(
        document_id: str,
        find_text: str,
        replace_text: str,
        match_case: bool = True,
    ) -> str:
        """Replace all occurrences of find_text in a Google Doc."""
        did = document_id.strip()
        find = find_text
        if not did:
            raise ValueError("document_id is required")
        if not find:
            raise ValueError("find_text is required")
        data = await _google_request(
            "POST",
            f"https://docs.googleapis.com/v1/documents/{did}:batchUpdate",
            json_body={
                "requests": [
                    {
                        "replaceAllText": {
                            "containsText": {
                                "text": find,
                                "matchCase": bool(match_case),
                            },
                            "replaceText": replace_text if replace_text is not None else "",
                        }
                    }
                ]
            },
        )
        return _dumps(data)

    async def docs_insert_text(
        document_id: str,
        text: str,
        index: int = 1,
    ) -> str:
        """Insert text at a Docs body index (1 = start)."""
        did = document_id.strip()
        body_text = text if text is not None else ""
        if not did:
            raise ValueError("document_id is required")
        if not body_text:
            raise ValueError("text is required")
        data = await _google_request(
            "POST",
            f"https://docs.googleapis.com/v1/documents/{did}:batchUpdate",
            json_body={
                "requests": [
                    {
                        "insertText": {
                            "location": {"index": max(1, int(index or 1))},
                            "text": body_text,
                        }
                    }
                ]
            },
        )
        return _dumps(data)

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

    def _parse_sheet_values(values_json: str) -> list[list[Any]]:
        raw = (values_json or "").strip()
        if not raw:
            raise ValueError("values_json is required (JSON 2D array)")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("values_json must be valid JSON") from exc
        if not isinstance(parsed, list) or not parsed:
            raise ValueError("values_json must be a non-empty JSON array of rows")
        rows: list[list[Any]] = []
        for row in parsed:
            if isinstance(row, list):
                rows.append(row)
            else:
                rows.append([row])
        return rows

    async def sheets_update_values(
        spreadsheet_id: str,
        a1_range: str,
        values_json: str,
        value_input_option: str = "USER_ENTERED",
    ) -> str:
        """Overwrite cells in a range. values_json is a JSON 2D array."""
        from urllib.parse import quote

        sid = spreadsheet_id.strip()
        rng = a1_range.strip()
        if not sid or not rng:
            raise ValueError("spreadsheet_id and a1_range are required")
        rows = _parse_sheet_values(values_json)
        opt = (value_input_option or "USER_ENTERED").strip() or "USER_ENTERED"
        data = await _google_request(
            "PUT",
            "https://sheets.googleapis.com/v4/spreadsheets/"
            f"{quote(sid, safe='')}/values/{quote(rng, safe='')}",
            params={"valueInputOption": opt},
            json_body={"values": rows},
        )
        return _dumps(data)

    async def sheets_append_values(
        spreadsheet_id: str,
        a1_range: str,
        values_json: str,
        value_input_option: str = "USER_ENTERED",
    ) -> str:
        """Append rows after the last data in a1_range (e.g. Sheet1!A1)."""
        from urllib.parse import quote

        sid = spreadsheet_id.strip()
        rng = a1_range.strip()
        if not sid or not rng:
            raise ValueError("spreadsheet_id and a1_range are required")
        rows = _parse_sheet_values(values_json)
        opt = (value_input_option or "USER_ENTERED").strip() or "USER_ENTERED"
        data = await _google_request(
            "POST",
            "https://sheets.googleapis.com/v4/spreadsheets/"
            f"{quote(sid, safe='')}/values/{quote(rng, safe='')}:append",
            params={
                "valueInputOption": opt,
                "insertDataOption": "INSERT_ROWS",
            },
            json_body={"values": rows},
        )
        return _dumps(data)

    async def sheets_add_sheet(spreadsheet_id: str, title: str) -> str:
        """Add a new tab (sheet) to a spreadsheet."""
        sid = spreadsheet_id.strip()
        name = title.strip()
        if not sid or not name:
            raise ValueError("spreadsheet_id and title are required")
        data = await _google_request(
            "POST",
            f"https://sheets.googleapis.com/v4/spreadsheets/{sid}:batchUpdate",
            json_body={
                "requests": [{"addSheet": {"properties": {"title": name}}}],
            },
        )
        return _dumps(data)

    async def sheets_list_sheets(spreadsheet_id: str) -> str:
        """List tabs (sheetId + title) in a spreadsheet."""
        sid = spreadsheet_id.strip()
        if not sid:
            raise ValueError("spreadsheet_id is required")
        data = await _google_request(
            "GET",
            f"https://sheets.googleapis.com/v4/spreadsheets/{sid}",
            params={"fields": "spreadsheetId,properties.title,sheets.properties"},
        )
        sheets_out: list[dict[str, Any]] = []
        for sheet in (data.get("sheets") if isinstance(data, dict) else None) or []:
            if not isinstance(sheet, dict):
                continue
            props = sheet.get("properties")
            if not isinstance(props, dict):
                continue
            sheets_out.append(
                {
                    "sheetId": props.get("sheetId"),
                    "title": props.get("title"),
                    "index": props.get("index"),
                }
            )
        return _dumps(
            {
                "spreadsheetId": data.get("spreadsheetId")
                if isinstance(data, dict)
                else sid,
                "title": (data.get("properties") or {}).get("title")
                if isinstance(data, dict)
                else None,
                "sheets": sheets_out,
            }
        )

    async def _resolve_sheet_id(
        spreadsheet_id: str, *, sheet_title: str, sheet_id: int | None
    ) -> int:
        if sheet_id is not None:
            return int(sheet_id)
        title = sheet_title.strip()
        if not title:
            raise ValueError("sheet_title or sheet_id is required")
        listed = json.loads(await sheets_list_sheets(spreadsheet_id))
        for sheet in listed.get("sheets") or []:
            if isinstance(sheet, dict) and sheet.get("title") == title:
                sid = sheet.get("sheetId")
                if sid is not None:
                    return int(sid)
        raise ValueError(f"No sheet titled {title!r}")

    async def sheets_delete_sheet(
        spreadsheet_id: str,
        sheet_title: str = "",
        sheet_id: int | None = None,
    ) -> str:
        """Delete a tab by title or sheet_id."""
        sid = spreadsheet_id.strip()
        if not sid:
            raise ValueError("spreadsheet_id is required")
        resolved = await _resolve_sheet_id(
            sid, sheet_title=sheet_title, sheet_id=sheet_id
        )
        data = await _google_request(
            "POST",
            f"https://sheets.googleapis.com/v4/spreadsheets/{sid}:batchUpdate",
            json_body={"requests": [{"deleteSheet": {"sheetId": resolved}}]},
        )
        return _dumps({"deletedSheetId": resolved, "result": data})

    async def sheets_rename_sheet(
        spreadsheet_id: str,
        new_title: str,
        sheet_title: str = "",
        sheet_id: int | None = None,
    ) -> str:
        """Rename a tab by title or sheet_id."""
        sid = spreadsheet_id.strip()
        name = new_title.strip()
        if not sid or not name:
            raise ValueError("spreadsheet_id and new_title are required")
        resolved = await _resolve_sheet_id(
            sid, sheet_title=sheet_title, sheet_id=sheet_id
        )
        data = await _google_request(
            "POST",
            f"https://sheets.googleapis.com/v4/spreadsheets/{sid}:batchUpdate",
            json_body={
                "requests": [
                    {
                        "updateSheetProperties": {
                            "properties": {"sheetId": resolved, "title": name},
                            "fields": "title",
                        }
                    }
                ]
            },
        )
        return _dumps(data)

    async def sheets_clear_values(spreadsheet_id: str, a1_range: str) -> str:
        """Clear cell values in a range (keeps formatting)."""
        sid = spreadsheet_id.strip()
        rng = a1_range.strip()
        if not sid or not rng:
            raise ValueError("spreadsheet_id and a1_range are required")
        data = await _google_request(
            "POST",
            "https://sheets.googleapis.com/v4/spreadsheets/"
            f"{quote(sid, safe='')}/values/{quote(rng, safe='')}:clear",
            json_body={},
        )
        return _dumps(data)

    async def sheets_delete_dimension(
        spreadsheet_id: str,
        sheet_title: str = "",
        sheet_id: int | None = None,
        dimension: str = "ROWS",
        start_index: int = 0,
        end_index: int = 1,
    ) -> str:
        """Delete rows or columns (0-based start, end exclusive)."""
        sid = spreadsheet_id.strip()
        if not sid:
            raise ValueError("spreadsheet_id is required")
        dim = (dimension or "ROWS").strip().upper()
        if dim not in {"ROWS", "COLUMNS"}:
            raise ValueError("dimension must be ROWS or COLUMNS")
        start = max(0, int(start_index))
        end = max(start + 1, int(end_index))
        resolved = await _resolve_sheet_id(
            sid, sheet_title=sheet_title, sheet_id=sheet_id
        )
        data = await _google_request(
            "POST",
            f"https://sheets.googleapis.com/v4/spreadsheets/{sid}:batchUpdate",
            json_body={
                "requests": [
                    {
                        "deleteDimension": {
                            "range": {
                                "sheetId": resolved,
                                "dimension": dim,
                                "startIndex": start,
                                "endIndex": end,
                            }
                        }
                    }
                ]
            },
        )
        return _dumps(data)

    async def sheets_copy_sheet(
        spreadsheet_id: str,
        sheet_title: str = "",
        sheet_id: int | None = None,
        destination_spreadsheet_id: str = "",
    ) -> str:
        """Duplicate a tab in-place, or copy it to another spreadsheet."""
        sid = spreadsheet_id.strip()
        if not sid:
            raise ValueError("spreadsheet_id is required")
        resolved = await _resolve_sheet_id(
            sid, sheet_title=sheet_title, sheet_id=sheet_id
        )
        dest = destination_spreadsheet_id.strip()
        if dest and dest != sid:
            data = await _google_request(
                "POST",
                f"https://sheets.googleapis.com/v4/spreadsheets/{sid}/sheets/{resolved}:copyTo",
                json_body={"destinationSpreadsheetId": dest},
            )
            return _dumps(data)
        data = await _google_request(
            "POST",
            f"https://sheets.googleapis.com/v4/spreadsheets/{sid}:batchUpdate",
            json_body={
                "requests": [
                    {
                        "duplicateSheet": {
                            "sourceSheetId": resolved,
                        }
                    }
                ]
            },
        )
        return _dumps(data)

    async def sheets_create_spreadsheet(title: str = "Untitled spreadsheet") -> str:
        """Create a new spreadsheet; returns spreadsheetId and URL."""
        name = title.strip() or "Untitled spreadsheet"
        data = await _google_request(
            "POST",
            "https://sheets.googleapis.com/v4/spreadsheets",
            json_body={"properties": {"title": name}},
        )
        return _dumps(
            {
                "spreadsheetId": data.get("spreadsheetId")
                if isinstance(data, dict)
                else None,
                "spreadsheetUrl": data.get("spreadsheetUrl")
                if isinstance(data, dict)
                else None,
                "title": name,
            }
        )

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

    async def slides_create_presentation(
        title: str = "Untitled presentation",
    ) -> str:
        """Create a new Google Slides presentation."""
        name = title.strip() or "Untitled presentation"
        data = await _google_request(
            "POST",
            "https://slides.googleapis.com/v1/presentations",
            json_body={"title": name},
        )
        return _dumps(
            {
                "presentationId": data.get("presentationId")
                if isinstance(data, dict)
                else None,
                "title": data.get("title") if isinstance(data, dict) else name,
            }
        )

    async def slides_add_slide(
        presentation_id: str,
        insertion_index: int | None = None,
    ) -> str:
        """Add a blank slide to a presentation."""
        pid = presentation_id.strip()
        if not pid:
            raise ValueError("presentation_id is required")
        create_slide: dict[str, Any] = {}
        if insertion_index is not None:
            create_slide["insertionIndex"] = int(insertion_index)
        data = await _google_request(
            "POST",
            f"https://slides.googleapis.com/v1/presentations/{pid}:batchUpdate",
            json_body={"requests": [{"createSlide": create_slide}]},
        )
        return _dumps(data)

    async def slides_delete_slide(
        presentation_id: str,
        slide_object_id: str,
    ) -> str:
        """Delete a slide by objectId (from slides_get_presentation)."""
        pid = presentation_id.strip()
        oid = slide_object_id.strip()
        if not pid or not oid:
            raise ValueError("presentation_id and slide_object_id are required")
        data = await _google_request(
            "POST",
            f"https://slides.googleapis.com/v1/presentations/{pid}:batchUpdate",
            json_body={"requests": [{"deleteObject": {"objectId": oid}}]},
        )
        return _dumps(data)

    async def slides_replace_all_text(
        presentation_id: str,
        find_text: str,
        replace_text: str,
        match_case: bool = True,
    ) -> str:
        """Replace text across all slides."""
        pid = presentation_id.strip()
        find = find_text
        if not pid:
            raise ValueError("presentation_id is required")
        if not find:
            raise ValueError("find_text is required")
        data = await _google_request(
            "POST",
            f"https://slides.googleapis.com/v1/presentations/{pid}:batchUpdate",
            json_body={
                "requests": [
                    {
                        "replaceAllText": {
                            "containsText": {
                                "text": find,
                                "matchCase": bool(match_case),
                            },
                            "replaceText": replace_text if replace_text is not None else "",
                        }
                    }
                ]
            },
        )
        return _dumps(data)

    async def slides_insert_text(
        presentation_id: str,
        shape_object_id: str,
        text: str,
        insertion_index: int = 0,
    ) -> str:
        """Insert text into a shape (objectId from pageElements)."""
        pid = presentation_id.strip()
        oid = shape_object_id.strip()
        body = text if text is not None else ""
        if not pid or not oid:
            raise ValueError("presentation_id and shape_object_id are required")
        if not body:
            raise ValueError("text is required")
        data = await _google_request(
            "POST",
            f"https://slides.googleapis.com/v1/presentations/{pid}:batchUpdate",
            json_body={
                "requests": [
                    {
                        "insertText": {
                            "objectId": oid,
                            "insertionIndex": max(0, int(insertion_index or 0)),
                            "text": body,
                        }
                    }
                ]
            },
        )
        return _dumps(data)

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

    async def chat_send_message(space: str, text: str) -> str:
        """Send a text message to a Google Chat space (spaces/xxx)."""
        parent = space.strip()
        body = (text or "").strip()
        if not parent:
            raise ValueError("space is required (e.g. spaces/AAAA)")
        if not body:
            raise ValueError("text is required")
        if not parent.startswith("spaces/"):
            parent = f"spaces/{parent}"
        data = await _google_request(
            "POST",
            f"https://chat.googleapis.com/v1/{parent}/messages",
            json_body={"text": body},
        )
        return _dumps(data)

    async def chat_create_space(display_name: str) -> str:
        """Create a Google Chat space."""
        name = display_name.strip()
        if not name:
            raise ValueError("display_name is required")
        data = await _google_request(
            "POST",
            "https://chat.googleapis.com/v1/spaces",
            json_body={"spaceType": "SPACE", "displayName": name},
        )
        return _dumps(data)

    async def chat_delete_message(name: str) -> str:
        """Delete a Chat message (resource name spaces/xxx/messages/yyy)."""
        resource = name.strip()
        if not resource:
            raise ValueError("name is required (spaces/.../messages/...)")
        if not resource.startswith("spaces/"):
            raise ValueError("name must look like spaces/XXX/messages/YYY")
        await _google_request(
            "DELETE",
            f"https://chat.googleapis.com/v1/{resource}",
        )
        return _dumps({"deleted": True, "name": resource})

    specs: list[tuple[Any, str, str]] = [
        (gmail_list_messages, "gmail_list_messages", "List Gmail messages"),
        (gmail_get_message, "gmail_get_message", "Get a Gmail message"),
        (gmail_send_message, "gmail_send_message", "Send an email"),
        (gmail_create_draft, "gmail_create_draft", "Create a Gmail draft"),
        (gmail_reply_message, "gmail_reply_message", "Reply to a Gmail message"),
        (gmail_trash_message, "gmail_trash_message", "Trash a Gmail message"),
        (gmail_modify_labels, "gmail_modify_labels", "Add/remove Gmail labels"),
        (drive_list_files, "drive_list_files", "List Drive files"),
        (drive_get_file, "drive_get_file", "Get Drive file metadata"),
        (drive_create_folder, "drive_create_folder", "Create a Drive folder"),
        (drive_move_file, "drive_move_file", "Move a Drive file"),
        (drive_trash_file, "drive_trash_file", "Trash a Drive file"),
        (drive_copy_file, "drive_copy_file", "Copy a Drive file"),
        (drive_export_file, "drive_export_file", "Export or download a Drive file"),
        (calendar_list_calendars, "calendar_list_calendars", "List calendars"),
        (calendar_list_events, "calendar_list_events", "List calendar events"),
        (calendar_create_event, "calendar_create_event", "Create a calendar event"),
        (calendar_update_event, "calendar_update_event", "Update a calendar event"),
        (calendar_delete_event, "calendar_delete_event", "Delete a calendar event"),
        (contacts_list, "contacts_list", "List contacts"),
        (contacts_search, "contacts_search", "Search contacts"),
        (meet_list_upcoming, "meet_list_upcoming", "List upcoming Meet events"),
        (meet_create_event, "meet_create_event", "Create an event with Meet link"),
        (tasks_list_lists, "tasks_list_lists", "List task lists"),
        (tasks_list_tasks, "tasks_list_tasks", "List tasks"),
        (tasks_create_task, "tasks_create_task", "Create a task"),
        (tasks_complete_task, "tasks_complete_task", "Complete or reopen a task"),
        (tasks_delete_task, "tasks_delete_task", "Delete a task"),
        (docs_list_documents, "docs_list_documents", "List Google Docs"),
        (docs_get_document, "docs_get_document", "Get a Google Doc"),
        (docs_create_document, "docs_create_document", "Create a Google Doc"),
        (docs_append_text, "docs_append_text", "Append text to a Google Doc"),
        (docs_replace_text, "docs_replace_text", "Replace text in a Google Doc"),
        (docs_insert_text, "docs_insert_text", "Insert text in a Google Doc"),
        (sheets_list_spreadsheets, "sheets_list_spreadsheets", "List Google Sheets"),
        (sheets_list_sheets, "sheets_list_sheets", "List tabs in a spreadsheet"),
        (sheets_get_values, "sheets_get_values", "Read spreadsheet values"),
        (sheets_update_values, "sheets_update_values", "Write spreadsheet values"),
        (sheets_append_values, "sheets_append_values", "Append spreadsheet rows"),
        (sheets_clear_values, "sheets_clear_values", "Clear spreadsheet values"),
        (sheets_add_sheet, "sheets_add_sheet", "Add a sheet tab"),
        (sheets_delete_sheet, "sheets_delete_sheet", "Delete a sheet tab"),
        (sheets_rename_sheet, "sheets_rename_sheet", "Rename a sheet tab"),
        (sheets_delete_dimension, "sheets_delete_dimension", "Delete rows or columns"),
        (sheets_copy_sheet, "sheets_copy_sheet", "Duplicate or copy a sheet tab"),
        (sheets_create_spreadsheet, "sheets_create_spreadsheet", "Create a spreadsheet"),
        (slides_list_presentations, "slides_list_presentations", "List Google Slides"),
        (slides_get_presentation, "slides_get_presentation", "Get a Google Slides deck"),
        (
            slides_create_presentation,
            "slides_create_presentation",
            "Create a Google Slides deck",
        ),
        (slides_add_slide, "slides_add_slide", "Add a slide"),
        (slides_delete_slide, "slides_delete_slide", "Delete a slide"),
        (slides_replace_all_text, "slides_replace_all_text", "Replace text in slides"),
        (slides_insert_text, "slides_insert_text", "Insert text into a slide shape"),
        (chat_list_spaces, "chat_list_spaces", "List Google Chat spaces"),
        (chat_list_messages, "chat_list_messages", "List Google Chat messages"),
        (chat_send_message, "chat_send_message", "Send a Google Chat message"),
        (chat_create_space, "chat_create_space", "Create a Google Chat space"),
        (chat_delete_message, "chat_delete_message", "Delete a Google Chat message"),
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
