import { useEffect, useState } from "react"
import { useNavigate } from "@tanstack/react-router"

import { apiKeyApi, authApi, memoryApi } from "@/lib/api"
import { useUsageLimits } from "@/hooks/use-chat-query"
import { useAuth } from "@/lib/auth-context"
import { useI18n, type TranslationKey } from "@/lib/i18n-context"
import { isValidPassword } from "@/lib/password"
import { LanguageSelect } from "@/components/LanguageSelect"
import {
  SettingsActionRow,
  SettingsControl,
  SettingsPage,
  SettingsRow,
  SettingsSection,
} from "@/components/settings/SettingsPanel"
import { SettingsShell } from "@/components/SettingsShell"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
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
import { getTheme, setTheme, type ThemeMode } from "@/lib/theme"
import { usePwaInstall } from "@/lib/use-pwa-install"
import type { ApiKey, UserMemory } from "@/lib/types"
import { Check, Pencil, Trash2, X } from "lucide-react"
import {
  actionInfoLevelStore,
  orgStore,
  type ActionInfoLevel,
} from "@/lib/storage"

export const MePage = () => {
  const navigate = useNavigate()
  const { clearToken } = useAuth()
  const { t, locale } = useI18n()
  const orgId = orgStore.get()
  const { data: usageLimits } = useUsageLimits(orgId)
  const {
    installed: pwaInstalled,
    canPromptInstall,
    platform,
    needsHttps,
    install: installPwa,
  } = usePwaInstall()
  const [theme, setThemeState] = useState<ThemeMode>(getTheme())
  const [installingPwa, setInstallingPwa] = useState(false)
  const [installHelpOpen, setInstallHelpOpen] = useState(false)
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

  const onThemeChange = (mode: ThemeMode) => {
    setThemeState(mode)
    setTheme(mode)
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

  const formatCost = (value: number) =>
    new Intl.NumberFormat(locale ?? "en", {
      style: "currency",
      currency: "USD",
      minimumFractionDigits: value > 0 && value < 0.01 ? 4 : 2,
      maximumFractionDigits: value > 0 && value < 0.01 ? 4 : 2,
    }).format(value)

  const userUsageLimit = usageLimits?.user
  const showUsageLimit =
    userUsageLimit?.limit_usd != null && userUsageLimit.limit_usd !== undefined

  const onInstallApp = () => {
    if (canPromptInstall) {
      setInstallingPwa(true)
      void installPwa().finally(() => setInstallingPwa(false))
      return
    }
    setInstallHelpOpen(true)
  }

  return (
    <SettingsShell title={t("me_title")}>
      <SettingsPage>
        <SettingsSection title={t("me_general")}>
          <SettingsRow label={t("language")}>
            <SettingsControl>
              <LanguageSelect triggerClassName="w-full" />
            </SettingsControl>
          </SettingsRow>
          <SettingsRow label={t("theme_title")}>
            <SettingsControl>
              <Select value={theme} onValueChange={(value) => onThemeChange(value as ThemeMode)}>
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="light">{t("theme_light")}</SelectItem>
                  <SelectItem value="dark">{t("theme_dark")}</SelectItem>
                </SelectContent>
              </Select>
            </SettingsControl>
          </SettingsRow>
          {showInstallSection ? (
            <SettingsRow label={t("me_install_app")}>
              <SettingsControl>
                <Button
                  variant="outline"
                  className="w-full"
                  disabled={installingPwa}
                  onClick={onInstallApp}
                >
                  {canPromptInstall ? t("me_install_app") : t("me_install_app_help")}
                </Button>
              </SettingsControl>
            </SettingsRow>
          ) : null}
          <SettingsRow label={t("me_action_info")}>
            <SettingsControl>
              <Select
                value={actionInfoLevel}
                onValueChange={(value) => onActionInfoLevelChange(value as ActionInfoLevel)}
              >
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">{t("me_action_info_none")}</SelectItem>
                  <SelectItem value="detailed">{t("me_action_info_detailed")}</SelectItem>
                </SelectContent>
              </Select>
            </SettingsControl>
          </SettingsRow>
          <SettingsRow label={t("me_memory_enabled")}>
            <Switch checked={memoryEnabled} onCheckedChange={onToggleMemory} />
          </SettingsRow>
          {memoryEnabled ? (
            <SettingsActionRow
              label={t("me_memory_see")}
              onClick={() => setMemoriesOpen(true)}
            />
          ) : null}
        </SettingsSection>

        {showUsageLimit && userUsageLimit ? (
          <SettingsSection title={t("me_usage_limit")} description={t("me_usage_limit_desc")}>
            <SettingsRow label={t("org_usage_limit_title")}>
              <SettingsControl>
                <p className="text-sm tabular-nums">
                  {t("me_usage_limit_used", {
                    used: formatCost(userUsageLimit.used_usd),
                    limit: formatCost(userUsageLimit.limit_usd!),
                    percent: Math.round(userUsageLimit.percent_used ?? 0),
                  })}
                </p>
              </SettingsControl>
            </SettingsRow>
          </SettingsSection>
        ) : null}

        <SettingsSection title={t("me_account")}>
          <Dialog open={passwordOpen} onOpenChange={onPasswordOpenChange}>
            <DialogTrigger asChild>
              <SettingsActionRow label={t("me_change_password")} />
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
              <SettingsActionRow label={t("api_keys_manage")} />
            </DialogTrigger>
            <DialogContent className="max-w-2xl">
              <DialogHeader>
                <DialogTitle>{t("api_keys_title")}</DialogTitle>
              </DialogHeader>
              <div className="space-y-4">
                <div className="rounded-md border px-3 py-2 text-muted-foreground text-xs">
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
                    <p className="text-muted-foreground text-sm">{t("api_keys_empty")}</p>
                  ) : (
                    apiKeys.map((key) => (
                      <div
                        key={key.id}
                        className="flex items-center justify-between rounded-md border px-3 py-2"
                      >
                        <div className="min-w-0">
                          <p className="truncate text-sm font-medium">{key.name}</p>
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
                          {key.revoked_at ? t("api_keys_revoked") : t("api_keys_revoke")}
                        </Button>
                      </div>
                    ))
                  )}
                </div>
              </div>
            </DialogContent>
          </Dialog>

          <SettingsActionRow
            label={t("me_logout")}
            destructive
            onClick={onLogout}
          />
        </SettingsSection>

        {showInstallSection ? (
          <Dialog open={installHelpOpen} onOpenChange={setInstallHelpOpen}>
            <DialogContent className="max-w-md">
              <DialogHeader>
                <DialogTitle>{t("me_install_app_help_title")}</DialogTitle>
              </DialogHeader>
              <p className="text-muted-foreground text-sm leading-6">{t(installHintKey)}</p>
            </DialogContent>
          </Dialog>
        ) : null}

        <Dialog open={memoriesOpen} onOpenChange={setMemoriesOpen}>
          <DialogContent className="max-w-2xl">
            <DialogHeader>
              <DialogTitle>{t("me_memory_title")}</DialogTitle>
            </DialogHeader>
            <div className="max-h-[60vh] space-y-2 overflow-y-auto">
              {memories.length === 0 ? (
                <p className="text-muted-foreground text-sm">{t("me_memory_empty")}</p>
              ) : (
                memories.map((memory) => (
                  <div
                    key={memory.id}
                    className="flex items-start gap-2 rounded-md border px-3 py-2 text-sm"
                  >
                    {editingMemoryId === memory.id ? (
                      <div className="flex flex-1 items-center gap-2">
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
                        <span className="min-w-0 flex-1 wrap-break-word">{memory.content}</span>
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
      </SettingsPage>
    </SettingsShell>
  )
}
