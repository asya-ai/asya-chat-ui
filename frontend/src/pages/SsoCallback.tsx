import { useEffect, useMemo } from "react"
import { useNavigate, useLocation } from "@tanstack/react-router"

import { useAuth } from "@/lib/auth-context"

export const SsoCallbackPage = () => {
  const location = useLocation()
  const token = useMemo(
    () => new URLSearchParams(location.searchStr).get("token"),
    [location.searchStr]
  )
  const navigate = useNavigate()
  const { setToken } = useAuth()

  useEffect(() => {
    if (!token) {
      navigate({ to: "/login", replace: true })
      return
    }
    setToken(token)
    navigate({ to: "/chat/{-$chatId}", replace: true })
  }, [navigate, setToken, token])

  return null
}
