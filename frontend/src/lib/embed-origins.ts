/** Match postMessage event.origin against admin allowlist (exact or *.host). */

export const parseEmbedOrigins = (raw: string | null | undefined): string[] =>
  String(raw ?? "")
    .split(/[,;\s]+/)
    .map((o) => o.trim())
    .filter(Boolean)

const defaultPort = (scheme: string) => (scheme === "https:" ? "443" : "80")

/** Normalize admin patterns: `*.host` → `https://*.host`. */
export const normalizeEmbedOriginPattern = (pattern: string): string => {
  const trimmed = pattern.trim()
  if (!trimmed || trimmed === "*") return trimmed
  if (/^https?:\/\//i.test(trimmed)) return trimmed
  if (trimmed.startsWith("*.")) return `https://${trimmed}`
  if (trimmed.includes("://")) return trimmed
  // Bare hostname → exact https origin
  return `https://${trimmed}`
}

export const originAllowedByEmbedList = (
  origin: string,
  patterns: string[]
): boolean => {
  if (!patterns.length) return true
  let parsed: URL
  try {
    parsed = new URL(origin)
  } catch {
    return false
  }
  const host = parsed.hostname.toLowerCase()
  const protocol = parsed.protocol.toLowerCase()

  for (const raw of patterns) {
    const pattern = normalizeEmbedOriginPattern(raw)
    if (pattern === "*" || pattern === origin) return true

    // Exact origin without trailing slash quirks
    try {
      if (new URL(pattern).origin === parsed.origin) return true
    } catch {
      // not an exact URL — try wildcard
    }

    const wild = /^(https?):\/\/\*\.([^/:]+)(?::(\d+))?$/i.exec(pattern)
    if (!wild) continue
    const scheme = `${wild[1].toLowerCase()}:`
    const domain = wild[2].toLowerCase()
    const port = wild[3]
    if (protocol !== scheme) continue
    if (port && parsed.port && parsed.port !== port) continue
    if (port && !parsed.port && port !== defaultPort(scheme)) continue
    // *.example.com → any subdomain of example.com (not the apex itself)
    if (host !== domain && host.endsWith(`.${domain}`)) return true
  }
  return false
}

/** Read PP API key from parent ppConnect payload (accept common aliases). */
export const ppConnectApiKeyFromMessage = (data: Record<string, unknown>): string => {
  for (const key of ["apiKey", "api_key", "key"] as const) {
    const value = data[key]
    if (typeof value === "string" && value.trim()) return value.trim()
  }
  return ""
}
