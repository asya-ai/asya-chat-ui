/** Embed iframe auth via SSO/login popup → session handoff to the embed frame. */

export const EMBED_AUTH_FLAG_KEY = "chatui_embed_popup"
export const EMBED_SESSION_MESSAGE = "chatui:embed-session"
export const EMBED_AUTH_REQUEST = "chatui:embed-auth-request"
export const EMBED_SESSION_ACK = "chatui:embed-session-ack"

export const markEmbedAuthPopup = () => {
  try {
    sessionStorage.setItem(EMBED_AUTH_FLAG_KEY, "1")
  } catch {
    // ignore
  }
}

export const isEmbedAuthPopup = () => {
  try {
    return sessionStorage.getItem(EMBED_AUTH_FLAG_KEY) === "1"
  } catch {
    return false
  }
}

export const clearEmbedAuthPopup = () => {
  try {
    sessionStorage.removeItem(EMBED_AUTH_FLAG_KEY)
  } catch {
    // ignore
  }
}

export const isEmbedClientSurface = () => {
  try {
    if (window.location.pathname.startsWith("/embed")) return true
    return window.self !== window.top
  } catch {
    return true
  }
}

const postSessionTo = (target: Window, accessToken: string, targetOrigin: string) => {
  try {
    target.postMessage(
      { type: EMBED_SESSION_MESSAGE, version: 1, accessToken },
      targetOrigin
    )
    return true
  } catch {
    return false
  }
}

/**
 * Publish JWT to the embed iframe.
 * Do not use localStorage — third-party iframe storage is partitioned from this popup.
 * Keep the popup open until the iframe ACKs (or timeout).
 */
export const tryCompleteEmbedAuth = (accessToken: string): boolean => {
  if (!accessToken || !isEmbedAuthPopup()) return false

  const origin = window.location.origin
  let closed = false
  const closeSoon = () => {
    if (closed) return
    closed = true
    clearEmbedAuthPopup()
    window.setTimeout(() => {
      try {
        window.close()
      } catch {
        // ignore
      }
    }, 200)
  }

  const pushToOpener = () => {
    const opener = window.opener as Window | null
    if (opener && !opener.closed) postSessionTo(opener, accessToken, origin)
  }

  // Opener may not be listening yet — retry briefly.
  pushToOpener()
  const openerTimer = window.setInterval(pushToOpener, 200)

  const onMessage = (event: MessageEvent) => {
    if (event.origin !== origin) return
    const data = event.data as { type?: string } | null
    if (!data || typeof data.type !== "string") return
    if (data.type === EMBED_SESSION_ACK) {
      window.clearInterval(openerTimer)
      window.removeEventListener("message", onMessage)
      closeSoon()
      return
    }
    if (data.type !== EMBED_AUTH_REQUEST) return
    if (event.source && "postMessage" in event.source) {
      postSessionTo(event.source as Window, accessToken, event.origin)
    }
  }
  window.addEventListener("message", onMessage)

  window.setTimeout(() => {
    window.clearInterval(openerTimer)
    window.removeEventListener("message", onMessage)
    closeSoon()
  }, 15_000)

  return true
}

export const openEmbedAuthPopup = (org?: string | null) => {
  const params = new URLSearchParams({ embed: "1" })
  const orgValue = org?.trim()
  if (orgValue) params.set("org", orgValue)
  const url = `/login?${params.toString()}`
  const popup = window.open(url, "chatui-embed-auth", "popup=yes,width=480,height=720")
  if (!popup || popup === window) return null
  return popup
}

export const requestEmbedSessionFromPopup = (popup: Window | null) => {
  if (!popup || popup.closed) return
  try {
    popup.postMessage({ type: EMBED_AUTH_REQUEST, version: 1 }, window.location.origin)
  } catch {
    // Popup may still be on the IdP origin.
  }
}

export const ackEmbedSessionToPopup = (popup: Window | null) => {
  if (!popup || popup.closed) return
  try {
    popup.postMessage({ type: EMBED_SESSION_ACK, version: 1 }, window.location.origin)
  } catch {
    // ignore
  }
}
