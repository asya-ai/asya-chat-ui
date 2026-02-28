import { useEffect, useState } from "react"
import type { FormEvent } from "react"
import { useNavigate, Link, useSearchParams } from "react-router-dom"

import { authApi } from "@/lib/api"
import { useAuth } from "@/lib/auth-context"
import { useI18n } from "@/lib/i18n-context"
import { LanguageSelect } from "@/components/LanguageSelect"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { modelStore, orgStore } from "@/lib/storage"

export const LoginPage = () => {
  const navigate = useNavigate()
  const { setToken } = useAuth()
  const { t } = useI18n()
  const [identifier, setIdentifier] = useState("")
  const [password, setPassword] = useState("")
  const [org, setOrg] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [registrationEnabled, setRegistrationEnabled] = useState(false)
  const [stage, setStage] = useState<"org" | "credentials">("org")
  const hasError = Boolean(error)
  const [searchParams] = useSearchParams()

  useEffect(() => {
    const orgParam = searchParams.get("org")
    if (orgParam) {
      setOrg(orgParam)
      setStage("org")
    }
  }, [searchParams])

  useEffect(() => {
    authApi
      .registrationEnabled()
      .then((data) => setRegistrationEnabled(data.enabled))
      .catch(() => setRegistrationEnabled(false))
  }, [])

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setLoading(true)
    setError(null)
    try {
      const orgValue = org.trim().toLowerCase()
      if (!orgValue) {
        setError(t("auth_login_failed"))
        return
      }
      if (stage === "org") {
        const resolve = await authApi.loginResolve("", orgValue)
        if (resolve.action === "sso" && resolve.redirect_url) {
          window.location.href = resolve.redirect_url
          return
        }
        setStage("credentials")
        return
      }
      const resolve = await authApi.loginResolve(identifier, orgValue || null)
      if (resolve.action === "sso" && resolve.redirect_url) {
        window.location.href = resolve.redirect_url
        return
      }
      if (!password.trim()) {
        setError(t("auth_login_failed"))
        return
      }
      const data = await authApi.login(identifier, password, orgValue || null)
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
              {stage === "org" ? (
                <Input
                  placeholder={t("auth_org")}
                  value={org}
                  onChange={(event) => setOrg(event.target.value)}
                  type="text"
                  className={hasError ? "border-destructive focus-visible:ring-destructive" : ""}
                  required
                />
              ) : (
                <>
                  <Input
                    placeholder={t("auth_identifier")}
                    value={identifier}
                    onChange={(event) => setIdentifier(event.target.value)}
                    type="text"
                    className={hasError ? "border-destructive focus-visible:ring-destructive" : ""}
                    required
                  />
                  <Input
                    placeholder={t("auth_org")}
                    value={org}
                    onChange={(event) => setOrg(event.target.value)}
                    type="text"
                    className={hasError ? "border-destructive focus-visible:ring-destructive" : ""}
                  />
                  <Input
                    placeholder={t("auth_password")}
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    type="password"
                    className={hasError ? "border-destructive focus-visible:ring-destructive" : ""}
                    required
                  />
                </>
              )}
              {error ? (
                <Alert variant="destructive">
                  <AlertDescription>{error}</AlertDescription>
                </Alert>
              ) : null}
              <Button className="w-full" disabled={loading}>
                {loading
                  ? t("auth_sign_in_loading")
                  : stage === "org"
                    ? t("auth_continue")
                    : t("auth_sign_in")}
              </Button>
              {stage === "credentials" ? (
                <div className="text-center text-sm text-muted-foreground">
                  <button
                    type="button"
                    className="underline"
                    onClick={() => setStage("org")}
                  >
                    {t("auth_org")}
                  </button>
                </div>
              ) : null}
              {registrationEnabled ? (
                <div className="text-center text-sm text-muted-foreground">
                  {t("auth_no_account")}{" "}
                  <Link to="/register" className="underline">
                    {t("auth_register")}
                  </Link>
                </div>
              ) : null}
            <div className="text-center text-sm">
              <Link to="/reset-password" className="underline">
                {t("auth_forgot_password")}
              </Link>
            </div>
            </form>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
