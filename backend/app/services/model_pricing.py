from dataclasses import dataclass
import json
import re
import time
import urllib.error
import urllib.request


@dataclass(frozen=True)
class ModelTokenPrice:
    input_per_million: float
    output_per_million: float
    cached_input_per_million: float | None = None


@dataclass(frozen=True)
class _PricingCache:
    fetched_at: float
    by_provider: dict[str, dict[str, ModelTokenPrice]]


_PRICING_URLS = (
    "https://www.aipricing.guru/api/pricing.json",
    "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json",
)
_OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
_PRICING_CACHE_TTL_SECONDS = 6 * 60 * 60
_pricing_cache: _PricingCache | None = None


def _parse_per_token_price(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _normalize_key(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"\s+", "-", value)
    value = value.replace("_", "-")
    return value


def _model_name_candidates(model_name: str) -> list[str]:
    normalized = _normalize_key(model_name)
    candidates = [normalized]
    if "/" in normalized:
        candidates.append(normalized.rsplit("/", 1)[-1])

    without_date_suffix = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", normalized)
    without_date_suffix = re.sub(r"-\d{8}$", "", without_date_suffix)
    without_date_suffix = re.sub(r"-\d{4}$", "", without_date_suffix)
    if without_date_suffix not in candidates:
        candidates.append(without_date_suffix)

    anthropic_decimal = re.sub(
        r"^claude-(\d)-(\d+)-(haiku|opus|sonnet)",
        r"claude-\1.\2-\3",
        without_date_suffix,
    )
    if anthropic_decimal not in candidates:
        candidates.append(anthropic_decimal)

    anthropic_family_decimal = re.sub(
        r"^claude-(haiku|opus|sonnet)-(\d)-(\d+)",
        r"claude-\1-\2.\3",
        without_date_suffix,
    )
    if anthropic_family_decimal not in candidates:
        candidates.append(anthropic_family_decimal)

    return candidates


def _provider_candidates(provider: str, model_name: str) -> list[str]:
    normalized_provider = _normalize_key(provider)
    candidates = [normalized_provider]
    if normalized_provider in {"azure", "azure-openai"}:
        candidates.append("openai")
    if normalized_provider in {"gemini", "vertex", "google-vertex"}:
        candidates.extend(["google", "gemini", "vertex-ai"])
    if normalized_provider == "openrouter" and "/" in model_name:
        routed_provider = _normalize_key(model_name.split("/", 1)[0])
        candidates.append("google" if routed_provider == "google" else routed_provider)
    return list(dict.fromkeys(candidates))


def _index_model_price(
    by_provider: dict[str, dict[str, ModelTokenPrice]],
    provider: str,
    key: str | None,
    price: ModelTokenPrice,
) -> None:
    if not key:
        return
    provider_prices = by_provider.setdefault(_normalize_key(provider), {})
    for candidate in _model_name_candidates(key):
        provider_prices.setdefault(candidate, price)


def _index_litellm_key(
    by_provider: dict[str, dict[str, ModelTokenPrice]],
    provider: str,
    key: str,
    price: ModelTokenPrice,
) -> None:
    _index_model_price(by_provider, provider, key, price)
    normalized_provider = _normalize_key(provider)
    normalized_key = _normalize_key(key)
    if normalized_key.startswith(f"{normalized_provider}/"):
        _index_model_price(
            by_provider,
            provider,
            normalized_key.removeprefix(f"{normalized_provider}/"),
            price,
        )


def _merge_api_pricing(
    by_provider: dict[str, dict[str, ModelTokenPrice]], payload: dict
) -> None:
    for item in payload.get("models", []):
        if not isinstance(item, dict):
            continue
        pricing = item.get("pricing")
        provider = item.get("provider")
        if not isinstance(pricing, dict) or not isinstance(provider, str):
            continue
        input_price = pricing.get("inputPerM")
        output_price = pricing.get("outputPerM")
        if not isinstance(input_price, (int, float)) or not isinstance(output_price, (int, float)):
            continue
        cached_price = pricing.get("cachedInputPerM")
        price = ModelTokenPrice(
            input_per_million=float(input_price),
            output_per_million=float(output_price),
            cached_input_per_million=float(cached_price)
            if isinstance(cached_price, (int, float))
            else None,
        )
        _index_model_price(by_provider, provider, item.get("id"), price)
        _index_model_price(by_provider, provider, item.get("name"), price)


def _merge_litellm_pricing(
    by_provider: dict[str, dict[str, ModelTokenPrice]], payload: dict
) -> None:
    for key, item in payload.items():
        if not isinstance(key, str) or not isinstance(item, dict):
            continue
        input_price = item.get("input_cost_per_token")
        output_price = item.get("output_cost_per_token")
        provider = item.get("litellm_provider")
        if not isinstance(input_price, (int, float)) or not isinstance(output_price, (int, float)):
            continue
        if not isinstance(provider, str):
            provider = key.split("/", 1)[0] if "/" in key else ""
        if not provider:
            continue
        cached_price = item.get("cache_read_input_token_cost")
        price = ModelTokenPrice(
            input_per_million=float(input_price) * 1_000_000,
            output_per_million=float(output_price) * 1_000_000,
            cached_input_per_million=float(cached_price) * 1_000_000
            if isinstance(cached_price, (int, float))
            else None,
        )
        _index_litellm_key(by_provider, provider, key, price)


def _merge_openrouter_pricing(
    by_provider: dict[str, dict[str, ModelTokenPrice]], payload: dict
) -> None:
    models = payload.get("data")
    if not isinstance(models, list):
        return
    for item in models:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id")
        pricing = item.get("pricing")
        if not isinstance(model_id, str) or not isinstance(pricing, dict):
            continue
        input_price = _parse_per_token_price(pricing.get("prompt"))
        output_price = _parse_per_token_price(pricing.get("completion"))
        if input_price is None or output_price is None:
            continue
        cached_price = _parse_per_token_price(pricing.get("input_cache_read"))
        price = ModelTokenPrice(
            input_per_million=input_price * 1_000_000,
            output_per_million=output_price * 1_000_000,
            cached_input_per_million=(
                cached_price * 1_000_000 if cached_price is not None else None
            ),
        )
        # Prefer full OpenRouter ids (author/slug) so lookups hit before routed-provider fallback.
        _index_model_price(by_provider, "openrouter", model_id, price)


def _fetch_json(url: str) -> dict | list | None:
    try:
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "chatui-usage-pricing"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if isinstance(payload, (dict, list)):
            return payload
    except (OSError, ValueError, urllib.error.URLError):
        return None
    return None


def _fetch_pricing_cache() -> _PricingCache | None:
    global _pricing_cache
    now = time.monotonic()
    if _pricing_cache and now - _pricing_cache.fetched_at < _PRICING_CACHE_TTL_SECONDS:
        return _pricing_cache
    by_provider: dict[str, dict[str, ModelTokenPrice]] = {}
    did_fetch = False

    # OpenRouter first: its /models pricing is authoritative for openrouter provider lookups.
    openrouter_payload = _fetch_json(_OPENROUTER_MODELS_URL)
    if isinstance(openrouter_payload, dict):
        _merge_openrouter_pricing(by_provider, openrouter_payload)
        did_fetch = True

    for url in _PRICING_URLS:
        payload = _fetch_json(url)
        if not isinstance(payload, dict):
            continue
        if isinstance(payload.get("models"), list):
            _merge_api_pricing(by_provider, payload)
        else:
            _merge_litellm_pricing(by_provider, payload)
        did_fetch = True
    if not did_fetch:
        return _pricing_cache
    _pricing_cache = _PricingCache(fetched_at=now, by_provider=by_provider)
    return _pricing_cache


def resolve_model_token_price(
    provider: str | None, model_name: str | None
) -> ModelTokenPrice | None:
    if not provider or not model_name:
        return None
    cache = _fetch_pricing_cache()
    if not cache:
        return None
    for provider_key in _provider_candidates(provider, model_name):
        prices = cache.by_provider.get(provider_key, {})
        for candidate in _model_name_candidates(model_name):
            price = prices.get(candidate)
            if price:
                return price
    return None


def estimate_token_cost_usd(
    provider: str | None,
    model_name: str | None,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int,
    thinking_tokens: int = 0,
) -> float | None:
    price = resolve_model_token_price(provider, model_name)
    if not price:
        return None

    cached_rate = price.cached_input_per_million or price.input_per_million
    return (
        (input_tokens * price.input_per_million)
        + (cached_tokens * cached_rate)
        + ((output_tokens + thinking_tokens) * price.output_per_million)
    ) / 1_000_000
