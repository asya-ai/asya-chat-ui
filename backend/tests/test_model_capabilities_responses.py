from __future__ import annotations

from app.models import ChatModel
from app.services.model_capabilities import (
    infer_uses_responses_api,
    prefer_responses_api_for_model,
)


def test_infer_uses_responses_api_for_sol_and_luna() -> None:
    assert infer_uses_responses_api("azure", "gpt-6-sol") is True
    assert infer_uses_responses_api("openai", "gpt-6-luna") is True
    assert infer_uses_responses_api("azure", "gpt-5.2-codex") is None
    assert infer_uses_responses_api("anthropic", "gpt-6-sol") is None


def test_prefer_responses_api_infers_when_unset() -> None:
    model = ChatModel(
        provider="azure",
        model_name="gpt-6-sol",
        display_name="Sol",
        uses_responses_api=None,
    )
    assert prefer_responses_api_for_model(model) is True

    model.uses_responses_api = False
    assert prefer_responses_api_for_model(model) is False
