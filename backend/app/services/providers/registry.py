from app.services.providers.base import ChatProvider
from app.services.providers.anthropic_provider import AnthropicProvider
from app.services.providers.gemini_provider import GeminiProvider
from app.services.providers.groq_provider import GroqProvider
from app.services.providers.openai_provider import AzureOpenAIProvider, OpenAIProvider
from app.services.providers.openrouter_provider import OpenRouterProvider
from app.services.providers.vertex_provider import VertexProvider


def get_provider(
    provider: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    endpoint: str | None = None,
    reasoning_effort: str | None = None,
    prompt_cache_key: str | None = None,
    prompt_cache_retention: str | None = None,
    prompt_cache_enabled: bool = True,
    prefer_responses_api: bool = False,
    config: dict | None = None,
    extra_body: dict | None = None,
    openrouter_endpoint: str | None = None,
) -> ChatProvider:
    match provider:
        case "openai":
            return OpenAIProvider(
                api_key=api_key,
                base_url=base_url,
                reasoning_effort=reasoning_effort,
                prompt_cache_key=prompt_cache_key,
                prompt_cache_retention=prompt_cache_retention,
                prompt_cache_enabled=prompt_cache_enabled,
                prefer_responses_api=prefer_responses_api,
            )
        case "azure":
            return AzureOpenAIProvider(
                api_key=api_key,
                endpoint=endpoint,
                reasoning_effort=reasoning_effort,
                prompt_cache_key=prompt_cache_key,
                prompt_cache_retention=prompt_cache_retention,
            )
        case "gemini":
            return GeminiProvider(
                api_key=api_key,
                prompt_cache_key=prompt_cache_key,
                prompt_cache_enabled=prompt_cache_enabled,
            )
        case "vertex":
            return VertexProvider(
                api_key=api_key,
                prompt_cache_key=prompt_cache_key,
                prompt_cache_enabled=prompt_cache_enabled,
                config=config,
            )
        case "groq":
            return GroqProvider(
                api_key=api_key,
                base_url=base_url,
                prompt_cache_key=prompt_cache_key,
                prompt_cache_retention=prompt_cache_retention,
            )
        case "anthropic":
            return AnthropicProvider(
                api_key=api_key,
                base_url=base_url,
            )
        case "openrouter":
            body = extra_body
            tag = (openrouter_endpoint or "").strip()
            if tag:
                routed = {
                    "provider": {
                        "only": [tag],
                        "allow_fallbacks": False,
                    }
                }
                body = {**routed, **(body or {})}
            return OpenRouterProvider(
                api_key=api_key,
                base_url=base_url,
                reasoning_effort=reasoning_effort,
                prompt_cache_key=prompt_cache_key,
                prompt_cache_retention=prompt_cache_retention,
                prefer_responses_api=prefer_responses_api,
                extra_body=body,
            )
        case _:
            raise ValueError(f"Unsupported provider: {provider}")
