import { useEffect, useState } from "react"

const THEME_KEY = "chatui_theme"

export type ThemeMode = "light" | "dark"

export const getTheme = (): ThemeMode => {
  const stored = localStorage.getItem(THEME_KEY)
  if (stored === "dark" || stored === "light") {
    return stored
  }
  if (typeof window !== "undefined" && window.matchMedia) {
    return window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light"
  }
  return "light"
}

export const setTheme = (mode: ThemeMode) => {
  localStorage.setItem(THEME_KEY, mode)
  applyTheme(mode)
}

export const applyTheme = (mode: ThemeMode) => {
  document.documentElement.classList.toggle("dark", mode === "dark")
  const themeColor = mode === "dark" ? "#111319" : "#e3e7f3"
  const meta = document.querySelector('meta[name="theme-color"]')
  if (meta) {
    meta.setAttribute("content", themeColor)
  }
}

export const toggleTheme = (): ThemeMode => {
  const next = getTheme() === "dark" ? "light" : "dark"
  setTheme(next)
  return next
}

/** Live light/dark from `<html class="dark">` (follows `setTheme`). */
export const useDocumentTheme = (): ThemeMode => {
  const [theme, setThemeState] = useState<ThemeMode>(() =>
    typeof document !== "undefined" && document.documentElement.classList.contains("dark")
      ? "dark"
      : getTheme()
  )

  useEffect(() => {
    const el = document.documentElement
    const sync = () => {
      setThemeState(el.classList.contains("dark") ? "dark" : "light")
    }
    sync()
    const obs = new MutationObserver(sync)
    obs.observe(el, { attributes: true, attributeFilter: ["class"] })
    return () => obs.disconnect()
  }, [])

  return theme
}

export type CssRgba = { r: number; g: number; b: number; a: number }

/** Resolve a CSS color (hex / rgb / rgba / named) to 0–255 channels. */
export const parseCssColor = (raw: string): CssRgba | null => {
  const value = raw.trim()
  if (!value) return null

  const hex = /^#([0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})$/i.exec(value)
  if (hex) {
    let h = hex[1]
    if (h.length === 3) {
      h = h
        .split("")
        .map((c) => c + c)
        .join("")
    }
    const r = Number.parseInt(h.slice(0, 2), 16)
    const g = Number.parseInt(h.slice(2, 4), 16)
    const b = Number.parseInt(h.slice(4, 6), 16)
    const a = h.length === 8 ? Number.parseInt(h.slice(6, 8), 16) : 255
    return { r, g, b, a }
  }

  const rgba =
    /^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)(?:\s*,\s*([\d.]+))?\s*\)$/i.exec(
      value
    )
  if (rgba) {
    const r = Math.round(Number(rgba[1]))
    const g = Math.round(Number(rgba[2]))
    const b = Math.round(Number(rgba[3]))
    const a =
      rgba[4] === undefined ? 255 : Math.round(Math.min(1, Math.max(0, Number(rgba[4]))) * 255)
    return { r, g, b, a }
  }

  if (typeof document === "undefined") return null
  const probe = document.createElement("span")
  probe.style.color = value
  probe.style.display = "none"
  document.body.appendChild(probe)
  const computed = getComputedStyle(probe).color
  document.body.removeChild(probe)
  if (computed === value) return null
  return parseCssColor(computed)
}

/** Read a CSS custom property from `:root` / `.dark` and parse to RGBA. */
export const cssVarColor = (name: string): CssRgba | null => {
  if (typeof document === "undefined") return null
  // Resolve via `var(--token)` so aliases like `--button-primary-bg: var(--primary)` work.
  const probe = document.createElement("span")
  probe.style.display = "none"
  probe.style.color = `var(${name})`
  document.documentElement.appendChild(probe)
  const computed = getComputedStyle(probe).color
  document.documentElement.removeChild(probe)
  return parseCssColor(computed)
}
