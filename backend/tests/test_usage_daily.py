from uuid import uuid4

from app.api.usage import (
    ModelUsageMeta,
    UsageDailyPoint,
    _aggregate_daily_points,
    _fill_daily_points,
)


def test_aggregate_daily_points_sums_models_and_costs(monkeypatch):
    first_model = uuid4()
    second_model = uuid4()
    model_map = {
        first_model: ModelUsageMeta(
            display_name="GPT", provider="openai", model_name="gpt-4o"
        )
    }

    monkeypatch.setattr("app.api.usage.estimate_token_cost_usd", lambda *args: 1.25)

    points = _aggregate_daily_points(
        [
            ("2026-08-01", first_model, 10, 5, 15, 10, 5, 0, 0),
            ("2026-08-01", second_model, 20, 10, 30, 20, 10, 0, 0),
            ("2026-08-02", first_model, 4, 1, 5, 4, 1, 0, 0),
        ],
        model_map,
    )

    assert [point.date for point in points] == ["2026-08-01", "2026-08-02"]
    assert points[0].input_tokens == 30
    assert points[0].output_tokens == 15
    assert points[0].total_tokens == 45
    assert points[0].cost_usd == 2.5
    assert points[1].total_tokens == 5
    assert points[1].cost_usd == 1.25


def test_aggregate_daily_points_marks_unknown_cost(monkeypatch):
    model_id = uuid4()
    model_map = {
        model_id: ModelUsageMeta(
            display_name="Mystery", provider="custom", model_name="secret"
        )
    }

    def _estimate(*args):
        return None

    monkeypatch.setattr("app.api.usage.estimate_token_cost_usd", _estimate)

    points = _aggregate_daily_points(
        [("2026-08-01", model_id, 10, 5, 15, 10, 5, 0, 0)],
        model_map,
    )
    assert points[0].cost_usd is None
    assert points[0].total_tokens == 15


def test_fill_daily_points_inserts_empty_days_for_month():
    filled = _fill_daily_points(
        [
            UsageDailyPoint(
                date="2026-02-03",
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                input_tokens=1,
                output_tokens=1,
                cached_tokens=0,
                thinking_tokens=0,
                cost_usd=0.4,
            )
        ],
        "2026-02",
    )
    assert len(filled) == 28
    assert filled[0].date == "2026-02-01"
    assert filled[0].total_tokens == 0
    assert filled[2].date == "2026-02-03"
    assert filled[2].total_tokens == 2
    assert filled[2].cost_usd == 0.4
    assert filled[-1].date == "2026-02-28"
