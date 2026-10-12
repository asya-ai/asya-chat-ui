import { createContext, useContext, useEffect, useMemo, useState } from "react"
import type { ReactNode } from "react"

import { tokenStore } from "@/lib/storage"
import {
  scheduleTokenRefresh,
  stopTokenRefreshScheduler,
} from "@/lib/token-refresh"

type AuthContextValue = {
  token: string | null
  setToken: (token: string) => void
  clearToken: () => void
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined)

export const AuthProvider = ({ children }: { children: ReactNode }) => {
  const [token, setTokenState] = useState<string | null>(tokenStore.get())

  const setToken = (next: string) => {
    tokenStore.set(next)
    setTokenState(next)
  }

  const clearToken = () => {
    stopTokenRefreshScheduler()
    tokenStore.clear()
    setTokenState(null)
  }

  useEffect(() => {
    if (!token) {
      stopTokenRefreshScheduler()
      return
    }
    scheduleTokenRefresh(token, (next) => {
      setTokenState(next)
    })
    return () => stopTokenRefreshScheduler()
  }, [token])

  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key !== "chatui_token") return
      const next = event.newValue
      if (!next) {
        stopTokenRefreshScheduler()
        setTokenState(null)
        return
      }
      setTokenState(next)
    }
    window.addEventListener("storage", onStorage)
    return () => window.removeEventListener("storage", onStorage)
  }, [])

  const value = useMemo(() => ({ token, setToken, clearToken }), [token])
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export const useAuth = () => {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error("useAuth must be used within AuthProvider")
  }
  return context
}
