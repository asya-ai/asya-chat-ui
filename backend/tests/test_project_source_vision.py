from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import (
    Agent,
    AgentSource,
    AgentSourceKind,
    AgentSourceStatus,
    AgentVisibility,
    Org,
    User,
)
from app.services.file_storage import write_agent_source_file
from app.services.tools.agent_tools import AgentToolContext, read_project_source


def test_read_project_source_attaches_image_bytes(tmp_path, monkeypatch) -> None:
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
        tables=[User.__table__, Org.__table__, Agent.__table__, AgentSource.__table__],
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

    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    source = AgentSource(
        agent_id=agent.id,
        kind=AgentSourceKind.file,
        title="diagram.png",
        file_name="diagram.png",
        content_type="image/png",
        content_text="Description: a simple diagram\nExtracted text:\n(none)",
        status=AgentSourceStatus.ready,
        metadata_json={"is_image": True},
        created_at=now,
        updated_at=now,
    )
    session.add(source)
    session.flush()
    relative_path, _ = write_agent_source_file(
        agent_id=agent.id,
        source_id=source.id,
        file_name="diagram.png",
        data=png_bytes,
    )
    source.file_path = relative_path
    session.add(source)
    session.commit()
    session.refresh(source)

    result = __import__("asyncio").get_event_loop().run_until_complete(
        read_project_source(
            AgentToolContext(session=session, agent_id=agent.id, user_id=owner.id),
            source_id=1,
        )
    )
    assert result.output.get("is_image") is True
    assert result.output.get("vision_attached") is True
    assert result.attachments and len(result.attachments) == 1
    assert result.attachments[0]["content_type"] == "image/png"
    assert result.attachments[0]["data_base64"]


def test_vision_parts_helper() -> None:
    from app.services.langchain_runtime.agentic_loop import (
        _vision_parts_from_tool_attachments,
    )

    parts = _vision_parts_from_tool_attachments(
        [
            {
                "file_name": "a.png",
                "content_type": "image/png",
                "data_base64": "abc",
            },
            {
                "file_name": "b.pdf",
                "content_type": "application/pdf",
                "data_base64": "x",
            },
        ]
    )
    assert len(parts) == 2
    assert parts[0]["type"] == "text"
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"] == "data:image/png;base64,abc"
