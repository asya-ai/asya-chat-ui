import { useEffect, useState } from "react"
import { useLocation, useNavigate } from "@tanstack/react-router"

import { SettingsShell } from "@/components/SettingsShell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { authApi, instanceProviderApi } from "@/lib/api"
import { useI18n } from "@/lib/i18n-context"
import type { InstanceProvider } from "@/lib/types"

type ProviderDraft = {
  api_key: string
  base_url: string
  endpoint: string
  config_json: string
  display_name: string
  is_enabled: boolean
}

const emptyDraft = (): ProviderDraft => ({
  api_key: "",
  base_url: "",
  endpoint: "",
  config_json: "",
  display_name: "",
  is_enabled: true,
})

export const InstanceProvidersPage = () => {
  const navigate = useNavigate()
  const location = useLocation()
  const { t } = useI18n()
  const [isSuperAdmin, setIsSuperAdmin] = useState(false)
  const [authChecked, setAuthChecked] = useState(false)
  const [providers, setProviders] = useState<InstanceProvider[]>([])
  const [drafts, setDrafts] = useState<Record<string, ProviderDraft>>({})
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [newProvider, setNewProvider] = useState("")
  const [newDisplayName, setNewDisplayName] = useState("")
  const [newBaseUrl, setNewBaseUrl] = useState("")
  const [newApiKey, setNewApiKey] = useState("")

  const loadProviders = async () => {
    setLoading(true)
    setError(null)
    try {
      const list = await instanceProviderApi.list()
      setProviders(list)
      setDrafts((prev) => {
        const next = { ...prev }
        for (const item of list) {
            next[item.provider] = {
              api_key: "",
              base_url: item.base_url ?? "",
              endpoint: item.endpoint ?? "",
              config_json: "",
              display_name: item.display_name ?? item.provider,
              is_enabled: item.is_enabled,
            }
        }
        return next
      })
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
    void loadProviders()
  }, [authChecked, isSuperAdmin])

  useEffect(() => {
    if (!authChecked) return
    if (!isSuperAdmin) {
      navigate({ to: "/settings/me" })
    }
  }, [authChecked, isSuperAdmin, navigate])

  const updateDraft = (provider: string, patch: Partial<ProviderDraft>) => {
    setDrafts((prev) => ({
      ...prev,
      [provider]: { ...(prev[provider] ?? emptyDraft()), ...patch },
    }))
  }

  const saveProvider = async (item: InstanceProvider) => {
    const draft = drafts[item.provider] ?? emptyDraft()
    setError(null)
    try {
      const payload = {
        display_name: draft.display_name || item.provider,
        is_enabled: draft.is_enabled,
        api_key: draft.api_key || undefined,
        base_url: draft.base_url || undefined,
        endpoint: draft.endpoint || undefined,
        config_json: draft.config_json || undefined,
      }
      const updated = item.is_configured
        ? await instanceProviderApi.update(item.provider, payload)
        : await instanceProviderApi.create({
            provider: item.provider,
            provider_type: item.provider_type,
            ...payload,
          })
      setProviders((prev) => prev.map((p) => (p.provider === updated.provider ? updated : p)))
      updateDraft(item.provider, { api_key: "", config_json: "" })
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common_save_failed"))
    }
  }

  const addCustomProvider = async () => {
    const provider = newProvider.trim().toLowerCase()
    if (!provider || !newBaseUrl.trim() || !newApiKey.trim()) return
    setError(null)
    try {
      await instanceProviderApi.create({
        provider,
        provider_type: "openai_compatible",
        display_name: newDisplayName.trim() || provider,
        base_url: newBaseUrl.trim(),
        api_key: newApiKey.trim(),
      })
      setNewProvider("")
      setNewDisplayName("")
      setNewBaseUrl("")
      setNewApiKey("")
      await loadProviders()
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common_save_failed"))
    }
  }

  const removeCustomProvider = async (provider: string) => {
    setError(null)
    try {
      await instanceProviderApi.remove(provider)
      await loadProviders()
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common_error"))
    }
  }

  const navItems = [
    { label: t("me_settings"), href: "/settings/me" },
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
  ]

  const builtinProviders = providers.filter((item) => item.provider_type === "builtin")
  const customProviders = providers.filter((item) => item.provider_type === "openai_compatible")

  const renderProviderForm = (item: InstanceProvider) => {
    const draft = drafts[item.provider] ?? emptyDraft()
    const isVertex = item.provider === "vertex"
    const isAzure = item.provider === "azure"
    const isCustom = item.provider_type === "openai_compatible"

    return (
      <Card key={item.provider}>
        <CardHeader className="flex flex-row items-center justify-between gap-3 pb-3">
          <div>
            <CardTitle className="text-lg font-medium">{item.display_name || item.provider}</CardTitle>
            <p className="font-mono text-sm text-muted-foreground">{item.provider}</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {item.migrated_from_env ? (
              <Badge variant="secondary">{t("instance_providers_migrated_badge")}</Badge>
            ) : null}
            <Badge variant={item.is_configured ? "outline" : "secondary"}>
              {item.is_configured
                ? t("instance_providers_configured")
                : t("instance_providers_not_configured")}
            </Badge>
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex items-center justify-between gap-3">
            <span className="text-sm">{t("instance_providers_enabled")}</span>
            <Switch
              checked={draft.is_enabled}
              onCheckedChange={(checked) => updateDraft(item.provider, { is_enabled: checked })}
            />
          </div>
          {!isVertex ? (
            <Input
              type="password"
              placeholder={
                item.api_key_set
                  ? t("org_provider_override_set")
                  : t("instance_providers_api_key_placeholder")
              }
              value={draft.api_key}
              onChange={(event) => updateDraft(item.provider, { api_key: event.target.value })}
            />
          ) : null}
          {isVertex ? (
            <Textarea
              placeholder={
                item.config_json_set
                  ? t("org_provider_override_set")
                  : t("org_provider_vertex_config_placeholder")
              }
              value={draft.config_json}
              onChange={(event) => updateDraft(item.provider, { config_json: event.target.value })}
              className="h-28 font-mono text-xs"
            />
          ) : isAzure ? (
            <Input
              placeholder={t("org_provider_endpoint")}
              value={draft.endpoint}
              onChange={(event) => updateDraft(item.provider, { endpoint: event.target.value })}
            />
          ) : (
            <Input
              placeholder={t("org_provider_base_url")}
              value={draft.base_url}
              onChange={(event) => updateDraft(item.provider, { base_url: event.target.value })}
            />
          )}
          <div className="flex gap-2">
            <Button onClick={() => saveProvider(item)}>{t("common_save")}</Button>
            {isCustom && item.is_configured ? (
              <Button variant="outline" onClick={() => removeCustomProvider(item.provider)}>
                {t("common_delete")}
              </Button>
            ) : null}
          </div>
        </CardContent>
      </Card>
    )
  }

  return (
    <SettingsShell title={t("instance_providers_title")} items={navItems}>
      <div className="space-y-6">
        <p className="text-sm text-muted-foreground">{t("instance_providers_description")}</p>
        {error ? <p className="text-sm text-destructive">{error}</p> : null}
        {loading && providers.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("diagnosis_checking")}</p>
        ) : null}

        <div className="space-y-4">
          <h2 className="text-base font-medium">{t("instance_providers_builtin_section")}</h2>
          {builtinProviders.map(renderProviderForm)}
        </div>

        {customProviders.length > 0 ? (
          <div className="space-y-4">
            <h2 className="text-base font-medium">{t("instance_providers_custom_section")}</h2>
            {customProviders.map(renderProviderForm)}
          </div>
        ) : null}

        <Card>
          <CardHeader>
            <CardTitle className="text-lg font-medium">{t("instance_providers_add_custom")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="grid gap-3 md:grid-cols-2">
              <Input
                placeholder={t("instance_providers_custom_id_placeholder")}
                value={newProvider}
                onChange={(event) => setNewProvider(event.target.value)}
              />
              <Input
                placeholder={t("instance_providers_custom_name_placeholder")}
                value={newDisplayName}
                onChange={(event) => setNewDisplayName(event.target.value)}
              />
              <Input
                placeholder={t("org_provider_base_url")}
                value={newBaseUrl}
                onChange={(event) => setNewBaseUrl(event.target.value)}
              />
              <Input
                type="password"
                placeholder={t("instance_providers_api_key_placeholder")}
                value={newApiKey}
                onChange={(event) => setNewApiKey(event.target.value)}
              />
            </div>
            <Button
              onClick={addCustomProvider}
              disabled={!newProvider.trim() || !newBaseUrl.trim() || !newApiKey.trim()}
            >
              {t("instance_providers_add_custom")}
            </Button>
          </CardContent>
        </Card>
      </div>
    </SettingsShell>
  )
}
