from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Agent, AgentVisibility, ChatModel, Org, UsageEvent, User
from app.services.agents.source_prepare import _describe_image_async
from app.services.providers.base import ChatResponse, ChatUsage


def test_describe_image_records_usage_event(monkeypatch) -> None:
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
            ChatModel.__table__,
            Agent.__table__,
            UsageEvent.__table__,
        ],
    )
    session = Session(engine)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    org = Org(name=f"Org-{uuid4().hex[:8]}")
    owner = User(
        email=f"owner-{uuid4().hex[:8]}@example.com",
        hashed_password="x",
        display_name="Owner",
    )
    session.add(org)
    session.add(owner)
    session.commit()
    session.refresh(org)
    session.refresh(owner)

    model = ChatModel(
        provider="openai",
        model_name="gpt-4o",
        display_name="GPT-4o",
        is_active=True,
        supports_image_input=True,
        created_at=now,
        updated_at=now,
    )
    session.add(model)
    session.commit()
    session.refresh(model)

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

    monkeypatch.setattr(
        "app.services.agents.source_prepare._pick_vision_model",
        lambda _session, _agent: model,
    )
    monkeypatch.setattr(
        "app.services.agents.source_prepare.require_provider_enabled",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )

    class _Provider:
        async def chat(self, _model_name: str, _messages: list) -> ChatResponse:
            return ChatResponse(
                content="Description: a red square\nExtracted text:\n(none)",
                usage=ChatUsage(
                    prompt_tokens=120,
                    completion_tokens=40,
                    total_tokens=160,
                    input_tokens=120,
                    output_tokens=40,
                    cached_tokens=0,
                    thinking_tokens=0,
                ),
            )

    monkeypatch.setattr(
        "app.services.agents.source_prepare.get_provider_for_org",
        lambda *_args, **_kwargs: _Provider(),
    )

    import asyncio

    text = asyncio.run(
        _describe_image_async(
            session,
            agent=agent,
            file_name="diagram.png",
            content_type="image/png",
            data=b"\x89PNG\r\n\x1a\n" + b"\x00" * 16,
        )
    )
    session.commit()

    assert "Description:" in text
    events = session.exec(select(UsageEvent)).all()
    assert len(events) == 1
    event = events[0]
    assert event.org_id == org.id
    assert event.user_id == owner.id
    assert event.model_id == model.id
    assert event.chat_id is None
    assert event.message_id is None
    assert event.prompt_tokens == 120
    assert event.completion_tokens == 40
    assert event.total_tokens == 160
    assert event.org_name_snapshot == org.name
    assert event.user_name_snapshot == "Owner"
