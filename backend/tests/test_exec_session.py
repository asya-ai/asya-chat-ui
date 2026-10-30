import pytest

from app.core.config import settings
from app.services.tools.exec_session import _holder_sleep_seconds, exec_session_key


def test_exec_session_key_prefers_generation_task() -> None:
    assert exec_session_key(
        generation_task_id="abc-123",
        exec_session_id="stream-1",
    ) == "task:abc-123"


def test_exec_session_key_stream_fallback() -> None:
    assert exec_session_key(
        generation_task_id=None,
        exec_session_id="stream-1",
    ) == "stream:stream-1"


def test_exec_session_key_requires_one_id() -> None:
    with pytest.raises(ValueError):
        exec_session_key(generation_task_id=None, exec_session_id=None)


def test_holder_sleep_is_bounded_not_infinite() -> None:
    sleep_secs = _holder_sleep_seconds(exec_timeout_seconds=120)
    assert 30 < sleep_secs <= settings.exec_session_container_seconds
