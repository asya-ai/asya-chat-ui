import { useEffect, useMemo, useState } from "react"
import type { FormEvent } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"

import { authApi } from "@/lib/api"
import { useAuth } from "@/lib/auth-context"
import { useI18n } from "@/lib/i18n-context"
import { isValidPassword } from "@/lib/password"
import { LanguageSelect } from "@/components/LanguageSelect"
import { Alert, AlertDescription } from "@/components/ui/alert"
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
  const [inviteEmail, setInviteEmail] = useState<string>("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const hasError = Boolean(error)

  useEffect(() => {
    if (!token) return
    authApi
      .invitePreview(token)
      .then((data) => setInviteEmail(data.email))
      .catch((err) =>
        setError(err instanceof Error ? err.message : t("auth_invite_failed"))
      )
  }, [token, t])

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setLoading(true)
    setError(null)
    if (!isValidPassword(password)) {
      setError(t("auth_password_requirements"))
      setLoading(false)
      return
    }
    try {
      const data = await authApi.acceptInvite(token, password)
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
                placeholder={t("auth_invite_email")}
                value={inviteEmail}
                disabled
              />
              <Input
                placeholder={t("auth_set_password")}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                type="password"
                autoComplete="off"
                autoCapitalize="off"
                spellCheck={false}
                className={hasError ? "border-destructive focus-visible:ring-destructive" : ""}
                required
              />
              {error ? (
                <Alert variant="destructive">
                  <AlertDescription>{error}</AlertDescription>
                </Alert>
              ) : null}
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
