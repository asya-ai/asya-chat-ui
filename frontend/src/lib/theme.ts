const THEME_KEY = "chatui_theme"

export type ThemeMode = "light" | "dark"

export const getTheme = (): ThemeMode => {
  const stored = localStorage.getItem(THEME_KEY)
  return stored === "dark" ? "dark" : "light"
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
