"""Agent checkpoint serialize/load."""

from __future__ import annotations

from app.services.providers.base import ChatUsage
from app.workers.generation_checkpoint import (
    build_checkpoint_payload,
    load_agent_checkpoint,
    usage_from_dict,
    usage_to_dict,
)


def test_usage_roundtrip() -> None:
    usage = ChatUsage(
        prompt_tokens=1,
        completion_tokens=2,
        total_tokens=3,
        input_tokens=4,
        output_tokens=5,
        cached_tokens=6,
        thinking_tokens=7,
    )
    restored = usage_from_dict(usage_to_dict(usage))
    assert restored == usage


def test_load_checkpoint_requires_messages() -> None:
    assert load_agent_checkpoint({"agent_checkpoint": {"v": 1}}) is None
    payload = build_checkpoint_payload(
        next_step=3,
        messages=[{"role": "user", "content": "hi"}],
        attachments=[],
        scrape_sources=[],
        other_sources=[],
        image_usages=[],
        usage=None,
        consecutive_empty=0,
        consecutive_length=0,
    )
    loaded = load_agent_checkpoint({"agent_checkpoint": payload})
    assert loaded is not None
    assert loaded["next_step"] == 3


from uuid import uuid4

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.entities import (
    Chat,
    ChatGenerationTask,
    ChatMessage,
    ChatModel,
    GenerationStatus,
    Org,
    UsageEvent,
    User,
)
from app.workers.generation_checkpoint import (
    build_checkpoint_payload,
    persist_partial_generation_usage,
    usage_to_dict,
)


def _engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def test_persist_partial_usage_from_checkpoint() -> None:
    engine = _engine()
    SQLModel.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            Org.__table__,
            ChatModel.__table__,
            Chat.__table__,
            ChatMessage.__table__,
            ChatGenerationTask.__table__,
            UsageEvent.__table__,
        ],
    )
    with Session(engine) as session:
        org = Org(name=f"Org-{uuid4().hex[:8]}")
        user = User(email=f"u-{uuid4().hex[:8]}@example.com", hashed_password="x")
        model = ChatModel(provider="openai", model_name="gpt-test", display_name="GPT")
        session.add(org)
        session.add(user)
        session.add(model)
        session.commit()
        session.refresh(org)
        session.refresh(user)
        session.refresh(model)
        chat = Chat(org_id=org.id, user_id=user.id, model_id=model.id, title="t")
        session.add(chat)
        session.commit()
        session.refresh(chat)
        user_msg = ChatMessage(chat_id=chat.id, user_id=user.id, role="user", content="hi", status="done")
        assistant = ChatMessage(chat_id=chat.id, role="assistant", content="", status="generating")
        session.add(user_msg)
        session.add(assistant)
        session.commit()
        session.refresh(user_msg)
        session.refresh(assistant)
        usage = ChatUsage(10, 20, 30, 40, 50, 5, 6)
        checkpoint = build_checkpoint_payload(
            next_step=2,
            messages=[{"role": "user", "content": "hi"}],
            attachments=[],
            scrape_sources=[],
            other_sources=[],
            image_usages=[],
            usage=usage,
            consecutive_empty=0,
            consecutive_length=0,
        )
        task = ChatGenerationTask(
            chat_id=chat.id,
            user_message_id=user_msg.id,
            assistant_message_id=assistant.id,
            status=GenerationStatus.streaming,
            metadata_json={"agent_checkpoint": checkpoint},
        )
        session.add(task)
        session.commit()
        session.refresh(task)

        persist_partial_generation_usage(
            session,
            task_id=task.id,
            org_id=org.id,
            user_id=user.id,
            chat_id=chat.id,
            message_id=assistant.id,
            model_id=model.id,
            runtime_usage=None,
            image_usages=None,
            metadata_json=task.metadata_json,
        )

        rows = session.exec(
            select(UsageEvent).where(UsageEvent.message_id == assistant.id)
        ).all()
        assert len(rows) == 1
        assert rows[0].input_tokens == 40
        assert rows[0].thinking_tokens == 6

        persist_partial_generation_usage(
            session,
            task_id=task.id,
            org_id=org.id,
            user_id=user.id,
            chat_id=chat.id,
            message_id=assistant.id,
            model_id=model.id,
            runtime_usage=None,
            image_usages=None,
            metadata_json=task.metadata_json,
        )
        rows = session.exec(
            select(UsageEvent).where(UsageEvent.message_id == assistant.id)
        ).all()
        assert len(rows) == 1
