import { useEffect, useState } from "react"
import { useLocation, useNavigate } from "@tanstack/react-router"

import { apiKeyApi, authApi, memoryApi } from "@/lib/api"
import { useAuth } from "@/lib/auth-context"
import { useI18n, type TranslationKey } from "@/lib/i18n-context"
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { getTheme, toggleTheme } from "@/lib/theme"
import { usePwaInstall } from "@/lib/use-pwa-install"
import type { ApiKey, UserMemory } from "@/lib/types"
import { Pencil, Trash2, X, Check } from "lucide-react"
import {
  actionInfoLevelStore,
  orgStore,
  type ActionInfoLevel,
} from "@/lib/storage"

export const MePage = () => {
  const navigate = useNavigate()
  const location = useLocation()
  const { clearToken } = useAuth()
  const { t } = useI18n()
  const {
    installed: pwaInstalled,
    canPromptInstall,
    platform,
    needsHttps,
    install: installPwa,
  } = usePwaInstall()
  const [theme, setTheme] = useState(getTheme())
  const [installingPwa, setInstallingPwa] = useState(false)
  const [installHelpOpen, setInstallHelpOpen] = useState(false)
  const [isAdmin, setIsAdmin] = useState(false)
  const [isSuperAdmin, setIsSuperAdmin] = useState(false)
  const [passwordOpen, setPasswordOpen] = useState(false)
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
  const [actionInfoLevel, setActionInfoLevel] = useState<ActionInfoLevel>(() =>
    actionInfoLevelStore.get()
  )
  const [memoryEnabled, setMemoryEnabled] = useState(false)
  const [memoriesOpen, setMemoriesOpen] = useState(false)
  const [memories, setMemories] = useState<UserMemory[]>([])
  const [editingMemoryId, setEditingMemoryId] = useState<string | null>(null)
  const [editingContent, setEditingContent] = useState("")
  const hasError = Boolean(error)

  useEffect(() => {
    authApi
      .me()
      .then((me) => {
        setIsAdmin(me.is_admin)
        setIsSuperAdmin(me.is_super_admin)
        setMemoryEnabled(me.memory_enabled)
      })
      .catch(() => null)
  }, [])

  useEffect(() => {
    if (!memoriesOpen || !memoryEnabled) return
    memoryApi.list().then(setMemories).catch(() => setMemories([]))
  }, [memoriesOpen, memoryEnabled])

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
    authApi
      .logout()
      .catch(() => null)
      .finally(() => {
        clearToken()
        navigate({ to: "/login", replace: true })
      })
  }

  const onActionInfoLevelChange = (level: ActionInfoLevel) => {
    setActionInfoLevel(level)
    actionInfoLevelStore.set(level)
  }

  const onToggleMemory = async (enabled: boolean) => {
    setMemoryEnabled(enabled)
    if (!enabled) setMemoriesOpen(false)
    try {
      await authApi.toggleMemory(enabled)
    } catch {
      setMemoryEnabled(!enabled)
    }
  }

  const onDeleteMemory = async (memoryId: string) => {
    try {
      await memoryApi.remove(memoryId)
      setMemories((prev) => prev.filter((m) => m.id !== memoryId))
    } catch {
      // ignore
    }
  }

  const onStartEditMemory = (memory: UserMemory) => {
    setEditingMemoryId(memory.id)
    setEditingContent(memory.content)
  }

  const onSaveMemory = async () => {
    if (!editingMemoryId) return
    try {
      const updated = await memoryApi.update(editingMemoryId, editingContent)
      setMemories((prev) => prev.map((m) => (m.id === updated.id ? updated : m)))
      setEditingMemoryId(null)
    } catch {
      // ignore
    }
  }

  const onPasswordOpenChange = (open: boolean) => {
    setPasswordOpen(open)
    if (!open) {
      setCurrentPassword("")
      setNewPassword("")
      setConfirmPassword("")
      setError(null)
      setSuccess(null)
    }
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
      setApiKeys((prev) => prev.filter((key) => key.id !== keyId))
    } catch (err) {
      setApiKeyError(err instanceof Error ? err.message : t("common_error"))
    }
  }

  const installHintKey: TranslationKey = needsHttps
    ? "me_install_app_https"
    : platform === "ios"
      ? "me_install_app_ios"
      : platform === "android"
        ? "me_install_app_android"
        : platform === "desktop-chromium"
          ? "me_install_app_desktop_chrome"
          : platform === "desktop-safari"
            ? "me_install_app_desktop_safari"
            : "me_install_app_other"
  const showInstallSection =
    !pwaInstalled && (canPromptInstall || needsHttps || platform !== "other")

  const onInstallApp = () => {
    if (canPromptInstall) {
      setInstallingPwa(true)
      void installPwa().finally(() => setInstallingPwa(false))
      return
    }
    setInstallHelpOpen(true)
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
      label: t("org_section_teams"),
      href: "/settings/teams",
      visible: isAdmin,
      active: location.pathname.startsWith("/settings/teams"),
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
      label: t("instance_providers_title"),
      href: "/settings/providers",
      visible: isSuperAdmin,
      active: location.pathname.startsWith("/settings/providers"),
    },
    {
      label: t("diagnosis_title"),
      href: "/settings/diagnosis",
      visible: isSuperAdmin,
      active: location.pathname.startsWith("/settings/diagnosis"),
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
        <Button variant="outline" onClick={() => navigate({ to: "/chat/{-$chatId}" })}>
          {t("common_back_to_chat")}
        </Button>
      }
    >
      <div className="space-y-6">
        <Card>
          <CardHeader>
            <CardTitle>{t("me_preferences")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap items-center gap-3">
              <LanguageSelect />
              <Button variant="outline" onClick={onToggleTheme}>
                {t("theme_label", {
                  theme: theme === "dark" ? t("theme_dark") : t("theme_light"),
                })}
              </Button>
            </div>
            {showInstallSection ? (
              <>
                <div className="pt-1 border-t">
                  <div className="flex justify-between items-start gap-4 py-2">
                    <div className="space-y-1">
                      <p className="font-medium text-sm leading-5">
                        {t("me_install_app")}
                      </p>
                      <p className="text-muted-foreground text-xs leading-5">
                        {t("me_install_app_desc")}
                      </p>
                    </div>
                    <Button
                      variant="outline"
                      className="shrink-0"
                      disabled={installingPwa}
                      onClick={onInstallApp}
                    >
                      {canPromptInstall ? t("me_install_app") : t("me_install_app_help")}
                    </Button>
                  </div>
                </div>
                <Dialog open={installHelpOpen} onOpenChange={setInstallHelpOpen}>
                  <DialogContent className="max-w-md">
                    <DialogHeader>
                      <DialogTitle>{t("me_install_app_help_title")}</DialogTitle>
                    </DialogHeader>
                    <p className="text-sm leading-6 text-muted-foreground">{t(installHintKey)}</p>
                  </DialogContent>
                </Dialog>
              </>
            ) : null}
            <div className="pt-1 border-t">
              <div className="flex justify-between items-start gap-4 py-2">
                <div className="space-y-1">
                  <p className="font-medium text-sm leading-5">
                    {t("me_action_info")}
                  </p>
                  <p className="text-muted-foreground text-xs leading-5">
                    {t("me_action_info_desc")}
                  </p>
                </div>
                <Select
                  value={actionInfoLevel}
                  onValueChange={(value) =>
                    onActionInfoLevelChange(value as ActionInfoLevel)
                  }
                >
                  <SelectTrigger className="w-48 shrink-0">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">{t("me_action_info_none")}</SelectItem>
                    <SelectItem value="detailed">
                      {t("me_action_info_detailed")}
                    </SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="pt-1 border-t space-y-3">
              <label className="flex justify-between items-start gap-4 cursor-pointer py-2">
                <div className="space-y-1">
                  <p className="font-medium text-sm leading-5">
                    {t("me_memory_enabled")}
                  </p>
                  <p className="text-muted-foreground text-xs leading-5">
                    {t("me_memory_enabled_desc")}
                  </p>
                </div>
                <Switch
                  checked={memoryEnabled}
                  onCheckedChange={onToggleMemory}
                />
              </label>
              {memoryEnabled ? (
                <Dialog open={memoriesOpen} onOpenChange={setMemoriesOpen}>
                  <DialogTrigger asChild>
                    <Button variant="outline">{t("me_memory_see")}</Button>
                  </DialogTrigger>
                  <DialogContent className="max-w-2xl">
                    <DialogHeader>
                      <DialogTitle>{t("me_memory_title")}</DialogTitle>
                    </DialogHeader>
                    <div className="space-y-2 max-h-[60vh] overflow-y-auto">
                      {memories.length === 0 ? (
                        <p className="text-muted-foreground text-sm">
                          {t("me_memory_empty")}
                        </p>
                      ) : (
                        memories.map((memory) => (
                          <div
                            key={memory.id}
                            className="flex items-start gap-2 px-3 py-2 border rounded-md text-sm"
                          >
                            {editingMemoryId === memory.id ? (
                              <div className="flex-1 flex items-center gap-2">
                                <Input
                                  value={editingContent}
                                  onChange={(e) => setEditingContent(e.target.value)}
                                  className="h-8 text-sm"
                                  autoFocus
                                  onKeyDown={(e) => {
                                    if (e.key === "Enter") onSaveMemory()
                                    if (e.key === "Escape") setEditingMemoryId(null)
                                  }}
                                />
                                <Button
                                  variant="ghost"
                                  size="icon"
                                  className="h-7 w-7 shrink-0"
                                  onClick={onSaveMemory}
                                >
                                  <Check className="h-3.5 w-3.5" />
                                </Button>
                                <Button
                                  variant="ghost"
                                  size="icon"
                                  className="h-7 w-7 shrink-0"
                                  onClick={() => setEditingMemoryId(null)}
                                >
                                  <X className="h-3.5 w-3.5" />
                                </Button>
                              </div>
                            ) : (
                              <>
                                <span className="flex-1 min-w-0 wrap-break-word">
                                  {memory.content}
                                </span>
                                <Button
                                  variant="ghost"
                                  size="icon"
                                  className="h-7 w-7 shrink-0"
                                  onClick={() => onStartEditMemory(memory)}
                                >
                                  <Pencil className="h-3.5 w-3.5" />
                                </Button>
                                <Button
                                  variant="ghost"
                                  size="icon"
                                  className="h-7 w-7 shrink-0 text-destructive hover:text-destructive"
                                  onClick={() => onDeleteMemory(memory.id)}
                                >
                                  <Trash2 className="h-3.5 w-3.5" />
                                </Button>
                              </>
                            )}
                          </div>
                        ))
                      )}
                    </div>
                  </DialogContent>
                </Dialog>
              ) : null}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>{t("me_account")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex flex-wrap gap-3">
              <Dialog open={passwordOpen} onOpenChange={onPasswordOpenChange}>
                <DialogTrigger asChild>
                  <Button variant="outline">{t("me_change_password")}</Button>
                </DialogTrigger>
                <DialogContent className="max-w-md">
                  <DialogHeader>
                    <DialogTitle>{t("me_change_password")}</DialogTitle>
                  </DialogHeader>
                  <div className="space-y-3">
                    <Input
                      type="password"
                      placeholder={t("me_current_password")}
                      value={currentPassword}
                      onChange={(event) => setCurrentPassword(event.target.value)}
                      className={
                        hasError ? "border-destructive focus-visible:ring-destructive" : ""
                      }
                    />
                    <Input
                      type="password"
                      placeholder={t("me_new_password")}
                      value={newPassword}
                      onChange={(event) => setNewPassword(event.target.value)}
                      className={
                        hasError ? "border-destructive focus-visible:ring-destructive" : ""
                      }
                    />
                    <Input
                      type="password"
                      placeholder={t("me_confirm_password")}
                      value={confirmPassword}
                      onChange={(event) => setConfirmPassword(event.target.value)}
                      className={
                        hasError ? "border-destructive focus-visible:ring-destructive" : ""
                      }
                    />
                    {error ? (
                      <Alert variant="destructive">
                        <AlertDescription>{error}</AlertDescription>
                      </Alert>
                    ) : null}
                    {success ? (
                      <p className="text-emerald-600 text-sm">{success}</p>
                    ) : null}
                    <Button onClick={onChangePassword} disabled={saving}>
                      {saving ? t("me_password_updating") : t("me_update_password")}
                    </Button>
                  </div>
                </DialogContent>
              </Dialog>

              <Dialog open={apiKeysOpen} onOpenChange={setApiKeysOpen}>
                <DialogTrigger asChild>
                  <Button variant="outline">{t("api_keys_manage")}</Button>
                </DialogTrigger>
                <DialogContent className="max-w-2xl">
                  <DialogHeader>
                    <DialogTitle>{t("api_keys_title")}</DialogTitle>
                  </DialogHeader>
                  <div className="space-y-4">
                    <div className="px-3 py-2 border rounded-md text-muted-foreground text-xs">
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
                        <p className="text-muted-foreground text-sm">
                          {t("api_keys_empty")}
                        </p>
                      ) : (
                        apiKeys.map((key) => (
                          <div
                            key={key.id}
                            className="flex justify-between items-center px-3 py-2 border rounded-md"
                          >
                            <div className="min-w-0">
                              <p className="font-medium text-sm truncate">{key.name}</p>
                              <p className="text-muted-foreground text-xs">
                                {t("api_keys_prefix")}: {key.prefix}
                              </p>
                            </div>
                            <Button
                              variant="destructive"
                              size="sm"
                              onClick={() => onRevokeApiKey(key.id)}
                              disabled={Boolean(key.revoked_at)}
                            >
                              {key.revoked_at
                                ? t("api_keys_revoked")
                                : t("api_keys_revoke")}
                            </Button>
                          </div>
                        ))
                      )}
                    </div>
                  </div>
                </DialogContent>
              </Dialog>
            </div>
            <Button variant="destructive" onClick={onLogout}>
              {t("me_logout")}
            </Button>
          </CardContent>
        </Card>
      </div>
    </SettingsShell>
  )
}
