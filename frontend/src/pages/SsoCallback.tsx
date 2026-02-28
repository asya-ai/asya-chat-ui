import { useEffect } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"

import { useAuth } from "@/lib/auth-context"
import { modelStore, orgStore } from "@/lib/storage"

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
    orgStore.clear()
    modelStore.clear()
    navigate("/chat", { replace: true })
  }, [navigate, searchParams, setToken])

  return null
}
