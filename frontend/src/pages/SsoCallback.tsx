import { useEffect } from "react"
import { useNavigate, useSearchParams } from "react-router"

import { useAuth } from "@/lib/auth-context"

export const SsoCallbackPage = () => {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const { setToken } = useAuth()

  useEffect(() => {
    const token = searchParams.get("token")
    if (!token) {
      navigate("/login", { replace: true })
      return
    }
    setToken(token)
    navigate("/chat", { replace: true })
  }, [navigate, searchParams, setToken])

  return null
}
