const API_BASE = import.meta.env.VITE_API_URL || "/api"
const RELOAD_GUARD_KEY = "chatui:deploy-reload"
const RELOAD_GUARD_MS = 15_000
const POLL_INTERVAL_MS = 60_000

export type DeploySignal = {
  frontend?: string
  backend?: string
}

type ReloadWindow = Pick<Window, "location" | "addEventListener" | "removeEventListener">

export const isStaleChunkError = (error: unknown): boolean => {
  const message = error instanceof Error ? error.message : String(error ?? "")
  return /Failed to fetch dynamically imported module|error loading dynamically imported module|Importing a module script failed|Loading chunk [\d]+ failed|Expected a JavaScript.*module script but the server responded with a MIME type/i.test(
    message
  )
}

const canReloadNow = (storage: Storage = sessionStorage): boolean => {
  const now = Date.now()
  try {
    const previous = Number(storage.getItem(RELOAD_GUARD_KEY) || 0)
    if (Number.isFinite(previous) && previous > 0 && now - previous < RELOAD_GUARD_MS) {
      return false
    }
    storage.setItem(RELOAD_GUARD_KEY, String(now))
    return true
  } catch {
    return true
  }
}

/** Reload once after a deploy or a missing hashed chunk (no SW/cache games). */
export const hardReloadOnce = (
  storage: Storage = sessionStorage,
  win: ReloadWindow = window
): boolean => {
  if (!canReloadNow(storage)) return false
  win.location.reload()
  return true
}

type PreloadErrorEventLike = Event & {
  preventDefault: () => void
}

/** Vite emits this when a lazy chunk 404s after a redeploy; reload onto fresh HTML. */
export const installStaleAssetRecovery = (options?: {
  storage?: Storage
  reload?: (storage?: Storage) => boolean
  win?: ReloadWindow
}): (() => void) => {
  const storage = options?.storage ?? sessionStorage
  const reload = options?.reload ?? hardReloadOnce
  const win = options?.win ?? window

  const onPreloadError = (event: Event) => {
    const preloadEvent = event as PreloadErrorEventLike
    if (reload(storage)) {
      preloadEvent.preventDefault()
    }
  }

  const onUnhandledRejection = (event: PromiseRejectionEvent) => {
    if (!isStaleChunkError(event.reason)) return
    if (reload(storage)) {
      event.preventDefault()
    }
  }

  win.addEventListener("vite:preloadError", onPreloadError)
  win.addEventListener("unhandledrejection", onUnhandledRejection)

  return () => {
    win.removeEventListener("vite:preloadError", onPreloadError)
    win.removeEventListener("unhandledrejection", onUnhandledRejection)
  }
}

const readJson = async (url: string): Promise<Record<string, unknown> | null> => {
  try {
    const response = await fetch(url, { cache: "no-store" })
    if (!response.ok) return null
    const data = (await response.json()) as unknown
    return data && typeof data === "object" ? (data as Record<string, unknown>) : null
  } catch {
    return null
  }
}

export const fetchDeploySignal = async (): Promise<DeploySignal> => {
  const [frontendJson, backendJson] = await Promise.all([
    readJson("/build-id.json"),
    readJson(`${API_BASE}/healthz`),
  ])
  const frontend =
    typeof frontendJson?.build_id === "string" ? frontendJson.build_id.trim() : ""
  const backend =
    typeof backendJson?.build_id === "string" ? backendJson.build_id.trim() : ""
  return {
    ...(frontend ? { frontend } : {}),
    ...(backend ? { backend } : {}),
  }
}

export const deploySignalChanged = (baseline: DeploySignal, next: DeploySignal): boolean => {
  if (baseline.frontend && next.frontend && baseline.frontend !== next.frontend) {
    return true
  }
  if (baseline.backend && next.backend && baseline.backend !== next.backend) {
    return true
  }
  return false
}

export const startDeployReloadWatcher = (options?: {
  intervalMs?: number
  fetchSignal?: () => Promise<DeploySignal>
  reload?: () => boolean
  isVisible?: () => boolean
}): (() => void) => {
  const intervalMs = options?.intervalMs ?? POLL_INTERVAL_MS
  const fetchSignal = options?.fetchSignal ?? fetchDeploySignal
  const reload = options?.reload ?? hardReloadOnce
  const isVisible = options?.isVisible ?? (() => document.visibilityState === "visible")

  let baseline: DeploySignal | null = null
  let cancelled = false
  let inFlight = false

  const check = async () => {
    if (cancelled || inFlight || !isVisible()) return
    inFlight = true
    try {
      const next = await fetchSignal()
      if (cancelled) return
      if (!baseline) {
        baseline = next
        return
      }
      if (deploySignalChanged(baseline, next)) {
        reload()
      }
    } finally {
      inFlight = false
    }
  }

  void check()
  const timer = window.setInterval(() => {
    void check()
  }, intervalMs)

  const onVisibility = () => {
    if (isVisible()) void check()
  }
  document.addEventListener("visibilitychange", onVisibility)

  return () => {
    cancelled = true
    window.clearInterval(timer)
    document.removeEventListener("visibilitychange", onVisibility)
  }
}
