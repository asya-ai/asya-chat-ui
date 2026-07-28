import type { ReactNode } from "react"
import { createContext, useContext, useEffect, useMemo, useState } from "react"

import { authApi } from "@/lib/api"
import { useAuth } from "@/lib/auth-context"
import { localeStore, tokenStore } from "@/lib/storage"
import { en } from "@/locales/en"
import { ja } from "@/locales/ja"
import { lv } from "@/locales/lv"

export type Locale = "en" | "lv" | "ja"

const translations = {
  en,
  lv,
  ja,
} as const

export type TranslationKey = keyof typeof en

/** English: n === 1. Latvian: ends with 1, but not 11/111/... */
export const usesSingularCount = (count: number, locale: Locale): boolean => {
  const n = Math.abs(Math.trunc(count))
  if (locale === "ja") {
    return false
  }
  if (locale === "lv") {
    return n % 10 === 1 && n % 100 !== 11
  }
  return n === 1
}

export type I18nContextValue = {
  locale: Locale
  setLocale: (locale: Locale) => void
  t: (key: TranslationKey, vars?: Record<string, string | number>) => string
  tCount: (
    singularKey: TranslationKey,
    pluralKey: TranslationKey,
    count: number,
    vars?: Record<string, string | number>
  ) => string
}

const I18nContext = createContext<I18nContextValue | null>(null)

const normalizeLocale = (value?: string | null): Locale => {
  if (!value) return "en"
  const normalized = value.toLowerCase()
  if (normalized.startsWith("lv")) {
    return "lv"
  }
  if (normalized.startsWith("ja")) {
    return "ja"
  }
  return "en"
}

const makeTranslator =
  (locale: Locale) =>
  (key: TranslationKey, vars?: Record<string, string | number>): string => {
    const template: string =
      translations[locale][key] ?? translations.en[key] ?? String(key)
    if (!vars) return template
    let result = template
    for (const [name, value] of Object.entries(vars)) {
      result = result.replace(`{${name}}`, String(value))
    }
    return result
  }

export const I18nProvider = ({ children }: { children: ReactNode }) => {
  const { token } = useAuth()
  const [locale, setLocaleState] = useState<Locale>(() => {
    const stored = localeStore.get()
    if (stored) return normalizeLocale(stored)
    if (typeof navigator === "undefined") return "en"
    return normalizeLocale(navigator.languages?.[0] ?? navigator.language)
  })

  const setLocale = (value: Locale) => {
    setLocaleState(value)
    localeStore.set(value)
    if (tokenStore.get()) {
      authApi.updateLocale(value).catch(() => null)
    }
  }

  useEffect(() => {
    if (!token) return
    let cancelled = false
    authApi
      .me()
      .then((me) => {
        if (cancelled || !me.locale) return
        const next = normalizeLocale(me.locale)
        setLocaleState(next)
        localeStore.set(next)
      })
      .catch(() => null)
    return () => {
      cancelled = true
    }
  }, [token])

  const t = useMemo(() => makeTranslator(locale), [locale])

  const tCount = useMemo(
    () =>
      (
        singularKey: TranslationKey,
        pluralKey: TranslationKey,
        count: number,
        vars?: Record<string, string | number>
      ) =>
        t(usesSingularCount(count, locale) ? singularKey : pluralKey, {
          count,
          ...vars,
        }),
    [locale, t]
  )

  useEffect(() => {
    if (typeof document === "undefined") return
    document.documentElement.lang = locale
  }, [locale])

  return (
    <I18nContext.Provider value={{ locale, setLocale, t, tCount }}>
      {children}
    </I18nContext.Provider>
  )
}

export const useI18n = () => {
  const context = useContext(I18nContext)
  if (!context) {
    throw new Error("useI18n must be used within I18nProvider")
  }
  return context
}
