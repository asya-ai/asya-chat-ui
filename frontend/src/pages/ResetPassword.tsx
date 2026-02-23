import { useMemo, useState } from "react"
import type { FormEvent } from "react"
import { useNavigate, useSearchParams, Link } from "react-router-dom"

import { authApi } from "@/lib/api"
import { useI18n } from "@/lib/i18n-context"
import { isValidPassword } from "@/lib/password"
import { LanguageSelect } from "@/components/LanguageSelect"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"

export const ResetPasswordPage = () => {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const token = useMemo(() => params.get("token") ?? "", [params])
  const { t } = useI18n()
  const [email, setEmail] = useState("")
  const [newPassword, setNewPassword] = useState("")
  const [confirmPassword, setConfirmPassword] = useState("")
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const hasError = Boolean(error)

  const onRequest = async (event: FormEvent) => {
    event.preventDefault()
    setLoading(true)
    setError(null)
    setSuccess(null)
    try {
      await authApi.requestPasswordReset(email)
      setSuccess(t("auth_reset_email_sent"))
    } catch (err) {
      setError(err instanceof Error ? err.message : t("auth_reset_failed"))
    } finally {
      setLoading(false)
    }
  }

  const onReset = async (event: FormEvent) => {
    event.preventDefault()
    if (newPassword !== confirmPassword) {
      setError(t("auth_reset_mismatch"))
      return
    }
    if (!isValidPassword(newPassword)) {
      setError(t("auth_password_requirements"))
      return
    }
    setLoading(true)
    setError(null)
    setSuccess(null)
    try {
      await authApi.confirmPasswordReset(token, newPassword)
      setSuccess(t("auth_reset_success"))
      setTimeout(() => navigate("/login"), 800)
    } catch (err) {
      setError(err instanceof Error ? err.message : t("auth_reset_failed"))
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
            <CardTitle>
              {token ? t("auth_reset_title") : t("auth_reset_request_title")}
            </CardTitle>
          </CardHeader>
          <CardContent>
            {token ? (
              <form onSubmit={onReset} className="space-y-4">
                <Input
                  type="password"
                  placeholder={t("auth_new_password")}
                  value={newPassword}
                  onChange={(event) => setNewPassword(event.target.value)}
                  autoComplete="off"
                  autoCapitalize="off"
                  spellCheck={false}
                  className={hasError ? "border-destructive focus-visible:ring-destructive" : ""}
                  required
                />
                <Input
                  type="password"
                  placeholder={t("auth_confirm_password")}
                  value={confirmPassword}
                  onChange={(event) => setConfirmPassword(event.target.value)}
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
                {success ? <p className="text-sm text-emerald-600">{success}</p> : null}
                <Button className="w-full" disabled={loading}>
                  {loading ? t("auth_resetting") : t("auth_reset_submit")}
                </Button>
                <div className="text-center text-sm text-muted-foreground">
                  <Link to="/login" className="underline">
                    {t("auth_back_to_login")}
                  </Link>
                </div>
              </form>
            ) : (
              <form onSubmit={onRequest} className="space-y-4">
                <Input
                  placeholder={t("auth_email")}
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  type="email"
                  className={hasError ? "border-destructive focus-visible:ring-destructive" : ""}
                  required
                />
                {error ? (
                  <Alert variant="destructive">
                    <AlertDescription>{error}</AlertDescription>
                  </Alert>
                ) : null}
                {success ? <p className="text-sm text-emerald-600">{success}</p> : null}
                <Button className="w-full" disabled={loading}>
                  {loading ? t("auth_resetting") : t("auth_reset_request_submit")}
                </Button>
                <div className="text-center text-sm text-muted-foreground">
                  <Link to="/login" className="underline">
                    {t("auth_back_to_login")}
                  </Link>
                </div>
              </form>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
