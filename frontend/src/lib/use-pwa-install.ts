import { useEffect, useState } from "react"

type BeforeInstallPromptEvent = Event & {
  prompt: () => Promise<void>
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>
}

export type PwaInstallPlatform = "ios" | "android" | "desktop-chromium" | "desktop-safari" | "other"

const isStandaloneDisplay = () => {
  if (typeof window === "undefined") return false
  if (window.matchMedia("(display-mode: standalone)").matches) return true
  const nav = window.navigator as Navigator & { standalone?: boolean }
  return nav.standalone === true
}

const detectPlatform = (): PwaInstallPlatform => {
  if (typeof window === "undefined") return "other"
  const ua = window.navigator.userAgent
  if (/iphone|ipad|ipod/i.test(ua)) return "ios"
  if (/android/i.test(ua)) return "android"
  if (/safari/i.test(ua) && !/chrome|chromium|crios|edg\//i.test(ua)) {
    return "desktop-safari"
  }
  if (/chrome|chromium|crios|edg\//i.test(ua)) return "desktop-chromium"
  return "other"
}

const isLocalhost = () => {
  if (typeof window === "undefined") return false
  const { hostname } = window.location
  return hostname === "localhost" || hostname === "127.0.0.1" || hostname === "[::1]"
}

export const usePwaInstall = () => {
  const [deferredPrompt, setDeferredPrompt] = useState<BeforeInstallPromptEvent | null>(
    null
  )
  const [installed, setInstalled] = useState(() => isStandaloneDisplay())
  const [platform] = useState(() => detectPlatform())
  const [needsHttps] = useState(
    () => typeof window !== "undefined" && !window.isSecureContext && !isLocalhost()
  )

  useEffect(() => {
    const syncInstalled = () => setInstalled(isStandaloneDisplay())
    syncInstalled()

    const onBeforeInstall = (event: Event) => {
      event.preventDefault()
      setDeferredPrompt(event as BeforeInstallPromptEvent)
    }
    const onInstalled = () => {
      setInstalled(true)
      setDeferredPrompt(null)
    }

    window.addEventListener("beforeinstallprompt", onBeforeInstall)
    window.addEventListener("appinstalled", onInstalled)
    const media = window.matchMedia("(display-mode: standalone)")
    media.addEventListener("change", syncInstalled)

    return () => {
      window.removeEventListener("beforeinstallprompt", onBeforeInstall)
      window.removeEventListener("appinstalled", onInstalled)
      media.removeEventListener("change", syncInstalled)
    }
  }, [])

  const install = async () => {
    if (!deferredPrompt) return false
    await deferredPrompt.prompt()
    const { outcome } = await deferredPrompt.userChoice
    setDeferredPrompt(null)
    if (outcome === "accepted") {
      setInstalled(true)
      return true
    }
    return false
  }

  return {
    installed,
    canPromptInstall: Boolean(deferredPrompt),
    platform,
    needsHttps,
    install,
  }
}
