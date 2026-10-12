import { tokenStore } from "@/lib/storage"

const API_BASE = import.meta.env.VITE_API_URL || "/api"
const REFRESH_SKEW_MS = 60_000
const MIN_REFRESH_SKEW_MS = 5_000

type JwtPayload = { exp?: number; sid?: string }

let refreshTimer: ReturnType<typeof setTimeout> | null = null
let refreshInFlight: Promise<string | null> | null = null

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

export const readTokenExpiryMs = (token: string | null | undefined): number | null => {
  if (!token) return null
  const payload = decodeJwtPayload(token)
  if (!payload?.exp || typeof payload.exp !== "number") return null
  return payload.exp * 1000
}

const clearRefreshTimer = () => {
  if (refreshTimer != null) {
    clearTimeout(refreshTimer)
    refreshTimer = null
  }
}

export const stopTokenRefreshScheduler = () => {
  clearRefreshTimer()
}

/** Exchange a (possibly expired) SSO app JWT for a fresh one via IdP refresh_token. */
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

const refreshSkewMs = (lifetimeMs: number): number => {
  // For short Authentik access tokens, refresh sooner than a fixed 60s skew.
  const relative = Math.floor(lifetimeMs * 0.2)
  return Math.min(REFRESH_SKEW_MS, Math.max(MIN_REFRESH_SKEW_MS, relative))
}

export const scheduleTokenRefresh = (
  token: string | null | undefined,
  onRefreshed?: (next: string) => void
) => {
  clearRefreshTimer()
  if (!token || !tokenHasOidcSession(token)) return
  const expiryMs = readTokenExpiryMs(token)
  if (!expiryMs) return
  const lifetimeMs = Math.max(0, expiryMs - Date.now())
  const delay = Math.max(0, lifetimeMs - refreshSkewMs(lifetimeMs))
  refreshTimer = setTimeout(() => {
    // Prefer tokenStore so a reactive refresh mid-wait is not overwritten by a stale JWT.
    void refreshAccessToken().then((next) => {
      if (!next) {
        // Retry once shortly before hard expiry if the first attempt failed.
        const retryIn = Math.max(
          0,
          (readTokenExpiryMs(tokenStore.get()) ?? 0) - Date.now() - MIN_REFRESH_SKEW_MS
        )
        if (retryIn > 0 && retryIn < lifetimeMs) {
          refreshTimer = setTimeout(() => {
            void refreshAccessToken().then((retried) => {
              if (!retried) return
              onRefreshed?.(retried)
              scheduleTokenRefresh(retried, onRefreshed)
            })
          }, retryIn)
        }
        return
      }
      onRefreshed?.(next)
      scheduleTokenRefresh(next, onRefreshed)
    })
  }, delay)
}
