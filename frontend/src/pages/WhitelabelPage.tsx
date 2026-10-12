import { useEffect, useRef, useState } from "react"
import { useNavigate } from "@tanstack/react-router"

import { SettingsShell } from "@/components/SettingsShell"
import {
  SettingsPage,
  SettingsPageHeader,
  SettingsRow,
  SettingsSection,
} from "@/components/settings/SettingsPanel"
import { BrandLogo } from "@/components/BrandLogo"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { authApi, whitelabelApi } from "@/lib/api"
import { useBranding } from "@/lib/branding"
import { useI18n } from "@/lib/i18n-context"
import type { WhitelabelSettings } from "@/lib/types"

export const WhitelabelPage = () => {
  const navigate = useNavigate()
  const { t } = useI18n()
  const { invalidate } = useBranding()
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const [isSuperAdmin, setIsSuperAdmin] = useState(false)
  const [authChecked, setAuthChecked] = useState(false)
  const [settings, setSettings] = useState<WhitelabelSettings | null>(null)
  const [appTitle, setAppTitle] = useState("")
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await whitelabelApi.get()
      setSettings(data)
      setAppTitle(data.app_title ?? "")
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common_error"))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    authApi
      .me()
      .then((me) => {
        setIsSuperAdmin(me.is_super_admin)
        setAuthChecked(true)
      })
      .catch(() => {
        setAuthChecked(true)
        navigate({ to: "/settings/me" })
      })
  }, [navigate])

  useEffect(() => {
    if (!authChecked || !isSuperAdmin) return
    void load()
  }, [authChecked, isSuperAdmin])

  useEffect(() => {
    if (!authChecked) return
    if (!isSuperAdmin) {
      navigate({ to: "/settings/me" })
    }
  }, [authChecked, isSuperAdmin, navigate])

  const saveTitle = async () => {
    setSaving(true)
    setError(null)
    try {
      const updated = await whitelabelApi.update({
        app_title: appTitle.trim() || null,
      })
      setSettings(updated)
      await invalidate()
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common_error"))
    } finally {
      setSaving(false)
    }
  }

  const onLogoSelected = async (files: FileList | null) => {
    const file = files?.[0]
    if (!file) return
    setSaving(true)
    setError(null)
    try {
      const updated = await whitelabelApi.uploadLogo(file)
      setSettings(updated)
      await invalidate()
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common_error"))
    } finally {
      setSaving(false)
      if (fileInputRef.current) fileInputRef.current.value = ""
    }
  }

  const clearLogo = async () => {
    setSaving(true)
    setError(null)
    try {
      const updated = await whitelabelApi.clearLogo()
      setSettings(updated)
      await invalidate()
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common_error"))
    } finally {
      setSaving(false)
    }
  }

  if (!authChecked || !isSuperAdmin) {
    return (
      <SettingsShell title={t("whitelabel_title")}>
        <SettingsPage>
          <p className="text-muted-foreground text-sm">{t("common_loading")}</p>
        </SettingsPage>
      </SettingsShell>
    )
  }

  return (
    <SettingsShell title={t("whitelabel_title")}>
      <SettingsPage>
        <SettingsPageHeader title={t("whitelabel_title")} />
        {error ? <p className="text-destructive text-sm">{error}</p> : null}
        {loading && !settings ? (
          <p className="text-muted-foreground text-sm">{t("common_loading")}</p>
        ) : (
          <>
            <SettingsSection title={t("whitelabel_branding")}>
              <SettingsRow label={t("whitelabel_app_title")}>
                <div className="flex w-full max-w-xs flex-col gap-2 sm:items-end">
                  <Input
                    value={appTitle}
                    onChange={(event) => setAppTitle(event.target.value)}
                    placeholder={t("app_title")}
                    className="w-full"
                  />
                  <Button size="sm" onClick={() => void saveTitle()} disabled={saving}>
                    {t("common_save")}
                  </Button>
                </div>
              </SettingsRow>
              <SettingsRow label={t("whitelabel_logo")}>
                <div className="flex w-full max-w-xs flex-col gap-3 sm:items-end">
                  <BrandLogo className="size-16" />
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept="image/png,image/jpeg,image/webp,image/svg+xml,image/x-icon,.ico"
                    className="hidden"
                    onChange={(event) => void onLogoSelected(event.target.files)}
                  />
                  <div className="flex flex-wrap gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={saving}
                      onClick={() => fileInputRef.current?.click()}
                    >
                      {t("whitelabel_logo_upload")}
                    </Button>
                    {settings?.has_logo ? (
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={saving}
                        onClick={() => void clearLogo()}
                      >
                        {t("whitelabel_logo_reset")}
                      </Button>
                    ) : null}
                  </div>
                </div>
              </SettingsRow>
            </SettingsSection>
          </>
        )}
      </SettingsPage>
    </SettingsShell>
  )
}
