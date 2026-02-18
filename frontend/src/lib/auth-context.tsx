import { createContext, useContext, useMemo, useState } from "react"
import type { ReactNode } from "react"

import { tokenStore } from "@/lib/storage"

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
    tokenStore.clear()
    setTokenState(null)
  }

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
