import { useEffect, useState } from "react"
import { useNavigate } from "@tanstack/react-router"

import { useAuth } from "@/lib/auth-context"
import { tryCompleteEmbedAuth } from "@/lib/embed-auth"
import { useI18n } from "@/lib/i18n-context"

const readCallbackToken = () =>
  new URLSearchParams(window.location.search).get("token")

export const SsoCallbackPage = () => {
  const navigate = useNavigate()
  const { setToken } = useAuth()
  const { t } = useI18n()
  const [embedDone, setEmbedDone] = useState(false)

  useEffect(() => {
    const token = readCallbackToken()
    if (!token) {
      navigate({ to: "/login", replace: true })
      return
    }
    if (tryCompleteEmbedAuth(token)) {
      setEmbedDone(true)
      return
    }
    setToken(token)
    navigate({ href: "/chat", replace: true })
  }, [navigate, setToken])

  return (
    <div className="flex min-h-svh items-center justify-center bg-background p-4 text-center text-sm text-muted-foreground">
      {embedDone ? t("embed_auth_close_window") : t("auth_sign_in_loading")}
    </div>
  )
}
