import logging
from google import genai
from app.core.config import settings
from app.services.providers.gemini_provider import GeminiProvider

class VertexProvider(GeminiProvider):
    def __init__(
        self,
        *,
        api_key: str | None = None,
        prompt_cache_key: str | None = None,
        config: dict | None = None,
    ) -> None:
        self.logger = logging.getLogger(__name__)
        self.prompt_cache_key = prompt_cache_key
        
        # Determine configuration
        project = (config or {}).get("project") or settings.google_vertex_project
        location = (config or {}).get("location") or settings.google_vertex_location
        
        # If project/location are provided, use vertexai mode
        if project and location:
            self.logger.info("Initializing Vertex AI client for project %s, location %s", project, location)
            self.client = genai.Client(
                vertexai=True,
                project=project,
                location=location,
                # api_key is typically not used for Vertex AI (uses ADC), but if passed we could use it?
                # The SDK docs say api_key is for AI Studio. For Vertex, credentials are handled by ADC or explicit credentials object.
                # If we want to support explicit credentials from config (e.g. SA JSON), we need to parse it.
                # For now, we rely on ADC if running in container with GOOGLE_APPLICATION_CREDENTIALS.
            )
        else:
            # Fallback or error?
            # If no project/location, maybe they wanted AI Studio? But this is "vertex" provider.
            # We'll default to env vars if not in config.
            # If env vars missing, we can try init without them (maybe ADC has default project?)
            self.logger.warning("Vertex AI project/location not configured, falling back to default client init (might fail)")
            self.client = genai.Client(vertexai=True)
