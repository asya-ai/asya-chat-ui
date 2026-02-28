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
    config: dict | None = None,
) -> ChatProvider:
    match provider:
        case "openai":
            return OpenAIProvider(
                api_key=api_key,
                base_url=base_url,
                reasoning_effort=reasoning_effort,
                prompt_cache_key=prompt_cache_key,
                prompt_cache_retention=prompt_cache_retention,
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
                api_key=api_key, prompt_cache_key=prompt_cache_key
            )
        case "vertex":
            return VertexProvider(
                api_key=api_key,
                prompt_cache_key=prompt_cache_key,
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
            return OpenRouterProvider(
                api_key=api_key,
                base_url=base_url,
                reasoning_effort=reasoning_effort,
                prompt_cache_key=prompt_cache_key,
                prompt_cache_retention=prompt_cache_retention,
            )
        case _:
            raise ValueError(f"Unsupported provider: {provider}")
