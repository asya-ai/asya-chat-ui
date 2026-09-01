import { useEffect } from "react"
import { useNavigate } from "@tanstack/react-router"

import { useAuth } from "@/lib/auth-context"

const readCallbackToken = () =>
  new URLSearchParams(window.location.search).get("token")

export const SsoCallbackPage = () => {
  const navigate = useNavigate()
  const { setToken } = useAuth()

  useEffect(() => {
    const token = readCallbackToken()
    if (!token) {
      navigate({ to: "/login", replace: true })
      return
    }
    setToken(token)
    navigate({ href: "/chat", replace: true })
  }, [navigate, setToken])

  return null
}
