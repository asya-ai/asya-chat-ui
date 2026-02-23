import { useEffect, useState } from "react"
import { useLocation, useNavigate } from "react-router-dom"

import { authApi } from "@/lib/api"
import { useAuth } from "@/lib/auth-context"
import { useI18n } from "@/lib/i18n-context"
import { isValidPassword } from "@/lib/password"
import { LanguageSelect } from "@/components/LanguageSelect"
import { SettingsShell } from "@/components/SettingsShell"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { getTheme, toggleTheme } from "@/lib/theme"
import { modelStore, orgStore } from "@/lib/storage"

export const MePage = () => {
  const navigate = useNavigate()
  const location = useLocation()
  const { clearToken } = useAuth()
  const { t } = useI18n()
  const [email, setEmail] = useState<string>("")
  const [theme, setTheme] = useState(getTheme())
  const [isAdmin, setIsAdmin] = useState(false)
  const [isSuperAdmin, setIsSuperAdmin] = useState(false)
  const [currentPassword, setCurrentPassword] = useState("")
  const [newPassword, setNewPassword] = useState("")
  const [confirmPassword, setConfirmPassword] = useState("")
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const hasError = Boolean(error)

  useEffect(() => {
    authApi
      .me()
      .then((me) => {
        setEmail(me.email)
        setIsAdmin(me.is_admin)
        setIsSuperAdmin(me.is_super_admin)
      })
      .catch(() => setEmail(""))
  }, [])

  const onToggleTheme = () => {
    const next = toggleTheme()
    setTheme(next)
  }

  const onLogout = () => {
    clearToken()
    orgStore.clear()
    modelStore.clear()
    navigate("/login", { replace: true })
  }

  const onChangePassword = async () => {
    setError(null)
    setSuccess(null)
    if (newPassword !== confirmPassword) {
      setError(t("me_password_mismatch"))
      return
    }
    if (!isValidPassword(newPassword)) {
      setError(t("auth_password_requirements"))
      return
    }
    setSaving(true)
    try {
      await authApi.changePassword(currentPassword, newPassword)
      setCurrentPassword("")
      setNewPassword("")
      setConfirmPassword("")
      setSuccess(t("me_password_updated"))
    } catch (err) {
      setError(err instanceof Error ? err.message : t("me_password_update_failed"))
    } finally {
      setSaving(false)
    }
  }

  const navItems = [
    { label: t("me_settings"), href: "/settings/me", active: true },
    {
      label: t("org_section_users"),
      href: "/settings/users",
      visible: isAdmin,
      active: location.pathname.startsWith("/settings/users"),
    },
    {
      label: t("org_section_orgs"),
      href: "/settings/organisation",
      visible: isSuperAdmin,
      active: location.pathname.startsWith("/settings/organisation"),
    },
    {
      label: t("org_section_models"),
      href: "/settings/models",
      visible: isSuperAdmin,
      active: location.pathname.startsWith("/settings/models"),
    },
    {
      label: t("usage_title"),
      href: "/usage",
      visible: isAdmin,
      active: location.pathname.startsWith("/usage"),
    },
  ]

  return (
    <SettingsShell
      title={t("me_title")}
      items={navItems}
      actions={
        <Button variant="outline" onClick={() => navigate("/chat")}>
          {t("common_back_to_chat")}
        </Button>
      }
    >
      <div className="space-y-6">
        <Card>
          <CardHeader>
            <CardTitle>{t("me_profile")}</CardTitle>
          </CardHeader>
          <CardContent>
            <Input value={email} readOnly placeholder={t("auth_email")} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>{t("me_preferences")}</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap items-center gap-3">
            <LanguageSelect />
            <Button variant="outline" onClick={onToggleTheme}>
              {t("theme_label", {
                theme: theme === "dark" ? t("theme_dark") : t("theme_light"),
              })}
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>{t("me_security")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <Input
              type="password"
              placeholder={t("me_current_password")}
              value={currentPassword}
              onChange={(event) => setCurrentPassword(event.target.value)}
              className={hasError ? "border-destructive focus-visible:ring-destructive" : ""}
            />
            <Input
              type="password"
              placeholder={t("me_new_password")}
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
              className={hasError ? "border-destructive focus-visible:ring-destructive" : ""}
            />
            <Input
              type="password"
              placeholder={t("me_confirm_password")}
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)}
              className={hasError ? "border-destructive focus-visible:ring-destructive" : ""}
            />
            {error ? (
              <Alert variant="destructive">
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            ) : null}
            {success ? <p className="text-sm text-emerald-600">{success}</p> : null}
            <Button onClick={onChangePassword} disabled={saving}>
              {saving ? t("me_password_updating") : t("me_update_password")}
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>{t("me_account")}</CardTitle>
          </CardHeader>
          <CardContent>
            <Button variant="destructive" onClick={onLogout}>
              {t("me_logout")}
            </Button>
          </CardContent>
        </Card>
      </div>
    </SettingsShell>
  )
}
