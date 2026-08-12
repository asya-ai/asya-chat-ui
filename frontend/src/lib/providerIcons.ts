const PROVIDER_ICON_URLS: Record<string, string> = {
  openai: "/icon-provider-openai.svg",
  azure: "/icon-provider-azure.svg",
  gemini: "/icon-provider-gemini.svg",
  groq: "/icon-provider-groq.svg",
  anthropic: "/icon-provider-anthropic.svg",
  openrouter: "/icon-provider-openrouter.svg",
  vertex: "/icon-provider-vertex.svg",
  moonshot: "/icon-provider-moonshot.svg",
  moonshotai: "/icon-provider-moonshot.svg",
  google: "/icon-provider-gemini.svg",
}

/** OpenRouter author/org slug → models.dev logo id when they differ. */
const OPENROUTER_AUTHOR_ALIASES: Record<string, string> = {
  "meta-llama": "meta",
  "google-ai-studio": "google",
  "x-ai": "xai",
  amazon: "amazon-bedrock",
  microsoft: "azure",
  moonshot: "moonshotai",
}

const MODELS_DEV_LOGO_BASE = "https://models.dev/logos"

/** Providers whose models.dev slug matches our local mark well enough to try remotely first. */
const MODELS_DEV_PROVIDER_SLUGS: Record<string, string> = {
  openai: "openai",
  anthropic: "anthropic",
  groq: "groq",
  azure: "azure",
  // models.dev/gemini is wrong (mistral mark); use google sparkle instead.
  gemini: "google",
  google: "google",
  moonshot: "moonshotai",
  moonshotai: "moonshotai",
  // vertex: local Vertex AI mark only — models.dev aliases collide with Gemini.
}

function modelsDevLogoUrl(slug: string): string {
  return `${MODELS_DEV_LOGO_BASE}/${encodeURIComponent(slug)}.svg`
}

function openRouterAuthorSlug(modelName: string): string | null {
  const author = modelName.split("/", 1)[0]?.trim().toLowerCase()
  if (!author) return null
  return OPENROUTER_AUTHOR_ALIASES[author] ?? author
}

/**
 * Preferred icon URL for a chat model provider.
 * OpenRouter models use the upstream author logo from models.dev when possible;
 * otherwise falls back to our local provider marks (OpenRouter official glyph, etc.).
 */
export function getProviderIconUrl(
  provider: string | null | undefined,
  modelName?: string | null
): string | null {
  const candidates = getProviderIconCandidates(provider, modelName)
  return candidates[0] ?? null
}

/** Ordered icon URLs: try remote author/provider logos first, then local fallbacks. */
export function getProviderIconCandidates(
  provider: string | null | undefined,
  modelName?: string | null
): string[] {
  const key = (provider ?? "").trim().toLowerCase()
  if (!key) return []

  const local = PROVIDER_ICON_URLS[key]
  const urls: string[] = []

  if (key === "openrouter") {
    if (modelName) {
      const authorSlug = openRouterAuthorSlug(modelName)
      if (authorSlug && authorSlug !== "openrouter") {
        urls.push(modelsDevLogoUrl(authorSlug))
      }
    }
    // Prefer our official OpenRouter glyph over models.dev (broken viewBox).
    urls.push(PROVIDER_ICON_URLS.openrouter)
    return urls
  }

  const modelsDevSlug = MODELS_DEV_PROVIDER_SLUGS[key]
  if (modelsDevSlug) {
    urls.push(modelsDevLogoUrl(modelsDevSlug))
  } else if (!local) {
    // Unknown provider id — still try models.dev before giving up.
    urls.push(modelsDevLogoUrl(key))
  }
  if (local) {
    urls.push(local)
  }
  return urls
}
