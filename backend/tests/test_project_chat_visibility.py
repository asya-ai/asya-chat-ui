"""Project chat visibility + collab access helpers."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from app.models.entities import AgentAccessRole, Chat, ChatMessage
from app.services.agent_access import (
    can_access_chat,
    can_edit_chat_message,
    can_manage_chat,
    user_display_label,
)


class _RoleSession:
    def __init__(self, *, role: AgentAccessRole | None) -> None:
        self._role = role

    def exec(self, _statement):
        return SimpleNamespace(first=lambda: self._agent() if self._role is not None else None)

    def _agent(self):
        return SimpleNamespace(id=uuid4(), org_id=uuid4(), org_access_role=self._role)


def test_can_manage_chat_owner_only() -> None:
    owner = SimpleNamespace(id=uuid4(), is_super_admin=False)
    other = SimpleNamespace(id=uuid4(), is_super_admin=False)
    chat = Chat(org_id=uuid4(), user_id=owner.id, agent_id=uuid4())
    assert can_manage_chat(chat, owner) is True
    assert can_manage_chat(chat, other) is False


def test_can_access_visible_project_chat(monkeypatch) -> None:
    owner_id = uuid4()
    member = SimpleNamespace(id=uuid4(), is_super_admin=False)
    agent_id = uuid4()
    org_id = uuid4()
    chat = Chat(
        org_id=org_id,
        user_id=owner_id,
        agent_id=agent_id,
        is_hidden_from_others=False,
    )
    session = _RoleSession(role=AgentAccessRole.viewer)

    monkeypatch.setattr(
        "app.services.agent_access.get_agent_role",
        lambda *_args, **_kwargs: AgentAccessRole.viewer,
    )
    assert can_access_chat(session, chat, member) is True


def test_can_access_blocks_hidden_project_chat(monkeypatch) -> None:
    owner_id = uuid4()
    member = SimpleNamespace(id=uuid4(), is_super_admin=False)
    chat = Chat(
        org_id=uuid4(),
        user_id=owner_id,
        agent_id=uuid4(),
        is_hidden_from_others=True,
    )
    session = _RoleSession(role=AgentAccessRole.editor)
    monkeypatch.setattr(
        "app.services.agent_access.get_agent_role",
        lambda *_args, **_kwargs: AgentAccessRole.editor,
    )
    assert can_access_chat(session, chat, member) is False
    owner = SimpleNamespace(id=owner_id, is_super_admin=False)
    assert can_access_chat(session, chat, owner) is True


def test_can_access_blocks_non_project_and_incognito(monkeypatch) -> None:
    owner_id = uuid4()
    member = SimpleNamespace(id=uuid4(), is_super_admin=False)
    session = _RoleSession(role=AgentAccessRole.viewer)
    monkeypatch.setattr(
        "app.services.agent_access.get_agent_role",
        lambda *_args, **_kwargs: AgentAccessRole.viewer,
    )
    personal = Chat(org_id=uuid4(), user_id=owner_id, agent_id=None)
    assert can_access_chat(session, personal, member) is False

    incognito = Chat(
        org_id=uuid4(),
        user_id=owner_id,
        agent_id=uuid4(),
        is_incognito=True,
    )
    assert can_access_chat(session, incognito, member) is False


def test_can_edit_chat_message_author_only() -> None:
    owner_id = uuid4()
    author_id = uuid4()
    chat = Chat(org_id=uuid4(), user_id=owner_id, agent_id=uuid4())
    message = ChatMessage(
        chat_id=chat.id,
        user_id=author_id,
        role="user",
        content="hi",
    )
    author = SimpleNamespace(id=author_id, is_super_admin=False)
    other = SimpleNamespace(id=uuid4(), is_super_admin=False)
    owner = SimpleNamespace(id=owner_id, is_super_admin=False)
    assert can_edit_chat_message(chat, message, author) is True
    assert can_edit_chat_message(chat, message, other) is False
    assert can_edit_chat_message(chat, message, owner) is False


def test_can_edit_legacy_message_falls_back_to_chat_owner() -> None:
    owner_id = uuid4()
    chat = Chat(org_id=uuid4(), user_id=owner_id, agent_id=uuid4())
    message = ChatMessage(chat_id=chat.id, user_id=None, role="user", content="old")
    owner = SimpleNamespace(id=owner_id, is_super_admin=False)
    other = SimpleNamespace(id=uuid4(), is_super_admin=False)
    assert can_edit_chat_message(chat, message, owner) is True
    assert can_edit_chat_message(chat, message, other) is False


def test_user_display_label() -> None:
    assert user_display_label(None) is None
    assert (
        user_display_label(
            SimpleNamespace(display_name="Ada", username="ada", email="a@b.c")
        )
        == "Ada"
    )
    assert (
        user_display_label(
            SimpleNamespace(display_name=None, username="ada", email="a@b.c")
        )
        == "ada"
    )


def test_chat_defaults_not_hidden() -> None:
    chat = Chat(org_id=uuid4(), user_id=uuid4())
    assert chat.is_hidden_from_others is False
