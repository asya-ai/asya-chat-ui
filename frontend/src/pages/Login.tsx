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
import { loginOrgStore } from "@/lib/storage"

type Stage = "org" | "sso" | "credentials"

export const LoginPage = () => {
  const navigate = useNavigate()
  const { setToken } = useAuth()
  const { t } = useI18n()
  const [identifier, setIdentifier] = useState("")
  const [password, setPassword] = useState("")
  const [org, setOrg] = useState("")
  const [ssoRedirectUrl, setSsoRedirectUrl] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [resolving, setResolving] = useState(false)
  const [registrationEnabled, setRegistrationEnabled] = useState(false)
  const [stage, setStage] = useState<Stage>("org")
  const hasError = Boolean(error)
  const [searchParams] = useSearchParams()

  useEffect(() => {
    const orgParam = searchParams.get("org")
    const initialOrg = orgParam ? orgParam.trim().toLowerCase() : loginOrgStore.get()
    const clientHost = window.location.host

    const applyResolve = (resolve: { action: string; redirect_url?: string | null; org?: string | null }) => {
      const resolvedOrg = resolve.org?.trim().toLowerCase()
      if (resolvedOrg) {
        setOrg(resolvedOrg)
        loginOrgStore.set(resolvedOrg)
      }
      if (resolve.action === "sso" && resolve.redirect_url) {
        setSsoRedirectUrl(resolve.redirect_url)
        setStage("sso")
        return
      }
      if (resolvedOrg) {
        setStage("credentials")
      }
    }

    if (initialOrg) {
      let cancelled = false
      setOrg(initialOrg)
      loginOrgStore.set(initialOrg)
      setResolving(true)
      authApi
        .loginResolve("", initialOrg, clientHost)
        .then((resolve) => {
          if (cancelled) return
          applyResolve(resolve)
        })
        .catch(() => {
          if (cancelled) return
          setStage("org")
        })
        .finally(() => {
          if (!cancelled) setResolving(false)
        })

      return () => {
        cancelled = true
      }
    }

    let cancelled = false
    setResolving(true)
    authApi
      .loginResolve("", null, clientHost)
      .then((resolve) => {
        if (cancelled) return
        applyResolve(resolve)
      })
      .catch(() => {
        if (cancelled) return
        setStage("org")
      })
      .finally(() => {
        if (!cancelled) setResolving(false)
      })

    return () => {
      cancelled = true
    }
  }, [searchParams])

  useEffect(() => {
    authApi
      .registrationEnabled()
      .then((data) => setRegistrationEnabled(data.enabled))
      .catch(() => setRegistrationEnabled(false))
  }, [])

  const resetToOrg = () => {
    setStage("org")
    setSsoRedirectUrl(null)
    setIdentifier("")
    setPassword("")
    setError(null)
  }

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setLoading(true)
    setError(null)
    const clientHost = window.location.host
    try {
      const orgValue = org.trim().toLowerCase()
      if (!orgValue) {
        setError(t("auth_login_failed"))
        return
      }
      loginOrgStore.set(orgValue)
      if (stage === "org") {
        const resolve = await authApi.loginResolve("", orgValue, clientHost)
        if (resolve.org) {
          setOrg(resolve.org)
          loginOrgStore.set(resolve.org)
        }
        if (resolve.action === "sso" && resolve.redirect_url) {
          setSsoRedirectUrl(resolve.redirect_url)
          setStage("sso")
          return
        }
        setStage("credentials")
        return
      }
      const resolve = await authApi.loginResolve(identifier, orgValue, clientHost)
      if (resolve.org) {
        setOrg(resolve.org)
        loginOrgStore.set(resolve.org)
      }
      if (resolve.action === "sso" && resolve.redirect_url) {
        window.location.href = resolve.redirect_url
        return
      }
      if (!password.trim()) {
        setError(t("auth_login_failed"))
        return
      }
      const data = await authApi.login(identifier, password, orgValue, clientHost)
      setToken(data.access_token)
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
            <div className="flex flex-col gap-1">
              <CardTitle>{t("auth_sign_in")}</CardTitle>
              {stage !== "org" ? (
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <span>{t("auth_signing_in_to", { org })}</span>
                  <button
                    type="button"
                    className="underline hover:text-foreground"
                    onClick={resetToOrg}
                  >
                    {t("auth_change_org")}
                  </button>
                </div>
              ) : null}
            </div>
          </CardHeader>
          <CardContent>
            {stage === "sso" ? (
              <div className="space-y-4">
                {error ? (
                  <Alert variant="destructive">
                    <AlertDescription>{error}</AlertDescription>
                  </Alert>
                ) : null}
                <Button
                  className="w-full"
                  disabled={loading || !ssoRedirectUrl}
                  onClick={() => {
                    if (ssoRedirectUrl) {
                      setLoading(true)
                      window.location.href = ssoRedirectUrl
                    }
                  }}
                >
                  {loading ? t("auth_sign_in_loading") : t("auth_continue_sso")}
                </Button>
              </div>
            ) : (
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
                <Button className="w-full" disabled={loading || resolving}>
                  {loading || resolving
                    ? t("auth_sign_in_loading")
                    : stage === "org"
                      ? t("auth_continue")
                      : t("auth_sign_in")}
                </Button>
                {registrationEnabled ? (
                  <div className="text-center text-sm text-muted-foreground">
                    {t("auth_no_account")}{" "}
                    <Link to="/register" className="underline">
                      {t("auth_register")}
                    </Link>
                  </div>
                ) : null}
                {stage === "credentials" ? (
                  <div className="text-center text-sm">
                    <Link to="/reset-password" className="underline">
                      {t("auth_forgot_password")}
                    </Link>
                  </div>
                ) : null}
              </form>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
