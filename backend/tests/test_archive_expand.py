from __future__ import annotations

import io
import zipfile
from datetime import datetime
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
from app.services.agents.source_prepare import prepare_source_content
from app.services.file_storage import write_agent_source_file


def _make_zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def test_prepare_archive_creates_child_sources(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.file_storage.settings.files_base_dir", str(tmp_path)
    )

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

    zip_bytes = _make_zip(
        {
            "readme.txt": b"Hello archive",
            "nested/notes.md": b"# Notes",
            "__MACOSX/._junk": b"skip",
        }
    )
    source = AgentSource(
        agent_id=agent.id,
        kind=AgentSourceKind.file,
        title="bundle.zip",
        file_name="bundle.zip",
        content_type="application/zip",
        content_text="",
        status=AgentSourceStatus.queued,
        metadata_json={"is_archive": True},
        created_at=now,
        updated_at=now,
    )
    session.add(source)
    session.flush()
    relative_path, _ = write_agent_source_file(
        agent_id=agent.id,
        source_id=source.id,
        file_name="bundle.zip",
        data=zip_bytes,
    )
    source.file_path = relative_path
    session.add(source)
    session.commit()
    session.refresh(source)

    child_ids = prepare_source_content(session, source)
    session.commit()

    children = session.exec(
        select(AgentSource).where(AgentSource.parent_source_id == source.id)
    ).all()
    names = sorted(child.file_name or "" for child in children)
    assert names == ["nested/notes.md", "readme.txt"]
    assert len(child_ids) == 2
    session.refresh(source)
    assert "readme.txt" in (source.content_text or "")
    assert "nested/notes.md" in (source.content_text or "")
