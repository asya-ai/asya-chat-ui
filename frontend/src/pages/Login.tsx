import { useState } from "react"
import type { FormEvent } from "react"
import { useNavigate, Link } from "react-router-dom"

import { authApi } from "@/lib/api"
import { useAuth } from "@/lib/auth-context"
import { useI18n } from "@/lib/i18n-context"
import { LanguageSelect } from "@/components/LanguageSelect"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { modelStore, orgStore } from "@/lib/storage"

export const LoginPage = () => {
  const navigate = useNavigate()
  const { setToken } = useAuth()
  const { t } = useI18n()
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setLoading(true)
    setError(null)
    try {
      const data = await authApi.login(email, password)
      setToken(data.access_token)
      orgStore.clear()
      modelStore.clear()
      navigate("/chat")
    } catch (err) {
      setError(err instanceof Error ? err.message : t("auth_login_failed"))
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
            <CardTitle>{t("auth_sign_in")}</CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={onSubmit} className="space-y-4">
              <Input
                placeholder={t("auth_email")}
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                type="email"
                required
              />
              <Input
                placeholder={t("auth_password")}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                type="password"
                required
              />
              {error ? <p className="text-sm text-red-500">{error}</p> : null}
              <Button className="w-full" disabled={loading}>
                {loading ? t("auth_sign_in_loading") : t("auth_sign_in")}
              </Button>
              <div className="text-center text-sm text-muted-foreground">
                {t("auth_no_account")}{" "}
                <Link to="/register" className="underline">
                  {t("auth_register")}
                </Link>
              </div>
            </form>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
