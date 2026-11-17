from unittest.mock import MagicMock, patch

import pytest

from app.services.providers.vertex_config import (
    is_vertex_claude_model,
    normalize_vertex_model_name,
    resolve_vertex_runtime_config,
)


def test_normalize_vertex_model_name_strips_anthropic_publisher() -> None:
    assert (
        normalize_vertex_model_name(
            "publishers/anthropic/models/claude-haiku-5-5"
        )
        == "claude-haiku-5-5"
    )


def test_is_vertex_claude_model() -> None:
    assert is_vertex_claude_model("claude-haiku-5-5")
    assert not is_vertex_claude_model("gemini-2.5-flash")


def test_resolve_vertex_runtime_config_honors_location() -> None:
    runtime = resolve_vertex_runtime_config(
        {"project": "demo", "location": "eu", "type": "service_account", "private_key": "x", "client_email": "y"}
    )
    assert runtime.project == "demo"
    assert runtime.location == "eu"


@pytest.mark.anyio
async def test_vertex_provider_routes_claude_to_anthropic() -> None:
    from app.services.providers.vertex_provider import VertexProvider

    provider = VertexProvider(
        config={"project": "demo", "location": "eu"},
    )
    mock_anthropic = MagicMock()

    async def fake_chat(*_args, **_kwargs):
        from app.services.providers.base import ChatResponse, ChatUsage

        return ChatResponse(
            content="ok",
            usage=ChatUsage(
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                input_tokens=1,
                output_tokens=1,
                cached_tokens=0,
                thinking_tokens=0,
            ),
        )

    mock_anthropic.chat = fake_chat
    provider._anthropic_chat = mock_anthropic

    result = await provider.chat("claude-haiku-5-5", [{"role": "user", "content": "hi"}])
    assert result.content == "ok"


def test_validate_vertex_claude_invokable_uses_anthropic_ping() -> None:
    from app.api import models as models_api
    from sqlmodel import Session

    with patch(
        "app.services.providers.vertex_claude.ping_vertex_claude_model",
        return_value=None,
    ) as ping_mock:
        with patch(
            "app.services.instance_providers.resolve_effective_credentials",
            return_value=MagicMock(config={"project": "demo", "location": "eu"}),
        ):
            with patch(
                "app.api.models.require_provider_enabled",
                return_value=MagicMock(),
            ):
                models_api._validate_vertex_model_invokable(
                    Session,
                    "00000000-0000-0000-0000-000000000001",
                    "claude-haiku-5-5",
                )
    ping_mock.assert_called_once()