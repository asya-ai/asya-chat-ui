import { useCallback, useEffect, useRef, useState } from "react"

const SIDEBAR_OVERLAY_SELECTOR = [
  '[data-slot="dropdown-menu-content"][data-state="open"]',
  '[data-slot="dropdown-menu-sub-content"][data-state="open"]',
  '[data-slot="context-menu-content"][data-state="open"]',
  '[data-slot="dialog-content"][data-state="open"]',
].join(",")

export const isSidebarOverlayOpen = () =>
  Boolean(document.querySelector(SIDEBAR_OVERLAY_SELECTOR))

/** Keep pinned panel mounted through the ease-in-out width transition. */
export const SIDEBAR_PIN_MS = 600

export const useSidebarPinnedMount = (pinned: boolean) => {
  const [mounted, setMounted] = useState(pinned)

  useEffect(() => {
    if (pinned) {
      setMounted(true)
      return
    }
    const id = window.setTimeout(() => setMounted(false), SIDEBAR_PIN_MS)
    return () => window.clearTimeout(id)
  }, [pinned])

  return mounted
}

/** Hover-expand sidebar that stays open while portaled menus/dialogs are active. */
export const useSidebarHover = (pinned: boolean) => {
  const [hoverOpen, setHoverOpen] = useState(false)
  const pointerInsideRef = useRef(false)
  const closeTimerRef = useRef<number | null>(null)
  const overlayObserverRef = useRef<MutationObserver | null>(null)
  const watchOverlaysRef = useRef<() => void>(() => {})

  const clearCloseTimer = useCallback(() => {
    if (closeTimerRef.current != null) {
      window.clearTimeout(closeTimerRef.current)
      closeTimerRef.current = null
    }
  }, [])

  const disconnectOverlayObserver = useCallback(() => {
    overlayObserverRef.current?.disconnect()
    overlayObserverRef.current = null
  }, [])

  const finishCloseIfIdle = useCallback(() => {
    if (pinned || pointerInsideRef.current || isSidebarOverlayOpen()) return
    setHoverOpen(false)
  }, [pinned])

  const watchOverlaysThenClose = useCallback(() => {
    disconnectOverlayObserver()
    if (!isSidebarOverlayOpen()) {
      finishCloseIfIdle()
      return
    }
    const observer = new MutationObserver(() => {
      if (isSidebarOverlayOpen()) return
      disconnectOverlayObserver()
      // Rename/delete dialogs open right after a menu item click.
      window.setTimeout(() => {
        if (isSidebarOverlayOpen()) {
          watchOverlaysRef.current()
          return
        }
        finishCloseIfIdle()
      }, 50)
    })
    observer.observe(document.body, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ["data-state"],
    })
    overlayObserverRef.current = observer
  }, [disconnectOverlayObserver, finishCloseIfIdle])

  watchOverlaysRef.current = watchOverlaysThenClose

  const openHover = useCallback(() => {
    if (pinned) return
    pointerInsideRef.current = true
    clearCloseTimer()
    disconnectOverlayObserver()
    setHoverOpen(true)
  }, [clearCloseTimer, disconnectOverlayObserver, pinned])

  const closeHover = useCallback(() => {
    if (pinned) return
    pointerInsideRef.current = false
    clearCloseTimer()
    closeTimerRef.current = window.setTimeout(() => {
      closeTimerRef.current = null
      if (pointerInsideRef.current) return
      if (isSidebarOverlayOpen()) {
        watchOverlaysThenClose()
        return
      }
      setHoverOpen(false)
    }, 160)
  }, [clearCloseTimer, pinned, watchOverlaysThenClose])

  useEffect(() => {
    if (!pinned) return
    pointerInsideRef.current = false
    clearCloseTimer()
    disconnectOverlayObserver()
    setHoverOpen(false)
  }, [clearCloseTimer, disconnectOverlayObserver, pinned])

  useEffect(
    () => () => {
      clearCloseTimer()
      disconnectOverlayObserver()
    },
    [clearCloseTimer, disconnectOverlayObserver]
  )

  return { hoverOpen, openHover, closeHover }
}
