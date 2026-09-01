from uuid import uuid4

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import ChatModel, Org, OrgModel
from app.services.model_pricing import estimate_token_cost_usd
from app.services.tool_usage import (
    merge_tool_usage_fields,
    perplexity_usage_fields,
    resolve_service_model_id,
)


def _session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(
        engine,
        tables=[
            Org.__table__,
            ChatModel.__table__,
            OrgModel.__table__,
        ],
    )
    return Session(engine)


def test_resolve_service_model_id_returns_chat_model_uuid():
    with _session() as session:
        org = Org(name=f"Org-{uuid4().hex[:8]}")
        session.add(org)
        session.commit()
        session.refresh(org)

        model_id = resolve_service_model_id(
            session,
            org.id,
            "perplexity",
            "sonar-pro",
            display_name="Perplexity sonar-pro",
        )
        session.commit()

        model = session.exec(
            select(ChatModel).where(ChatModel.id == model_id)
        ).first()
        assert model is not None
        assert model.provider == "perplexity"
        assert model.model_name == "sonar-pro"

        org_link = session.exec(
            select(OrgModel).where(
                OrgModel.org_id == org.id,
                OrgModel.model_id == model_id,
            )
        ).first()
        assert org_link is not None


def test_perplexity_usage_fields_maps_prompt_and_completion_tokens():
    fields = perplexity_usage_fields(
        {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150}
    )
    assert fields["prompt_tokens"] == 120
    assert fields["completion_tokens"] == 30
    assert fields["total_tokens"] == 150
    assert fields["input_tokens"] == 120
    assert fields["output_tokens"] == 30


def test_merge_tool_usage_fields_accumulates_totals():
    base = perplexity_usage_fields({"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
    merged = merge_tool_usage_fields(base, perplexity_usage_fields({"prompt_tokens": 3, "completion_tokens": 2}))
    assert merged["prompt_tokens"] == 13
    assert merged["completion_tokens"] == 7
    assert merged["total_tokens"] == 20


def test_estimate_token_cost_usd_supports_perplexity_sonar_pro(monkeypatch):
    from app.services.model_pricing import ModelTokenPrice, _PricingCache

    cache = _PricingCache(
        fetched_at=0,
        by_provider={
            "perplexity": {
                "sonar-pro": ModelTokenPrice(
                    input_per_million=3.0,
                    output_per_million=15.0,
                )
            }
        },
    )
    monkeypatch.setattr("app.services.model_pricing._pricing_cache", cache)

    cost = estimate_token_cost_usd("perplexity", "sonar-pro", 1_000_000, 1_000_000, 0)
    assert cost == 18.0
