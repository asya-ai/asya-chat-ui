import { useEffect, useState } from "react"
import { useLocation, useNavigate } from "react-router-dom"

import { apiKeyApi, authApi } from "@/lib/api"
import { useAuth } from "@/lib/auth-context"
import { useI18n } from "@/lib/i18n-context"
import { isValidPassword } from "@/lib/password"
import { LanguageSelect } from "@/components/LanguageSelect"
import { SettingsShell } from "@/components/SettingsShell"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { getTheme, toggleTheme } from "@/lib/theme"
import type { ApiKey } from "@/lib/types"
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
  const [apiKeysOpen, setApiKeysOpen] = useState(false)
  const [apiKeys, setApiKeys] = useState<ApiKey[]>([])
  const [apiKeyName, setApiKeyName] = useState("")
  const [apiKeyCreating, setApiKeyCreating] = useState(false)
  const [apiKeyError, setApiKeyError] = useState<string | null>(null)
  const [createdKey, setCreatedKey] = useState<string | null>(null)
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

  useEffect(() => {
    if (!apiKeysOpen) return
    setApiKeyError(null)
    apiKeyApi
      .list()
      .then((keys) => setApiKeys(keys))
      .catch((err) =>
        setApiKeyError(err instanceof Error ? err.message : t("common_error"))
      )
  }, [apiKeysOpen, t])

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

  const onCreateApiKey = async () => {
    setApiKeyError(null)
    setCreatedKey(null)
    setApiKeyCreating(true)
    try {
      const orgId = orgStore.get() ?? undefined
      const created = await apiKeyApi.create(
        apiKeyName.trim() || t("api_keys_default_name"),
        orgId || undefined
      )
      setCreatedKey(created.api_key)
      setApiKeyName("")
      const keys = await apiKeyApi.list()
      setApiKeys(keys)
    } catch (err) {
      setApiKeyError(err instanceof Error ? err.message : t("common_error"))
    } finally {
      setApiKeyCreating(false)
    }
  }

  const onRevokeApiKey = async (keyId: string) => {
    setApiKeyError(null)
    try {
      await apiKeyApi.revoke(keyId)
      const keys = await apiKeyApi.list()
      setApiKeys(keys)
    } catch (err) {
      setApiKeyError(err instanceof Error ? err.message : t("common_error"))
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

        <Card>
          <CardHeader>
            <CardTitle>{t("api_keys_title")}</CardTitle>
          </CardHeader>
          <CardContent>
            <Dialog open={apiKeysOpen} onOpenChange={setApiKeysOpen}>
              <DialogTrigger asChild>
                <Button variant="outline">{t("api_keys_manage")}</Button>
              </DialogTrigger>
              <DialogContent className="max-w-2xl">
                <DialogHeader>
                  <DialogTitle>{t("api_keys_title")}</DialogTitle>
                </DialogHeader>
                <div className="space-y-4">
                  <div className="rounded-md border px-3 py-2 text-xs text-muted-foreground">
                    <p>
                      {t("api_keys_endpoint_label")}{" "}
                      <span className="font-semibold text-foreground">
                        {`${window.location.origin}/api/v1`}
                      </span>
                    </p>
                    <p>{t("api_keys_header_note")}</p>
                  </div>
                  <div className="space-y-2">
                    <Input
                      placeholder={t("api_keys_name_placeholder")}
                      value={apiKeyName}
                      onChange={(event) => setApiKeyName(event.target.value)}
                    />
                    <Button onClick={onCreateApiKey} disabled={apiKeyCreating}>
                      {apiKeyCreating ? t("common_saving") : t("api_keys_create")}
                    </Button>
                  </div>
                  {createdKey ? (
                    <Alert>
                      <AlertDescription>
                        <div className="space-y-2">
                          <p>{t("api_keys_created_once")}</p>
                          <Input value={createdKey} readOnly />
                        </div>
                      </AlertDescription>
                    </Alert>
                  ) : null}
                  {apiKeyError ? (
                    <Alert variant="destructive">
                      <AlertDescription>{apiKeyError}</AlertDescription>
                    </Alert>
                  ) : null}
                  <div className="space-y-2">
                    {apiKeys.length === 0 ? (
                      <p className="text-sm text-muted-foreground">
                        {t("api_keys_empty")}
                      </p>
                    ) : (
                      apiKeys.map((key) => (
                        <div
                          key={key.id}
                          className="flex items-center justify-between rounded-md border px-3 py-2"
                        >
                          <div className="min-w-0">
                            <p className="truncate text-sm font-medium">{key.name}</p>
                            <p className="text-xs text-muted-foreground">
                              {t("api_keys_prefix")}: {key.prefix}
                            </p>
                          </div>
                          <Button
                            variant="destructive"
                            size="sm"
                            onClick={() => onRevokeApiKey(key.id)}
                            disabled={Boolean(key.revoked_at)}
                          >
                            {key.revoked_at ? t("api_keys_revoked") : t("api_keys_revoke")}
                          </Button>
                        </div>
                      ))
                    )}
                  </div>
                </div>
              </DialogContent>
            </Dialog>
          </CardContent>
        </Card>
      </div>
    </SettingsShell>
  )
}
