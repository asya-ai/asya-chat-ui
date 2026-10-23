from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import (
    Agent,
    AgentSource,
    AgentSourceKind,
    AgentSourceStatus,
    AgentVisibility,
    Org,
    User,
)
from app.services.agents.cloud_sources import (
    PROVIDER_GOOGLE,
    RemoteItem,
    cooldown_elapsed,
    create_cloud_source_tree,
    maybe_sync_cloud_source,
    parse_cloud_url,
    prepare_cloud_source_content,
)


def test_parse_google_file_and_folder_urls() -> None:
    file_ref = parse_cloud_url("https://drive.google.com/file/d/abc123xyz/view?usp=sharing")
    assert file_ref is not None
    assert file_ref.provider == PROVIDER_GOOGLE
    assert file_ref.remote_id == "abc123xyz"
    assert file_ref.hint_kind == "file"

    folder_ref = parse_cloud_url("https://drive.google.com/drive/folders/folder99")
    assert folder_ref is not None
    assert folder_ref.remote_id == "folder99"
    assert folder_ref.hint_kind == "folder"

    doc_ref = parse_cloud_url("https://docs.google.com/document/d/docid42/edit")
    assert doc_ref is not None
    assert doc_ref.remote_id == "docid42"


def test_parse_onedrive_share_url() -> None:
    ref = parse_cloud_url("https://1drv.ms/w/s!AbcDef")
    assert ref is not None
    assert ref.provider == "onedrive"
    assert ref.share_url is not None
    assert ref.remote_id is None


def test_parse_non_cloud_url() -> None:
    assert parse_cloud_url("https://example.com/article") is None


def test_cooldown_elapsed(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.agents.cloud_sources.settings.cloud_source_check_cooldown_seconds",
        1800,
    )
    assert cooldown_elapsed({}, force=False) is True
    assert cooldown_elapsed({}, force=True) is True

    recent = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    assert cooldown_elapsed({"checked_at": recent}, force=False) is False
    assert cooldown_elapsed({"checked_at": recent}, force=True) is True

    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    assert cooldown_elapsed({"checked_at": old}, force=False) is True


def _session_with_agent() -> tuple[Session, Agent, User, Org]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            Org.__table__,
            Agent.__table__,
            AgentSource.__table__,
        ],
    )
    session = Session(engine)
    org = Org(name=f"Org-{uuid4().hex[:8]}")
    owner = User(email=f"owner-{uuid4().hex[:8]}@example.com", hashed_password="x")
    session.add(org)
    session.add(owner)
    session.commit()
    session.refresh(org)
    session.refresh(owner)
    now = datetime.utcnow()
    agent = Agent(
        org_id=org.id,
        owner_user_id=owner.id,
        name="Project",
        master_prompt="",
        visibility=AgentVisibility.private,
        created_at=now,
        updated_at=now,
    )
    session.add(agent)
    session.commit()
    session.refresh(agent)
    return session, agent, owner, org


def test_create_cloud_file_source_no_file_path(monkeypatch) -> None:
    session, agent, owner, org = _session_with_agent()
    remote = RemoteItem(
        remote_id="fid1",
        name="notes.txt",
        mime_type="text/plain",
        remote_kind="file",
        etag="etag-1",
        modified_time="2024-01-01T00:00:00Z",
        web_view_link="https://drive.google.com/file/d/fid1/view",
    )

    monkeypatch.setattr(
        "app.services.agents.cloud_sources.resolve_cloud_access_token",
        lambda *a, **k: "token",
    )
    monkeypatch.setattr(
        "app.services.agents.cloud_sources.resolve_remote_item",
        lambda *a, **k: remote,
    )

    root, enqueue_ids = create_cloud_source_tree(
        session,
        agent_id=agent.id,
        url="https://drive.google.com/file/d/fid1/view",
        title=None,
        user_id=owner.id,
        org_id=org.id,
    )
    session.commit()

    assert root.kind == AgentSourceKind.cloud
    assert root.file_path is None
    assert root.title == "notes.txt"
    assert enqueue_ids == [root.id]
    meta = root.metadata_json or {}
    assert meta["provider"] == PROVIDER_GOOGLE
    assert meta["remote_id"] == "fid1"
    assert meta["connected_user_id"] == str(owner.id)


def test_create_cloud_folder_expands_children(monkeypatch) -> None:
    session, agent, owner, org = _session_with_agent()
    folder = RemoteItem(
        remote_id="folder1",
        name="Docs",
        mime_type="application/vnd.google-apps.folder",
        remote_kind="folder",
        etag="f-etag",
        modified_time="2024-01-01T00:00:00Z",
        web_view_link="https://drive.google.com/drive/folders/folder1",
    )
    child = RemoteItem(
        remote_id="child1",
        name="a.txt",
        mime_type="text/plain",
        remote_kind="file",
        etag="c-etag",
        modified_time="2024-01-02T00:00:00Z",
        web_view_link=None,
    )

    monkeypatch.setattr(
        "app.services.agents.cloud_sources.resolve_cloud_access_token",
        lambda *a, **k: "token",
    )
    monkeypatch.setattr(
        "app.services.agents.cloud_sources.resolve_remote_item",
        lambda *a, **k: folder,
    )
    monkeypatch.setattr(
        "app.services.agents.cloud_sources.list_folder_files",
        lambda *a, **k: [child],
    )

    root, enqueue_ids = create_cloud_source_tree(
        session,
        agent_id=agent.id,
        url="https://drive.google.com/drive/folders/folder1",
        title=None,
        user_id=owner.id,
        org_id=org.id,
    )
    session.commit()

    assert root.metadata_json["remote_kind"] == "folder"
    assert root.status == AgentSourceStatus.ready
    children = session.exec(
        select(AgentSource).where(AgentSource.parent_source_id == root.id)
    ).all()
    assert len(children) == 1
    assert children[0].title == "a.txt"
    assert enqueue_ids == [children[0].id]


def test_create_cloud_folder_over_cap_errors(monkeypatch) -> None:
    session, agent, owner, org = _session_with_agent()
    folder = RemoteItem(
        remote_id="folder1",
        name="Docs",
        mime_type="application/vnd.google-apps.folder",
        remote_kind="folder",
        etag="f-etag",
        modified_time="2024-01-01T00:00:00Z",
        web_view_link=None,
    )

    def _too_many(*_a, **_k):
        from app.services.agents.cloud_sources import CloudSourceError

        raise CloudSourceError("Folder has more than 200 files. Narrow the link.")

    monkeypatch.setattr(
        "app.services.agents.cloud_sources.resolve_cloud_access_token",
        lambda *a, **k: "token",
    )
    monkeypatch.setattr(
        "app.services.agents.cloud_sources.resolve_remote_item",
        lambda *a, **k: folder,
    )
    monkeypatch.setattr(
        "app.services.agents.cloud_sources.list_folder_files",
        _too_many,
    )

    from app.services.agents.cloud_sources import CloudSourceError

    try:
        create_cloud_source_tree(
            session,
            agent_id=agent.id,
            url="https://drive.google.com/drive/folders/folder1",
            title=None,
            user_id=owner.id,
            org_id=org.id,
        )
        raise AssertionError("expected CloudSourceError")
    except CloudSourceError as exc:
        assert "200" in str(exc)


def test_prepare_cloud_file_fetches_text(monkeypatch) -> None:
    session, agent, owner, org = _session_with_agent()
    now = datetime.utcnow()
    source = AgentSource(
        agent_id=agent.id,
        kind=AgentSourceKind.cloud,
        title="notes.txt",
        file_name="notes.txt",
        content_type="text/plain",
        content_text="",
        status=AgentSourceStatus.queued,
        metadata_json={
            "provider": PROVIDER_GOOGLE,
            "remote_id": "fid1",
            "remote_kind": "file",
            "etag": "old",
            "connected_user_id": str(owner.id),
            "org_id": str(org.id),
            "catalog_id": "google_drive",
        },
        created_at=now,
        updated_at=now,
    )
    session.add(source)
    session.commit()
    session.refresh(source)

    item = RemoteItem(
        remote_id="fid1",
        name="notes.txt",
        mime_type="text/plain",
        remote_kind="file",
        etag="new",
        modified_time="2024-02-01T00:00:00Z",
        web_view_link="https://drive.google.com/file/d/fid1/view",
    )
    monkeypatch.setattr(
        "app.services.agents.cloud_sources._token_for_source",
        lambda *_a, **_k: ("token", dict(source.metadata_json or {})),
    )
    monkeypatch.setattr(
        "app.services.agents.cloud_sources.google_get_metadata",
        lambda *_a, **_k: item,
    )
    monkeypatch.setattr(
        "app.services.agents.cloud_sources.fetch_remote_text",
        lambda *_a, **_k: "hello cloud",
    )

    prepare_cloud_source_content(session, source)
    session.commit()
    session.refresh(source)
    assert source.content_text == "hello cloud"
    assert source.file_path is None
    assert source.metadata_json["etag"] == "new"


def test_sync_skips_when_cooldown_active(monkeypatch) -> None:
    session, agent, owner, org = _session_with_agent()
    now = datetime.utcnow()
    checked = datetime.now(timezone.utc).isoformat()
    source = AgentSource(
        agent_id=agent.id,
        kind=AgentSourceKind.cloud,
        title="notes.txt",
        content_text="cached",
        status=AgentSourceStatus.ready,
        metadata_json={
            "provider": PROVIDER_GOOGLE,
            "remote_id": "fid1",
            "remote_kind": "file",
            "etag": "same",
            "checked_at": checked,
            "connected_user_id": str(owner.id),
            "org_id": str(org.id),
            "catalog_id": "google_drive",
        },
        created_at=now,
        updated_at=now,
    )
    session.add(source)
    session.commit()
    session.refresh(source)

    called = {"meta": False}

    def _fail_token(*_a, **_k):
        called["meta"] = True
        raise AssertionError("should not call remote during cooldown")

    monkeypatch.setattr(
        "app.services.agents.cloud_sources._token_for_source",
        _fail_token,
    )
    monkeypatch.setattr(
        "app.services.agents.cloud_sources.settings.cloud_source_check_cooldown_seconds",
        1800,
    )

    result = maybe_sync_cloud_source(session, source, force=False)
    assert result == []
    assert called["meta"] is False


def test_sync_file_unchanged_etag_skips_reindex(monkeypatch) -> None:
    session, agent, owner, org = _session_with_agent()
    now = datetime.utcnow()
    source = AgentSource(
        agent_id=agent.id,
        kind=AgentSourceKind.cloud,
        title="notes.txt",
        content_text="cached",
        status=AgentSourceStatus.ready,
        metadata_json={
            "provider": PROVIDER_GOOGLE,
            "remote_id": "fid1",
            "remote_kind": "file",
            "etag": "same",
            "modified_time": "2024-01-01T00:00:00Z",
            "connected_user_id": str(owner.id),
            "org_id": str(org.id),
            "catalog_id": "google_drive",
        },
        created_at=now,
        updated_at=now,
    )
    session.add(source)
    session.commit()
    session.refresh(source)

    item = RemoteItem(
        remote_id="fid1",
        name="notes.txt",
        mime_type="text/plain",
        remote_kind="file",
        etag="same",
        modified_time="2024-01-01T00:00:00Z",
        web_view_link=None,
    )
    monkeypatch.setattr(
        "app.services.agents.cloud_sources._token_for_source",
        lambda *_a, **_k: ("token", dict(source.metadata_json or {})),
    )
    monkeypatch.setattr(
        "app.services.agents.cloud_sources.google_get_metadata",
        lambda *_a, **_k: item,
    )
    enqueued: list = []
    monkeypatch.setattr(
        "app.services.agents.cloud_sources._enqueue_reindex",
        lambda sid: enqueued.append(sid),
    )

    result = maybe_sync_cloud_source(session, source, force=False)
    assert result == []
    assert enqueued == []
    session.refresh(source)
    assert source.metadata_json.get("checked_at")


def test_sync_file_changed_etag_enqueues(monkeypatch) -> None:
    session, agent, owner, org = _session_with_agent()
    now = datetime.utcnow()
    source = AgentSource(
        agent_id=agent.id,
        kind=AgentSourceKind.cloud,
        title="notes.txt",
        content_text="cached",
        status=AgentSourceStatus.ready,
        metadata_json={
            "provider": PROVIDER_GOOGLE,
            "remote_id": "fid1",
            "remote_kind": "file",
            "etag": "old",
            "connected_user_id": str(owner.id),
            "org_id": str(org.id),
            "catalog_id": "google_drive",
        },
        created_at=now,
        updated_at=now,
    )
    session.add(source)
    session.commit()
    session.refresh(source)

    item = RemoteItem(
        remote_id="fid1",
        name="notes.txt",
        mime_type="text/plain",
        remote_kind="file",
        etag="new",
        modified_time="2024-03-01T00:00:00Z",
        web_view_link=None,
    )
    monkeypatch.setattr(
        "app.services.agents.cloud_sources._token_for_source",
        lambda *_a, **_k: ("token", dict(source.metadata_json or {})),
    )
    monkeypatch.setattr(
        "app.services.agents.cloud_sources.google_get_metadata",
        lambda *_a, **_k: item,
    )
    enqueued: list = []
    monkeypatch.setattr(
        "app.services.agents.cloud_sources._enqueue_reindex",
        lambda sid: enqueued.append(sid),
    )

    result = maybe_sync_cloud_source(session, source, force=False)
    assert result == [source.id]
    assert enqueued == [source.id]
    session.refresh(source)
    assert source.status == AgentSourceStatus.queued


def test_sync_auth_failure_marks_failed(monkeypatch) -> None:
    session, agent, owner, org = _session_with_agent()
    now = datetime.utcnow()
    source = AgentSource(
        agent_id=agent.id,
        kind=AgentSourceKind.cloud,
        title="notes.txt",
        content_text="",
        status=AgentSourceStatus.ready,
        metadata_json={
            "provider": PROVIDER_GOOGLE,
            "remote_id": "fid1",
            "remote_kind": "file",
            "connected_user_id": str(owner.id),
            "org_id": str(org.id),
            "catalog_id": "google_drive",
        },
        created_at=now,
        updated_at=now,
    )
    session.add(source)
    session.commit()
    session.refresh(source)

    from app.services.agents.cloud_sources import CloudSourceError

    def _auth_fail(*_a, **_k):
        raise CloudSourceError("Connect Google Drive in Integrations first")

    monkeypatch.setattr(
        "app.services.agents.cloud_sources._token_for_source",
        _auth_fail,
    )

    result = maybe_sync_cloud_source(session, source, force=True)
    assert result == []
    session.refresh(source)
    assert source.status == AgentSourceStatus.failed
    assert "Google Drive" in (source.error_message or "")


def test_no_beat_schedule_for_cloud_sync() -> None:
    from app.workers.celery_app import celery_app

    assert "sync_cloud" not in str(celery_app.conf.beat_schedule)
    for key in celery_app.conf.beat_schedule:
        assert "cloud" not in key.lower()
