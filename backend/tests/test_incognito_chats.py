from uuid import uuid4

from app.api.chats import ChatCreateRequest
from app.models.entities import Chat
from app.services.providers.openai_provider import OpenAIProvider
from app.workers.tasks import _delete_expired_chat


class _Rows:
    def __init__(self, values: list[object]) -> None:
        self._values = values

    def all(self) -> list[object]:
        return self._values


class _DeleteSession:
    def __init__(self, message_id: object) -> None:
        self._message_id = message_id
        self._scalar_results = [[], [message_id], [], [message_id], []]
        self.statements: list[object] = []
        self.deleted: list[object] = []

    def scalars(self, _statement: object) -> _Rows:
        return _Rows(self._scalar_results.pop(0))

    def exec(self, statement: object) -> None:
        self.statements.append(statement)

    def delete(self, instance: object) -> None:
        self.deleted.append(instance)


def test_incognito_create_request_defaults_to_false() -> None:
    assert ChatCreateRequest(org_id=str(uuid4())).is_incognito is False


def test_incognito_create_request_accepts_true() -> None:
    assert ChatCreateRequest(org_id=str(uuid4()), is_incognito=True).is_incognito is True


def test_incognito_chat_defaults_to_false() -> None:
    chat = Chat(org_id=uuid4(), user_id=uuid4())

    assert chat.is_incognito is False


def test_incognito_deletion_detaches_usage_event_links() -> None:
    chat = Chat(org_id=uuid4(), user_id=uuid4(), is_incognito=True)
    message_id = uuid4()
    session = _DeleteSession(message_id)

    _delete_expired_chat(session, chat)

    statements = "\n".join(str(statement) for statement in session.statements)
    assert "UPDATE usage_events SET chat_id=:chat_id" in statements
    assert "UPDATE usage_events SET message_id=:message_id" in statements
    assert session.deleted == [chat]


def test_incognito_disables_openai_prompt_cache() -> None:
    provider = OpenAIProvider(api_key="test-key", prompt_cache_enabled=False)
    payload: dict[str, object] = {}

    provider._apply_prompt_cache(payload)

    assert payload == {}
