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
}

export const toggleTheme = (): ThemeMode => {
  const next = getTheme() === "dark" ? "light" : "dark"
  setTheme(next)
  return next
}
