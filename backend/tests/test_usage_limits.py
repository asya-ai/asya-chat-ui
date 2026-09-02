from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services.usage_limits import (
    CHAT_USAGE_LIMIT_EXCEEDED,
    USAGE_LIMIT_WARNING_RATIO,
    build_usage_limit_info,
    enforce_chat_usage_limits,
    estimate_scoped_usage_cost_usd,
)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    def __init__(self, *, exec_results):
        self._exec_results = list(exec_results)
        self.calls = 0

    def exec(self, _statement):
        if self.calls >= len(self._exec_results):
            raise AssertionError("Unexpected session.exec call")
        result = self._exec_results[self.calls]
        self.calls += 1
        if isinstance(result, _FakeResult):
            return result
        return _FakeResult(result)


def test_estimate_scoped_usage_cost_usd_sums_priced_models(monkeypatch):
    model_id = uuid4()
    model = SimpleNamespace(
        id=model_id,
        provider="openai",
        model_name="gpt-4o",
        display_name="GPT-4o",
    )
    session = _FakeSession(
        exec_results=[
            [(model_id, 1_000_000, 500_000, 0, 0)],
            [model],
        ]
    )

    def _fake_estimate(provider, model_name, input_tokens, output_tokens, cached, thinking):
        assert provider == "openai"
        assert model_name == "gpt-4o"
        return (input_tokens + output_tokens) / 1_000_000

    monkeypatch.setattr(
        "app.services.usage_limits.estimate_token_cost_usd", _fake_estimate
    )

    total = estimate_scoped_usage_cost_usd(session, org_id=uuid4())
    assert total == pytest.approx(1.5)


def test_enforce_chat_usage_limits_blocks_org_ceiling(monkeypatch):
    org_id = uuid4()
    user_id = uuid4()
    org = SimpleNamespace(id=org_id, cost_ceiling_usd=10.0)
    session = _FakeSession(exec_results=[[org]])

    monkeypatch.setattr(
        "app.services.usage_limits.estimate_scoped_usage_cost_usd",
        lambda *_args, **_kwargs: 10.0,
    )

    with pytest.raises(HTTPException) as exc:
        enforce_chat_usage_limits(session, org_id=org_id, user_id=user_id)

    assert exc.value.status_code == 403
    assert exc.value.detail == CHAT_USAGE_LIMIT_EXCEEDED


def test_enforce_chat_usage_limits_blocks_user_ceiling(monkeypatch):
    org_id = uuid4()
    user_id = uuid4()
    org = SimpleNamespace(id=org_id, cost_ceiling_usd=None)
    user = SimpleNamespace(id=user_id, cost_ceiling_usd=5.0)
    session = _FakeSession(exec_results=[[org], [user]])

    monkeypatch.setattr(
        "app.services.usage_limits.estimate_scoped_usage_cost_usd",
        lambda *_args, **_kwargs: 5.0,
    )

    with pytest.raises(HTTPException) as exc:
        enforce_chat_usage_limits(session, org_id=org_id, user_id=user_id)

    assert exc.value.status_code == 403
    assert exc.value.detail == CHAT_USAGE_LIMIT_EXCEEDED


def test_enforce_chat_usage_limits_allows_under_ceiling(monkeypatch):
    org_id = uuid4()
    user_id = uuid4()
    org = SimpleNamespace(id=org_id, cost_ceiling_usd=20.0)
    user = SimpleNamespace(id=user_id, cost_ceiling_usd=10.0)
    session = _FakeSession(exec_results=[[org], [user]])

    monkeypatch.setattr(
        "app.services.usage_limits.estimate_scoped_usage_cost_usd",
        lambda *_args, **_kwargs: 1.0,
    )

    enforce_chat_usage_limits(session, org_id=org_id, user_id=user_id)


def test_build_usage_limit_info_without_ceiling():
    info = build_usage_limit_info(12.5, None)
    assert info.used_usd == 12.5
    assert info.limit_usd is None
    assert info.percent_used is None
    assert info.near_limit is False
    assert info.at_limit is False


def test_build_usage_limit_info_near_limit():
    info = build_usage_limit_info(9.0, 10.0)
    assert info.percent_used == pytest.approx(90.0)
    assert info.near_limit is True
    assert info.at_limit is False


def test_build_usage_limit_info_at_limit():
    info = build_usage_limit_info(10.0, 10.0)
    assert info.at_limit is True
    assert info.near_limit is True


def test_build_usage_limit_info_below_warning_threshold():
    info = build_usage_limit_info(8.9, 10.0)
    assert info.near_limit is False
    assert info.percent_used == pytest.approx(89.0)


def test_usage_limit_warning_ratio():
    assert USAGE_LIMIT_WARNING_RATIO == 0.9
