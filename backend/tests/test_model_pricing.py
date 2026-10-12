from uuid import uuid4

import pytest

from app.api.models import _model_read
from app.models import ChatModel
from app.services.model_pricing import (
    ModelTokenPrice,
    _PricingCache,
    classify_model_cost_tier,
    cost_tier_from_price,
)


def test_cost_tier_from_price_buckets():
    # Workload: 1M input + 100k output against current Auto thresholds.
    assert (
        cost_tier_from_price(ModelTokenPrice(input_per_million=0.1, output_per_million=0.4))
        == 1
    )
    assert (
        cost_tier_from_price(ModelTokenPrice(input_per_million=1.0, output_per_million=5.0))
        == 2
    )
    assert (
        cost_tier_from_price(ModelTokenPrice(input_per_million=12.0, output_per_million=50.0))
        == 3
    )
    assert (
        cost_tier_from_price(ModelTokenPrice(input_per_million=15.0, output_per_million=75.0))
        == 4
    )


def test_cost_tier_from_image_price():
    assert (
        cost_tier_from_price(
            ModelTokenPrice(input_per_million=0, output_per_million=0, cost_per_image=0.01)
        )
        == 1
    )
    assert (
        cost_tier_from_price(
            ModelTokenPrice(input_per_million=0, output_per_million=0, cost_per_image=0.04)
        )
        == 2
    )


def test_merge_litellm_indexes_image_models():
    from app.services.model_pricing import _merge_litellm_pricing

    by_provider: dict = {}
    _merge_litellm_pricing(
        by_provider,
        {
            "gpt-image-2": {
                "litellm_provider": "openai",
                "mode": "image_generation",
                "input_cost_per_token": 5e-6,
                "output_cost_per_image_token": 3e-5,
            },
            "dall-e-3": {
                "litellm_provider": "openai",
                "mode": "image_generation",
                "output_cost_per_image": 0.04,
            },
        },
    )
    image = by_provider["openai"]["gpt-image-2"]
    assert image.cost_per_image == pytest.approx(3e-5 * 1050)
    assert by_provider["openai"]["dall-e-3"].cost_per_image == 0.04


def test_classify_model_cost_tier_uses_cache(monkeypatch):
    cache = _PricingCache(
        fetched_at=0,
        by_provider={
            "openai": {
                "gpt-4o-mini": ModelTokenPrice(
                    input_per_million=0.15,
                    output_per_million=0.6,
                )
            }
        },
    )
    monkeypatch.setattr("app.services.model_pricing._pricing_cache", cache)

    assert classify_model_cost_tier("openai", "gpt-4o-mini") == 1
    assert classify_model_cost_tier("openai", "missing") is None


def test_model_read_exposes_prices_and_honors_override(monkeypatch):
    cache = _PricingCache(
        fetched_at=0,
        by_provider={
            "openai": {
                "gpt-4o-mini": ModelTokenPrice(
                    input_per_million=0.15,
                    output_per_million=0.6,
                    cached_input_per_million=0.075,
                )
            }
        },
    )
    monkeypatch.setattr("app.services.model_pricing._pricing_cache", cache)

    model = ChatModel(
        id=uuid4(),
        provider="openai",
        model_name="gpt-4o-mini",
        display_name="OpenAI/gpt-4o-mini",
        cost_tier_override=None,
    )
    auto = _model_read(model)
    assert auto.input_cost_per_million == 0.15
    assert auto.cached_input_cost_per_million == 0.075
    assert auto.output_cost_per_million == 0.6
    assert auto.auto_cost_tier == 1
    assert auto.cost_tier_override is None
    assert auto.cost_tier == 1

    model.cost_tier_override = 4
    overridden = _model_read(model)
    assert overridden.auto_cost_tier == 1
    assert overridden.cost_tier_override == 4
    assert overridden.cost_tier == 4
