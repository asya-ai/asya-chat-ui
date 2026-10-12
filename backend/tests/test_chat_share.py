from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine

from app.api.chats import _chat_share_url, _shared_chat_or_404
from app.models.entities import Chat


def test_chat_share_url_uses_share_token():
    chat = Chat(org_id=uuid4(), user_id=uuid4(), share_token="tok_abc")
    assert _chat_share_url(chat) == "/shared/tok_abc"


def test_chat_share_url_none_when_unshared():
    chat = Chat(org_id=uuid4(), user_id=uuid4())
    assert _chat_share_url(chat) is None


def test_shared_chat_or_404_returns_shared_chat():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        chat = Chat(
            org_id=uuid4(),
            user_id=uuid4(),
            title="Shared",
            share_token="public_tok",
        )
        session.add(chat)
        session.commit()
        session.refresh(chat)

        found = _shared_chat_or_404(session, "public_tok")
        assert found.id == chat.id
        assert found.title == "Shared"


def test_shared_chat_or_404_rejects_missing_and_incognito():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            Chat(
                org_id=uuid4(),
                user_id=uuid4(),
                share_token="secret",
                is_incognito=True,
            )
        )
        session.commit()

        with pytest.raises(HTTPException) as missing:
            _shared_chat_or_404(session, "nope")
        assert missing.value.status_code == 404

        with pytest.raises(HTTPException) as incognito:
            _shared_chat_or_404(session, "secret")
        assert incognito.value.status_code == 404
