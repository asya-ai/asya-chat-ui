import { useMemo, useState } from "react"
import type { FormEvent } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"

import { authApi } from "@/lib/api"
import { useAuth } from "@/lib/auth-context"
import { useI18n } from "@/lib/i18n-context"
import { LanguageSelect } from "@/components/LanguageSelect"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { modelStore, orgStore } from "@/lib/storage"

export const InviteAcceptPage = () => {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const token = useMemo(() => params.get("token") ?? "", [params])
  const { setToken } = useAuth()
  const { t } = useI18n()
  const [password, setPassword] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setLoading(true)
    setError(null)
    try {
      const data = await authApi.acceptInvite(token, password || undefined)
      setToken(data.access_token)
      orgStore.clear()
      modelStore.clear()
      navigate("/chat")
    } catch (err) {
      setError(err instanceof Error ? err.message : t("auth_invite_failed"))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-6">
      <div className="w-full max-w-md space-y-3">
        <div className="flex justify-end">
          <LanguageSelect />
        </div>
        <Card>
          <CardHeader>
            <CardTitle>{t("auth_accept_invite")}</CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={onSubmit} className="space-y-4">
              <Input
                placeholder={t("auth_invite_token")}
                value={token}
                disabled
              />
              <Input
                placeholder={t("auth_set_password_optional")}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                type="password"
              />
              {error ? <p className="text-sm text-red-500">{error}</p> : null}
              <Button className="w-full" disabled={loading || !token}>
                {loading ? t("auth_accepting_invite") : t("auth_accept_invite")}
              </Button>
            </form>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
