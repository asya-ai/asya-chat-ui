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

const OPENROUTER_ROUTED_ICONS: Record<string, string> = {
  openai: PROVIDER_ICON_URLS.openai,
  anthropic: PROVIDER_ICON_URLS.anthropic,
  google: PROVIDER_ICON_URLS.gemini,
  "google-ai-studio": PROVIDER_ICON_URLS.gemini,
  moonshotai: PROVIDER_ICON_URLS.moonshot,
  moonshot: PROVIDER_ICON_URLS.moonshot,
}

export function getProviderIconUrl(
  provider: string | null | undefined,
  modelName?: string | null
): string | null {
  const key = (provider ?? "").trim().toLowerCase()
  if (!key) return null

  if (key === "openrouter" && modelName) {
    const routed = modelName.split("/", 1)[0]?.trim().toLowerCase()
    if (routed && OPENROUTER_ROUTED_ICONS[routed]) {
      return OPENROUTER_ROUTED_ICONS[routed]
    }
  }

  return PROVIDER_ICON_URLS[key] ?? null
}
