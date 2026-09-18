import logging

from app.core.config import settings
from app.services.providers.openai_provider import OpenAIProvider


def _openrouter_model_needs_cache_control(model: str) -> bool:
    """Anthropic Claude via OpenRouter requires explicit cache_control (not OpenAI auto-cache)."""
    author = model.split("/", 1)[0].strip().lower()
    return author == "anthropic"


def _anthropic_cache_control(retention: str | None) -> dict[str, str]:
    # OpenRouter Anthropic TTL is 5m (default) or 1h. Map any longer OpenAI retention to 1h.
    if retention and retention.lower() not in {"in-memory", "in_memory", "5m"}:
        return {"type": "ephemeral", "ttl": "1h"}
    return {"type": "ephemeral"}


class OpenRouterProvider(OpenAIProvider):
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        reasoning_effort: str | None = None,
        prompt_cache_key: str | None = None,
        prompt_cache_retention: str | None = None,
        prompt_cache_enabled: bool = True,
        prefer_responses_api: bool = False,
        extra_body: dict | None = None,
    ) -> None:
        super().__init__(
            api_key=api_key or settings.openrouter_api_key,
            base_url=base_url or "https://openrouter.ai/api/v1",
            reasoning_effort=reasoning_effort,
            prompt_cache_key=prompt_cache_key,
            prompt_cache_retention=prompt_cache_retention,
            prompt_cache_enabled=prompt_cache_enabled,
            prefer_responses_api=prefer_responses_api,
            extra_body=extra_body,
        )
        self._prompt_cache_enabled = prompt_cache_enabled
        # AsyncOpenAI.default_headers is read-only; merge via with_options.
        self.client = self.client.with_options(
            default_headers={
                "HTTP-Referer": "https://chatui.com",  # TODO: Make this configurable
                "X-Title": "ChatUI",
            }
        )
        self.logger = logging.getLogger(__name__)

    def _apply_prompt_cache(self, payload: dict) -> None:
        model = str(payload.get("model") or "")
        if not _openrouter_model_needs_cache_control(model):
            super()._apply_prompt_cache(payload)
            return

        if not self._prompt_cache_enabled:
            return

        # prompt_cache_key / session_id pin sticky routing on OpenRouter; do not send
        # OpenAI prompt_cache_retention (unsupported on Anthropic and would trigger a
        # strip-and-retry that also drops cache_control).
        extra: dict = {}
        if self.prompt_cache_key:
            payload["prompt_cache_key"] = self.prompt_cache_key
            # session_id activates sticky routing from the first request (not only
            # after a cache hit), which is required for reliable Anthropic cache hits.
            extra["session_id"] = self.prompt_cache_key

        extra["cache_control"] = _anthropic_cache_control(self.prompt_cache_retention)
        existing = payload.get("extra_body")
        if isinstance(existing, dict):
            payload["extra_body"] = {**existing, **extra}
        else:
            payload["extra_body"] = extra

    def _strip_prompt_cache(self, payload: dict) -> bool:
        removed = super()._strip_prompt_cache(payload)
        extra = payload.get("extra_body")
        if isinstance(extra, dict) and (
            "cache_control" in extra or "session_id" in extra
        ):
            next_extra = {
                key: value
                for key, value in extra.items()
                if key not in {"cache_control", "session_id"}
            }
            if next_extra:
                payload["extra_body"] = next_extra
            else:
                payload.pop("extra_body", None)
            removed = True
        return removed
