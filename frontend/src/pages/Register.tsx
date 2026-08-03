import { useEffect, useState } from "react"
import type { FormEvent } from "react"
import { useNavigate, Link } from "react-router"

import { authApi } from "@/lib/api"
import { useAuth } from "@/lib/auth-context"
import { useI18n } from "@/lib/i18n-context"
import { isValidPassword } from "@/lib/password"
import { LanguageSelect } from "@/components/LanguageSelect"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"

export const RegisterPage = () => {
  const navigate = useNavigate()
  const { setToken } = useAuth()
  const { t } = useI18n()
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [registrationEnabled, setRegistrationEnabled] = useState(true)
  const hasError = Boolean(error)

  useEffect(() => {
    authApi
      .registrationEnabled()
      .then((data) => setRegistrationEnabled(data.enabled))
      .catch(() => setRegistrationEnabled(false))
  }, [])

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()
    if (!registrationEnabled) {
      setError(t("auth_register_disabled"))
      return
    }
    if (!isValidPassword(password)) {
      setError(t("auth_password_requirements"))
      return
    }
    setLoading(true)
    setError(null)
    try {
      const data = await authApi.register(email, password)
      setToken(data.access_token)
      navigate("/chat")
    } catch (err) {
      setError(err instanceof Error ? err.message : t("auth_register_failed"))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex min-h-svh items-center justify-center bg-background p-4 sm:p-6">
      <div className="w-full max-w-md space-y-3">
        <div className="flex justify-end">
          <LanguageSelect />
        </div>
        <Card className="rounded-[var(--radius-card)] border-border bg-card shadow-none">
          <CardHeader>
            <div className="flex flex-col items-center gap-3 text-center">
              <img src="/favicon.svg" alt="Eldigen" className="size-10 object-contain" />
              <CardTitle className="font-heading text-4xl font-normal leading-10">
                {t("auth_create_account")}
              </CardTitle>
            </div>
          </CardHeader>
          <CardContent>
            <form onSubmit={onSubmit} className="space-y-4">
              <Input
                placeholder={t("auth_email")}
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                type="email"
                className={hasError ? "border-destructive focus-visible:ring-destructive" : ""}
                required
              />
              <Input
                placeholder={t("auth_password")}
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
              {registrationEnabled ? (
                <Button className="w-full" disabled={loading}>
                  {loading ? t("auth_register_loading") : t("auth_register")}
                </Button>
              ) : (
                <p className="text-center text-sm text-muted-foreground">
                  {t("auth_register_disabled_note")}
                </p>
              )}
              <div className="text-center text-sm text-muted-foreground">
                {t("auth_have_account")}{" "}
                <Link to="/login" className="underline">
                  {t("auth_sign_in")}
                </Link>
              </div>
            </form>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
