import logging
from app.services.providers.openai_provider import OpenAIProvider
from app.core.config import settings

class OpenRouterProvider(OpenAIProvider):
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        reasoning_effort: str | None = None,
        prompt_cache_key: str | None = None,
        prompt_cache_retention: str | None = None,
    ) -> None:
        super().__init__(
            api_key=api_key or settings.openrouter_api_key,
            base_url=base_url or "https://openrouter.ai/api/v1",
            reasoning_effort=reasoning_effort,
            prompt_cache_key=prompt_cache_key,
            prompt_cache_retention=prompt_cache_retention,
        )
        self.client.default_headers = {
            "HTTP-Referer": "https://chatui.com", # TODO: Make this configurable
            "X-Title": "ChatUI",
        }
        self.logger = logging.getLogger(__name__)
