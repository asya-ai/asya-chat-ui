"""Live Google Drive / OneDrive project sources (cloud as source of truth)."""

from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
from uuid import UUID

import httpx
from sqlmodel import Session, select

from app.core.config import settings
from app.models import (
    AgentSource,
    AgentSourceKind,
    AgentSourceStatus,
    McpUserConnection,
)
from app.services.agents.source_parsing import extract_text_from_file
from app.services.integrations.materialize import find_catalog_mcp_server
from app.services.integrations.oauth import refresh_user_connection_tokens
from app.services.integrations.store import get_user_install
from app.services.mcp.auth_config import decrypt_auth_config

logger = logging.getLogger(__name__)

PROVIDER_GOOGLE = "google_drive"
PROVIDER_ONEDRIVE = "onedrive"

_MIME_FOLDER_GOOGLE = "application/vnd.google-apps.folder"
_MIME_DOCS = "application/vnd.google-apps.document"
_MIME_SHEETS = "application/vnd.google-apps.spreadsheet"
_MIME_SLIDES = "application/vnd.google-apps.presentation"
_MIME_DRAWING = "application/vnd.google-apps.drawing"

_GOOGLE_EXPORT_MIME = {
    _MIME_DOCS: "text/plain",
    _MIME_SHEETS: "text/csv",
    _MIME_SLIDES: "text/plain",
    _MIME_DRAWING: "image/png",
}

_GOOGLE_HOSTS = frozenset(
    {
        "drive.google.com",
        "docs.google.com",
        "sheets.google.com",
        "slides.google.com",
    }
)
_ONEDRIVE_HOST_SUFFIXES = (
    "onedrive.live.com",
    "sharepoint.com",
    "sharepointonline.com",
    "1drv.ms",
)


@dataclass(frozen=True)
class CloudRef:
    provider: str
    remote_id: str | None
    share_url: str | None = None
    hint_kind: str | None = None  # "file" | "folder" | None


@dataclass
class RemoteItem:
    remote_id: str
    name: str
    mime_type: str
    remote_kind: str  # file | folder
    etag: str | None
    modified_time: str | None
    web_view_link: str | None
    size: int | None = None
    drive_id: str | None = None


class CloudSourceError(Exception):
    """User-facing cloud source failure."""


def parse_cloud_url(url: str) -> CloudRef | None:
    """Detect Drive / OneDrive / Docs / SharePoint share links."""
    raw = (url or "").strip()
    if not raw:
        return None
    try:
        parsed = urlparse(raw)
    except Exception:
        return None
    if parsed.scheme not in {"http", "https"}:
        return None
    host = (parsed.hostname or "").lower().rstrip(".")
    path = unquote(parsed.path or "")

    if host in _GOOGLE_HOSTS or host.endswith(".google.com"):
        folder_match = re.search(r"/folders/([a-zA-Z0-9_-]+)", path)
        if folder_match:
            return CloudRef(
                provider=PROVIDER_GOOGLE,
                remote_id=folder_match.group(1),
                hint_kind="folder",
            )
        file_match = re.search(r"/file/d/([a-zA-Z0-9_-]+)", path)
        if file_match:
            return CloudRef(
                provider=PROVIDER_GOOGLE,
                remote_id=file_match.group(1),
                hint_kind="file",
            )
        doc_match = re.search(
            r"/(?:document|spreadsheets|presentation|drawings)/d/([a-zA-Z0-9_-]+)",
            path,
        )
        if doc_match:
            return CloudRef(
                provider=PROVIDER_GOOGLE,
                remote_id=doc_match.group(1),
                hint_kind="file",
            )
        qs = parse_qs(parsed.query or "")
        open_id = (qs.get("id") or [None])[0]
        if open_id:
            return CloudRef(
                provider=PROVIDER_GOOGLE,
                remote_id=str(open_id),
                hint_kind=None,
            )
        return None

    if host == "1drv.ms" or any(host == s or host.endswith("." + s) for s in _ONEDRIVE_HOST_SUFFIXES):
        hint: str | None = None
        if "/:f:/" in path or "/folders/" in path.lower():
            hint = "folder"
        elif any(marker in path for marker in ("/:w:/", "/:x:/", "/:b:/", "/:p:/", "/:u:/")):
            hint = "file"
        return CloudRef(
            provider=PROVIDER_ONEDRIVE,
            remote_id=None,
            share_url=raw,
            hint_kind=hint,
        )
    return None


def catalog_id_for_provider(provider: str) -> str:
    if provider == PROVIDER_GOOGLE:
        return "google_drive"
    if provider == PROVIDER_ONEDRIVE:
        return "onedrive"
    raise CloudSourceError(f"Unsupported cloud provider: {provider}")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_checked_at(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def cooldown_elapsed(meta: dict[str, Any], *, force: bool) -> bool:
    if force:
        return True
    checked = _parse_checked_at(meta.get("checked_at"))
    if checked is None:
        return True
    age = (datetime.now(timezone.utc) - checked).total_seconds()
    return age >= max(0, int(settings.cloud_source_check_cooldown_seconds or 0))


def resolve_cloud_access_token(
    session: Session, *, user_id: UUID, org_id: UUID, catalog_id: str
) -> str:
    install = get_user_install(
        session, user_id=user_id, org_id=org_id, catalog_id=catalog_id
    )
    if not install or install.status != "connected":
        label = "Google Drive" if catalog_id == "google_drive" else "OneDrive"
        raise CloudSourceError(f"Connect {label} in Integrations first")

    server = find_catalog_mcp_server(session, catalog_id)
    if server:
        connection = session.exec(
            select(McpUserConnection).where(
                McpUserConnection.user_id == user_id,
                McpUserConnection.server_id == server.id,
                McpUserConnection.status == "connected",
            )
        ).first()
        if connection and connection.auth_config:
            refreshed = refresh_user_connection_tokens(
                session, auth_config=connection.auth_config
            )
            if refreshed and refreshed != connection.auth_config:
                connection.auth_config = refreshed
                connection.updated_at = datetime.utcnow()
                session.add(connection)
                session.commit()
                session.refresh(connection)
            data = decrypt_auth_config(connection.auth_config)
            token = str(data.get("token") or "").strip()
            if token:
                return token

    creds = decrypt_auth_config(install.credentials)
    token = str(creds.get("access_token") or creds.get("token") or "").strip()
    if not token:
        raise CloudSourceError("Cloud connection has no access token. Reconnect.")
    return token


def _http_json(
    method: str,
    url: str,
    *,
    token: str,
    params: dict[str, Any] | None = None,
    timeout: float = 45.0,
) -> Any:
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        response = client.request(method, url, params=params, headers=headers)
    if response.status_code in {401, 403}:
        raise CloudSourceError("Cloud auth failed. Reconnect Google Drive / OneDrive.")
    if response.status_code >= 400:
        detail = (response.text or "")[:400]
        raise CloudSourceError(f"Cloud API {response.status_code}: {detail}")
    if not response.content:
        return {}
    try:
        return response.json()
    except Exception:
        return {"raw": response.text[:2000]}


def _http_bytes(
    method: str,
    url: str,
    *,
    token: str,
    params: dict[str, Any] | None = None,
    timeout: float = 90.0,
) -> tuple[bytes, str | None]:
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        response = client.request(method, url, params=params, headers=headers)
    if response.status_code in {401, 403}:
        raise CloudSourceError("Cloud auth failed. Reconnect Google Drive / OneDrive.")
    if response.status_code >= 400:
        detail = (response.text or "")[:400]
        raise CloudSourceError(f"Cloud API {response.status_code}: {detail}")
    ctype = response.headers.get("content-type")
    return response.content or b"", ctype


def _google_item_from_payload(data: dict[str, Any]) -> RemoteItem:
    mime = str(data.get("mimeType") or "")
    remote_kind = "folder" if mime == _MIME_FOLDER_GOOGLE else "file"
    return RemoteItem(
        remote_id=str(data.get("id") or ""),
        name=str(data.get("name") or "Untitled"),
        mime_type=mime,
        remote_kind=remote_kind,
        etag=str(data["md5Checksum"]) if data.get("md5Checksum") else (
            str(data["version"]) if data.get("version") else None
        ),
        modified_time=str(data["modifiedTime"]) if data.get("modifiedTime") else None,
        web_view_link=str(data["webViewLink"]) if data.get("webViewLink") else None,
        size=int(data["size"]) if str(data.get("size") or "").isdigit() else None,
    )


def google_get_metadata(token: str, file_id: str) -> RemoteItem:
    data = _http_json(
        "GET",
        f"https://www.googleapis.com/drive/v3/files/{file_id}",
        token=token,
        params={
            "fields": "id,name,mimeType,modifiedTime,md5Checksum,version,webViewLink,size",
            "supportsAllDrives": "true",
        },
    )
    item = _google_item_from_payload(data if isinstance(data, dict) else {})
    if not item.remote_id:
        raise CloudSourceError("Google Drive file not found")
    return item


def google_list_children(
    token: str,
    folder_id: str,
    *,
    max_files: int,
    max_depth: int,
    depth: int = 0,
) -> list[RemoteItem]:
    if depth > max_depth:
        return []
    out: list[RemoteItem] = []
    page_token: str | None = None
    query = f"'{folder_id}' in parents and trashed=false"
    while True:
        params: dict[str, Any] = {
            "q": query,
            "pageSize": 100,
            "fields": (
                "nextPageToken,files(id,name,mimeType,modifiedTime,md5Checksum,"
                "version,webViewLink,size)"
            ),
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        if page_token:
            params["pageToken"] = page_token
        data = _http_json(
            "GET",
            "https://www.googleapis.com/drive/v3/files",
            token=token,
            params=params,
        )
        files = data.get("files") if isinstance(data, dict) else None
        for raw in files or []:
            if not isinstance(raw, dict):
                continue
            item = _google_item_from_payload(raw)
            if not item.remote_id:
                continue
            if item.remote_kind == "folder":
                nested = google_list_children(
                    token,
                    item.remote_id,
                    max_files=max_files,
                    max_depth=max_depth,
                    depth=depth + 1,
                )
                out.extend(nested)
            else:
                out.append(item)
            if len(out) > max_files:
                raise CloudSourceError(
                    f"Folder has more than {max_files} files. Narrow the link."
                )
        page_token = data.get("nextPageToken") if isinstance(data, dict) else None
        if not page_token:
            break
    return out


def google_fetch_text(token: str, item: RemoteItem) -> str:
    mime = item.mime_type or ""
    if mime in _GOOGLE_EXPORT_MIME:
        export_mime = _GOOGLE_EXPORT_MIME[mime]
        raw, _ctype = _http_bytes(
            "GET",
            f"https://www.googleapis.com/drive/v3/files/{item.remote_id}/export",
            token=token,
            params={"mimeType": export_mime},
        )
        if export_mime.startswith("text/") or export_mime == "text/csv":
            return raw.decode("utf-8", errors="replace")
        return extract_text_from_file(
            file_name=item.name,
            content_type=export_mime,
            data=raw,
        )
    raw, ctype = _http_bytes(
        "GET",
        f"https://www.googleapis.com/drive/v3/files/{item.remote_id}",
        token=token,
        params={"alt": "media", "supportsAllDrives": "true"},
    )
    return extract_text_from_file(
        file_name=item.name,
        content_type=ctype or item.mime_type,
        data=raw,
    )


def _graph_share_id(share_url: str) -> str:
    encoded = base64.urlsafe_b64encode(share_url.encode("utf-8")).decode("ascii")
    encoded = encoded.rstrip("=")
    return f"u!{encoded}"


def _graph_item_from_payload(data: dict[str, Any]) -> RemoteItem:
    remote_id = str(data.get("id") or "")
    name = str(data.get("name") or "Untitled")
    folder = data.get("folder") is not None
    file_meta = data.get("file") if isinstance(data.get("file"), dict) else {}
    mime = str(file_meta.get("mimeType") or "").strip()
    if folder:
        mime = mime or "application/vnd.ms-folder"
    elif not mime:
        mime = "application/octet-stream"
    etag = str(data["eTag"]) if data.get("eTag") else (
        str(data["cTag"]) if data.get("cTag") else None
    )
    modified = data.get("lastModifiedDateTime")
    web = None
    if isinstance(data.get("webUrl"), str):
        web = data["webUrl"]
    size = data.get("size")
    parent = data.get("parentReference") if isinstance(data.get("parentReference"), dict) else {}
    drive_id = str(parent.get("driveId") or data.get("driveId") or "").strip() or None
    return RemoteItem(
        remote_id=remote_id,
        name=name,
        mime_type=mime or "application/octet-stream",
        remote_kind="folder" if folder else "file",
        etag=etag,
        modified_time=str(modified) if modified else None,
        web_view_link=web,
        size=int(size) if isinstance(size, int) else None,
        drive_id=drive_id,
    )


def _graph_item_url(item_id: str, drive_id: str | None = None, *, suffix: str = "") -> str:
    if drive_id:
        return f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item_id}{suffix}"
    return f"https://graph.microsoft.com/v1.0/me/drive/items/{item_id}{suffix}"


_GRAPH_SELECT = "id,name,size,eTag,cTag,lastModifiedDateTime,webUrl,file,folder,parentReference"


def onedrive_resolve_ref(token: str, ref: CloudRef) -> RemoteItem:
    if ref.remote_id:
        data = _http_json(
            "GET",
            _graph_item_url(ref.remote_id),
            token=token,
            params={"$select": _GRAPH_SELECT},
        )
    elif ref.share_url:
        share_id = _graph_share_id(ref.share_url)
        data = _http_json(
            "GET",
            f"https://graph.microsoft.com/v1.0/shares/{share_id}/driveItem",
            token=token,
            params={"$select": _GRAPH_SELECT},
        )
    else:
        raise CloudSourceError("OneDrive link could not be resolved")
    item = _graph_item_from_payload(data if isinstance(data, dict) else {})
    if not item.remote_id:
        raise CloudSourceError("OneDrive item not found")
    return item


def onedrive_get_metadata(
    token: str, item_id: str, *, drive_id: str | None = None
) -> RemoteItem:
    data = _http_json(
        "GET",
        _graph_item_url(item_id, drive_id),
        token=token,
        params={"$select": _GRAPH_SELECT},
    )
    item = _graph_item_from_payload(data if isinstance(data, dict) else {})
    if not item.remote_id:
        raise CloudSourceError("OneDrive item not found")
    if drive_id and not item.drive_id:
        item.drive_id = drive_id
    return item


def onedrive_list_children(
    token: str,
    folder_id: str,
    *,
    max_files: int,
    max_depth: int,
    depth: int = 0,
    drive_id: str | None = None,
) -> list[RemoteItem]:
    if depth > max_depth:
        return []
    out: list[RemoteItem] = []
    base = _graph_item_url(folder_id, drive_id, suffix="/children")
    url: str | None = f"{base}?$select={_GRAPH_SELECT}&$top=100"
    while url:
        data = _http_json("GET", url, token=token)
        values = data.get("value") if isinstance(data, dict) else None
        for raw in values or []:
            if not isinstance(raw, dict):
                continue
            item = _graph_item_from_payload(raw)
            if not item.remote_id:
                continue
            if not item.drive_id and drive_id:
                item.drive_id = drive_id
            if item.remote_kind == "folder":
                nested = onedrive_list_children(
                    token,
                    item.remote_id,
                    max_files=max_files,
                    max_depth=max_depth,
                    depth=depth + 1,
                    drive_id=item.drive_id or drive_id,
                )
                out.extend(nested)
            else:
                out.append(item)
            if len(out) > max_files:
                raise CloudSourceError(
                    f"Folder has more than {max_files} files. Narrow the link."
                )
        url = data.get("@odata.nextLink") if isinstance(data, dict) else None
    return out


def onedrive_fetch_text(token: str, item: RemoteItem) -> str:
    raw, ctype = _http_bytes(
        "GET",
        _graph_item_url(item.remote_id, item.drive_id, suffix="/content"),
        token=token,
    )
    return extract_text_from_file(
        file_name=item.name,
        content_type=ctype or item.mime_type,
        data=raw,
    )


def resolve_remote_item(token: str, ref: CloudRef) -> RemoteItem:
    if ref.provider == PROVIDER_GOOGLE:
        if not ref.remote_id:
            raise CloudSourceError("Google Drive file id missing")
        return google_get_metadata(token, ref.remote_id)
    if ref.provider == PROVIDER_ONEDRIVE:
        return onedrive_resolve_ref(token, ref)
    raise CloudSourceError(f"Unsupported provider: {ref.provider}")


def list_folder_files(
    token: str,
    provider: str,
    folder_id: str,
    *,
    drive_id: str | None = None,
) -> list[RemoteItem]:
    max_files = max(1, int(settings.cloud_source_max_folder_files or 200))
    max_depth = max(0, int(settings.cloud_source_max_folder_depth or 2))
    if provider == PROVIDER_GOOGLE:
        return google_list_children(
            token, folder_id, max_files=max_files, max_depth=max_depth
        )
    if provider == PROVIDER_ONEDRIVE:
        return onedrive_list_children(
            token,
            folder_id,
            max_files=max_files,
            max_depth=max_depth,
            drive_id=drive_id,
        )
    raise CloudSourceError(f"Unsupported provider: {provider}")


def fetch_remote_text(token: str, provider: str, item: RemoteItem) -> str:
    if provider == PROVIDER_GOOGLE:
        return google_fetch_text(token, item)
    if provider == PROVIDER_ONEDRIVE:
        return onedrive_fetch_text(token, item)
    raise CloudSourceError(f"Unsupported provider: {provider}")


def _fingerprint(item: RemoteItem) -> str:
    return item.etag or item.modified_time or ""


def base_cloud_metadata(
    *,
    provider: str,
    item: RemoteItem,
    connected_user_id: UUID,
    org_id: UUID,
    catalog_id: str,
) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "provider": provider,
        "remote_id": item.remote_id,
        "remote_kind": item.remote_kind,
        "etag": item.etag,
        "modified_time": item.modified_time,
        "mime_type": item.mime_type,
        "web_view_link": item.web_view_link,
        "connected_user_id": str(connected_user_id),
        "org_id": str(org_id),
        "catalog_id": catalog_id,
        "checked_at": _now_iso(),
    }
    if item.drive_id:
        meta["drive_id"] = item.drive_id
    return meta


def _token_for_source(session: Session, source: AgentSource) -> tuple[str, dict[str, Any]]:
    meta = dict(source.metadata_json or {})
    user_raw = meta.get("connected_user_id")
    org_raw = meta.get("org_id")
    catalog_id = str(meta.get("catalog_id") or catalog_id_for_provider(str(meta.get("provider") or "")))
    if not user_raw or not org_raw:
        raise CloudSourceError("Cloud source is missing connection metadata")
    token = resolve_cloud_access_token(
        session,
        user_id=UUID(str(user_raw)),
        org_id=UUID(str(org_raw)),
        catalog_id=catalog_id,
    )
    return token, meta


def create_cloud_source_tree(
    session: Session,
    *,
    agent_id: UUID,
    url: str,
    title: str | None,
    user_id: UUID,
    org_id: UUID,
) -> tuple[AgentSource, list[UUID]]:
    """Create cloud source (and folder children). Returns root + child ids to index."""
    ref = parse_cloud_url(url)
    if not ref:
        raise CloudSourceError("Not a Google Drive or OneDrive link")
    catalog_id = catalog_id_for_provider(ref.provider)
    token = resolve_cloud_access_token(
        session, user_id=user_id, org_id=org_id, catalog_id=catalog_id
    )
    item = resolve_remote_item(token, ref)
    now = datetime.utcnow()
    meta = base_cloud_metadata(
        provider=ref.provider,
        item=item,
        connected_user_id=user_id,
        org_id=org_id,
        catalog_id=catalog_id,
    )
    root = AgentSource(
        agent_id=agent_id,
        kind=AgentSourceKind.cloud,
        title=(title.strip() if title and title.strip() else item.name),
        url=item.web_view_link or url,
        file_name=item.name if item.remote_kind == "file" else None,
        content_type=item.mime_type if item.remote_kind == "file" else None,
        content_text="",
        status=AgentSourceStatus.queued,
        metadata_json=meta,
        created_at=now,
        updated_at=now,
    )
    session.add(root)
    session.flush()

    enqueue_ids: list[UUID] = []
    if item.remote_kind == "folder":
        children = list_folder_files(
            token, ref.provider, item.remote_id, drive_id=item.drive_id
        )
        for child_item in children:
            child_meta = base_cloud_metadata(
                provider=ref.provider,
                item=child_item,
                connected_user_id=user_id,
                org_id=org_id,
                catalog_id=catalog_id,
            )
            child = AgentSource(
                agent_id=agent_id,
                parent_source_id=root.id,
                kind=AgentSourceKind.cloud,
                title=child_item.name,
                url=child_item.web_view_link,
                file_name=child_item.name,
                content_type=child_item.mime_type,
                content_text="",
                status=AgentSourceStatus.queued,
                metadata_json=child_meta,
                created_at=now,
                updated_at=now,
            )
            session.add(child)
            session.flush()
            enqueue_ids.append(child.id)
        root.content_text = f"Cloud folder: {root.title}\nFiles ({len(children)})."
        root.status = AgentSourceStatus.ready
        root.updated_at = now
        session.add(root)
    else:
        enqueue_ids.append(root.id)
    session.flush()
    return root, enqueue_ids


def prepare_cloud_source_content(session: Session, source: AgentSource) -> list[UUID]:
    """Fetch remote text into content_text for a cloud file. Folders are containers."""
    meta = dict(source.metadata_json or {})
    if meta.get("remote_kind") == "folder":
        source.content_text = source.content_text or f"Cloud folder: {source.title}"
        source.status = AgentSourceStatus.ready
        meta["checked_at"] = _now_iso()
        source.metadata_json = meta
        session.add(source)
        session.flush()
        return []

    token, meta = _token_for_source(session, source)
    provider = str(meta.get("provider") or "")
    remote_id = str(meta.get("remote_id") or "")
    if not provider or not remote_id:
        raise CloudSourceError("Cloud source metadata incomplete")

    drive_id = str(meta.get("drive_id") or "").strip() or None
    if provider == PROVIDER_GOOGLE:
        item = google_get_metadata(token, remote_id)
    else:
        item = onedrive_get_metadata(token, remote_id, drive_id=drive_id)

    text = fetch_remote_text(token, provider, item)
    source.content_text = text
    source.title = source.title or item.name
    source.file_name = item.name
    source.content_type = item.mime_type
    source.url = item.web_view_link or source.url
    meta.update(
        {
            "etag": item.etag,
            "modified_time": item.modified_time,
            "mime_type": item.mime_type,
            "web_view_link": item.web_view_link,
            "checked_at": _now_iso(),
            "remote_kind": "file",
        }
    )
    if item.drive_id:
        meta["drive_id"] = item.drive_id
    source.metadata_json = meta
    session.add(source)
    session.flush()
    return []


def _mark_checked(session: Session, source: AgentSource, meta: dict[str, Any]) -> None:
    meta["checked_at"] = _now_iso()
    source.metadata_json = meta
    source.updated_at = datetime.utcnow()
    session.add(source)


def _enqueue_reindex(source_id: UUID) -> None:
    from app.workers.celery_app import celery_app

    celery_app.send_task("chatui.reindex_agent_source", args=[str(source_id)])


def _reconcile_folder_children(
    session: Session,
    folder: AgentSource,
    *,
    token: str,
    provider: str,
    remote_items: list[RemoteItem],
    meta: dict[str, Any],
) -> list[UUID]:
    """Add/remove/update children. Returns ids that need reindex."""
    existing = session.exec(
        select(AgentSource).where(AgentSource.parent_source_id == folder.id)
    ).all()
    by_remote: dict[str, AgentSource] = {}
    for child in existing:
        child_meta = child.metadata_json if isinstance(child.metadata_json, dict) else {}
        rid = str(child_meta.get("remote_id") or "")
        if rid:
            by_remote[rid] = child

    seen: set[str] = set()
    reindex: list[UUID] = []
    user_id = UUID(str(meta["connected_user_id"]))
    org_id = UUID(str(meta["org_id"]))
    catalog_id = str(meta.get("catalog_id") or catalog_id_for_provider(provider))
    now = datetime.utcnow()

    for item in remote_items:
        seen.add(item.remote_id)
        child = by_remote.get(item.remote_id)
        if child is None:
            child_meta = base_cloud_metadata(
                provider=provider,
                item=item,
                connected_user_id=user_id,
                org_id=org_id,
                catalog_id=catalog_id,
            )
            child = AgentSource(
                agent_id=folder.agent_id,
                parent_source_id=folder.id,
                kind=AgentSourceKind.cloud,
                title=item.name,
                url=item.web_view_link,
                file_name=item.name,
                content_type=item.mime_type,
                content_text="",
                status=AgentSourceStatus.queued,
                metadata_json=child_meta,
                created_at=now,
                updated_at=now,
            )
            session.add(child)
            session.flush()
            reindex.append(child.id)
            continue

        child_meta = dict(child.metadata_json or {})
        prev = child_meta.get("etag") or child_meta.get("modified_time") or ""
        cur = _fingerprint(item)
        child_meta.update(
            {
                "etag": item.etag,
                "modified_time": item.modified_time,
                "mime_type": item.mime_type,
                "web_view_link": item.web_view_link,
                "checked_at": _now_iso(),
            }
        )
        child.title = item.name
        child.file_name = item.name
        child.content_type = item.mime_type
        child.url = item.web_view_link or child.url
        child.metadata_json = child_meta
        child.updated_at = now
        session.add(child)
        if cur and cur != prev:
            child.status = AgentSourceStatus.queued
            child.error_message = None
            reindex.append(child.id)

    for rid, child in by_remote.items():
        if rid in seen:
            continue
        from app.services.agents.source_prepare import delete_source_tree

        delete_source_tree(session, child)

    folder.content_text = f"Cloud folder: {folder.title}\nFiles ({len(remote_items)})."
    return reindex


def maybe_sync_cloud_source(
    session: Session, source: AgentSource, *, force: bool = False
) -> list[UUID]:
    """Cheap on-access sync. Returns source ids queued for reindex."""
    if source.kind != AgentSourceKind.cloud:
        return []
    if source.parent_source_id is not None:
        # Children sync via folder parent (or direct file root sync).
        pass

    meta = dict(source.metadata_json or {})
    if not cooldown_elapsed(meta, force=force):
        return []

    try:
        token, meta = _token_for_source(session, source)
        provider = str(meta.get("provider") or "")
        remote_id = str(meta.get("remote_id") or "")
        remote_kind = str(meta.get("remote_kind") or "file")
        if not provider or not remote_id:
            raise CloudSourceError("Cloud source metadata incomplete")

        drive_id = str(meta.get("drive_id") or "").strip() or None
        if provider == PROVIDER_GOOGLE:
            item = google_get_metadata(token, remote_id)
        else:
            item = onedrive_get_metadata(token, remote_id, drive_id=drive_id)

        prev_fp = meta.get("etag") or meta.get("modified_time") or ""
        cur_fp = _fingerprint(item)
        meta.update(
            {
                "etag": item.etag,
                "modified_time": item.modified_time,
                "mime_type": item.mime_type,
                "web_view_link": item.web_view_link,
                "remote_kind": item.remote_kind,
            }
        )
        if item.drive_id:
            meta["drive_id"] = item.drive_id
        source.url = item.web_view_link or source.url
        if item.remote_kind == "file":
            source.file_name = item.name
            source.content_type = item.mime_type

        if item.remote_kind == "folder" or remote_kind == "folder":
            if cur_fp and cur_fp == prev_fp and not force:
                _mark_checked(session, source, meta)
                session.commit()
                return []
            # force or changed: list once and reconcile
            children = list_folder_files(
                token,
                provider,
                remote_id,
                drive_id=item.drive_id or drive_id,
            )
            reindex_ids = _reconcile_folder_children(
                session,
                source,
                token=token,
                provider=provider,
                remote_items=children,
                meta=meta,
            )
            _mark_checked(session, source, meta)
            source.status = AgentSourceStatus.ready
            source.error_message = None
            session.add(source)
            session.commit()
            for sid in reindex_ids:
                _enqueue_reindex(sid)
            return reindex_ids

        # File root — skip content fetch when fingerprint unchanged unless forced.
        if (
            not force
            and cur_fp
            and cur_fp == prev_fp
            and source.status == AgentSourceStatus.ready
        ):
            _mark_checked(session, source, meta)
            session.commit()
            return []

        meta["checked_at"] = _now_iso()
        source.metadata_json = meta
        source.status = AgentSourceStatus.queued
        source.error_message = None
        source.updated_at = datetime.utcnow()
        session.add(source)
        session.commit()
        _enqueue_reindex(source.id)
        return [source.id]
    except CloudSourceError as exc:
        meta = dict(source.metadata_json or {})
        meta["checked_at"] = _now_iso()
        source.metadata_json = meta
        source.status = AgentSourceStatus.failed
        source.error_message = str(exc)
        source.updated_at = datetime.utcnow()
        session.add(source)
        session.commit()
        return []
    except Exception as exc:
        logger.exception("Cloud sync failed source_id=%s", source.id)
        meta = dict(source.metadata_json or {})
        meta["checked_at"] = _now_iso()
        source.metadata_json = meta
        source.status = AgentSourceStatus.failed
        source.error_message = "Cloud sync failed"
        source.updated_at = datetime.utcnow()
        session.add(source)
        session.commit()
        logger.debug("cloud sync error detail: %s", exc)
        return []


def maybe_sync_agent_cloud_roots(
    session: Session, agent_id: UUID, *, force: bool = False
) -> None:
    """Sync top-level cloud sources for an agent (on list / access)."""
    roots = session.exec(
        select(AgentSource).where(
            AgentSource.agent_id == agent_id,
            AgentSource.kind == AgentSourceKind.cloud,
            AgentSource.parent_source_id == None,  # noqa: E711
        )
    ).all()
    for root in roots:
        maybe_sync_cloud_source(session, root, force=force)
