import { tokenStore } from "@/lib/storage"

const API_BASE = import.meta.env.VITE_API_URL || "/api"

type JwtPayload = { sid?: string }

const decodeJwtPayload = (token: string): JwtPayload | null => {
  try {
    const parts = token.split(".")
    if (parts.length < 2) return null
    const normalized = parts[1].replace(/-/g, "+").replace(/_/g, "/")
    const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, "=")
    const json = atob(padded)
    return JSON.parse(json) as JwtPayload
  } catch {
    return null
  }
}

export const tokenHasOidcSession = (token: string | null | undefined): boolean => {
  if (!token) return false
  const payload = decodeJwtPayload(token)
  return Boolean(payload?.sid)
}

let refreshInFlight: Promise<string | null> | null = null

/** Mint a new app JWT when the browser copy expired; IdP refresh runs on the server. */
export const refreshAccessToken = async (
  token?: string | null
): Promise<string | null> => {
  if (refreshInFlight) return refreshInFlight

  refreshInFlight = (async () => {
    const current = (token ?? tokenStore.get())?.trim() || ""
    if (!current || !tokenHasOidcSession(current)) return null
    try {
      const response = await fetch(`${API_BASE}/auth/refresh`, {
        method: "POST",
        headers: { Authorization: `Bearer ${current}` },
      })
      if (!response.ok) return null
      const data = (await response.json()) as { access_token?: string }
      const next = data.access_token?.trim() || ""
      if (!next) return null
      tokenStore.set(next)
      return next
    } catch {
      return null
    } finally {
      refreshInFlight = null
    }
  })()

  return refreshInFlight
}

export const stopTokenRefreshScheduler = () => {
  /* no-op; kept for callers that clear auth state */
}
