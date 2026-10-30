import { useEffect, useMemo, useState } from "react"
import { useNavigate } from "@tanstack/react-router"

import { SettingsShell } from "@/components/SettingsShell"
import { SettingsPage } from "@/components/settings/SettingsPanel"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { authApi, instanceProviderApi, ttsApi } from "@/lib/api"
import { useI18n, type TranslationKey } from "@/lib/i18n-context"
import type { InstanceProvider, TtsSettings, TtsVoice } from "@/lib/types"

type ProviderDraft = {
  api_key: string
  base_url: string
  endpoint: string
  config_json: string
  display_name: string
  is_enabled: boolean
}

type TtsDraft = {
  provider: string
  is_enabled: boolean
  api_key: string
  base_url: string
  region: string
  voice_id: string
  voice_label: string
  speed: string
}

const emptyDraft = (): ProviderDraft => ({
  api_key: "",
  base_url: "",
  endpoint: "",
  config_json: "",
  display_name: "",
  is_enabled: true,
})

const TTS_PROVIDER_LABEL: Record<string, TranslationKey> = {
  asya: "tts_provider_asya",
  openai: "tts_provider_openai",
  google: "tts_provider_google",
  elevenlabs: "tts_provider_elevenlabs",
  azure: "tts_provider_azure",
  browser: "tts_provider_browser",
}

const browserVoices = (): TtsVoice[] => {
  if (typeof window === "undefined" || !window.speechSynthesis) return []
  return window.speechSynthesis.getVoices().map((voice) => ({
    id: voice.voiceURI,
    name: `${voice.name} (${voice.lang})`,
    language: voice.lang,
  }))
}

export const InstanceProvidersPage = () => {
  const navigate = useNavigate()
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

  const [ttsSettings, setTtsSettings] = useState<TtsSettings | null>(null)
  const [ttsDraft, setTtsDraft] = useState<TtsDraft | null>(null)
  const [ttsVoices, setTtsVoices] = useState<TtsVoice[]>([])
  const [ttsVoicesLoading, setTtsVoicesLoading] = useState(false)
  const [ttsSaving, setTtsSaving] = useState(false)
  const [ttsError, setTtsError] = useState<string | null>(null)
  const [browserVoiceTick, setBrowserVoiceTick] = useState(0)

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

  const loadTts = async () => {
    setTtsError(null)
    try {
      const settings = await ttsApi.getSettings()
      setTtsSettings(settings)
      setTtsDraft({
        provider: settings.provider || "asya",
        is_enabled: settings.is_enabled,
        api_key: "",
        base_url: settings.base_url ?? (settings.provider === "asya" ? "https://ttsapi.asya.ai" : ""),
        region: settings.region ?? "",
        voice_id: settings.voice_id ?? "",
        voice_label: settings.voice_label ?? "",
        speed: String(settings.speed ?? 1),
      })
    } catch (err) {
      setTtsError(err instanceof Error ? err.message : t("common_error"))
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
    void loadTts()
  }, [authChecked, isSuperAdmin])

  useEffect(() => {
    if (!authChecked) return
    if (!isSuperAdmin) {
      navigate({ to: "/settings/me" })
    }
  }, [authChecked, isSuperAdmin, navigate])

  useEffect(() => {
    if (typeof window === "undefined" || !window.speechSynthesis) return
    const onVoices = () => setBrowserVoiceTick((value) => value + 1)
    window.speechSynthesis.addEventListener("voiceschanged", onVoices)
    onVoices()
    return () => window.speechSynthesis.removeEventListener("voiceschanged", onVoices)
  }, [])

  useEffect(() => {
    if (!ttsDraft) return
    if (ttsDraft.provider === "browser") {
      setTtsVoices(browserVoices())
      setTtsVoicesLoading(false)
      return
    }
    const needsKey =
      ttsDraft.provider === "asya" ||
      ttsDraft.provider === "elevenlabs" ||
      ttsDraft.provider === "azure" ||
      ttsDraft.provider === "google"
    const hasDraftKey = Boolean(ttsDraft.api_key.trim())
    const hasSavedKey =
      Boolean(ttsSettings?.api_key_set) && ttsDraft.provider === ttsSettings?.provider
    // OpenAI can fall back to instance LLM key; others need a draft or saved key.
    if (needsKey && !hasDraftKey && !hasSavedKey && ttsDraft.provider !== "openai") {
      setTtsVoices([])
      setTtsVoicesLoading(false)
      return
    }
    if (ttsDraft.provider === "azure" && !ttsDraft.region.trim() && !ttsDraft.base_url.trim()) {
      setTtsVoices([])
      setTtsVoicesLoading(false)
      return
    }

    let cancelled = false
    setTtsVoicesLoading(true)
    const timer = window.setTimeout(() => {
      ttsApi
        .listVoices({
          provider: ttsDraft.provider,
          ...(hasDraftKey ? { api_key: ttsDraft.api_key.trim() } : {}),
          base_url: ttsDraft.base_url.trim() || null,
          region: ttsDraft.region.trim() || null,
        })
        .then((voices) => {
          if (cancelled) return
          setTtsVoices(voices)
          if (voices.length === 0) {
            setTtsError(t("tts_voice_empty"))
          } else {
            setTtsError(null)
          }
        })
        .catch((err) => {
          if (cancelled) return
          setTtsVoices([])
          setTtsError(err instanceof Error ? err.message : t("common_error"))
        })
        .finally(() => {
          if (!cancelled) setTtsVoicesLoading(false)
        })
    }, 350)

    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [
    ttsDraft?.provider,
    ttsDraft?.api_key,
    ttsDraft?.base_url,
    ttsDraft?.region,
    ttsSettings?.api_key_set,
    ttsSettings?.provider,
    browserVoiceTick,
    t,
  ])

  const updateDraft = (provider: string, patch: Partial<ProviderDraft>) => {
    setDrafts((prev) => ({
      ...prev,
      [provider]: { ...(prev[provider] ?? emptyDraft()), ...patch },
    }))
  }

  const updateTtsDraft = (patch: Partial<TtsDraft>) => {
    setTtsDraft((prev) => (prev ? { ...prev, ...patch } : prev))
  }

  const saveProvider = async (item: InstanceProvider) => {
    const draft = drafts[item.provider] ?? emptyDraft()
    setError(null)
    try {
      const payload = {
        display_name: draft.display_name || item.provider,
        is_enabled: draft.is_enabled,
        ...(draft.api_key.trim() ? { api_key: draft.api_key.trim() } : {}),
        base_url: draft.base_url.trim() || null,
        endpoint: draft.endpoint.trim() || null,
        ...(draft.config_json.trim() ? { config_json: draft.config_json.trim() } : {}),
      }
      await instanceProviderApi.update(item.provider, payload)
      await loadProviders()
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common_error"))
    }
  }

  const addCustomProvider = async () => {
    setError(null)
    try {
      await instanceProviderApi.create({
        provider: newProvider.trim(),
        display_name: newDisplayName.trim() || null,
        provider_type: "openai_compatible",
        base_url: newBaseUrl.trim(),
        api_key: newApiKey.trim(),
        is_enabled: true,
      })
      setNewProvider("")
      setNewDisplayName("")
      setNewBaseUrl("")
      setNewApiKey("")
      await loadProviders()
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common_error"))
    }
  }

  const removeProvider = async (item: InstanceProvider) => {
    if (!window.confirm(t("instance_providers_remove_confirm"))) return
    setError(null)
    try {
      await instanceProviderApi.remove(item.provider)
      await loadProviders()
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common_error"))
    }
  }

  const saveTts = async () => {
    if (!ttsDraft) return
    setTtsError(null)
    if (ttsDraft.is_enabled && !ttsDraft.voice_id.trim()) {
      setTtsError(t("tts_voice_required"))
      return
    }
    const hasDraftKey = Boolean(ttsDraft.api_key.trim())
    const hasSavedKey = Boolean(ttsSettings?.api_key_set)
    if (
      ttsDraft.is_enabled &&
      ttsDraft.provider === "asya" &&
      !hasDraftKey &&
      !hasSavedKey
    ) {
      setTtsError(t("tts_api_key"))
      return
    }
    setTtsSaving(true)
    try {
      const selected = ttsVoices.find((voice) => voice.id === ttsDraft.voice_id)
      const settings = await ttsApi.updateSettings({
        provider: ttsDraft.provider,
        is_enabled: ttsDraft.is_enabled,
        ...(ttsDraft.api_key.trim() ? { api_key: ttsDraft.api_key.trim() } : {}),
        base_url: ttsDraft.base_url.trim() || null,
        region: ttsDraft.region.trim() || null,
        voice_id: ttsDraft.voice_id.trim() || null,
        voice_label: selected?.name || ttsDraft.voice_label || null,
        ...(ttsDraft.provider === "asya"
          ? { speed: Number.parseFloat(ttsDraft.speed) || 1 }
          : {}),
      })
      setTtsSettings(settings)
      setTtsDraft({
        provider: settings.provider || "asya",
        is_enabled: settings.is_enabled,
        api_key: "",
        base_url: settings.base_url ?? "",
        region: settings.region ?? "",
        voice_id: settings.voice_id ?? "",
        voice_label: settings.voice_label ?? "",
        speed: String(settings.speed ?? 1),
      })
    } catch (err) {
      setTtsError(err instanceof Error ? err.message : t("common_error"))
    } finally {
      setTtsSaving(false)
    }
  }

  const providerNote = useMemo(() => {
    const provider = ttsDraft?.provider
    if (provider === "browser") return t("tts_note_browser")
    if (provider === "asya") return t("tts_note_asya")
    if (provider === "azure") return t("tts_note_azure")
    if (provider === "google") return t("tts_note_google")
    if (provider === "openai" || provider === "elevenlabs") return t("tts_note_cloud")
    return null
  }, [t, ttsDraft?.provider])

  const showApiKey = ttsDraft && ttsDraft.provider !== "browser"
  const showBaseUrl =
    ttsDraft &&
    (ttsDraft.provider === "asya" ||
      ttsDraft.provider === "elevenlabs" ||
      ttsDraft.provider === "google" ||
      ttsDraft.provider === "openai" ||
      ttsDraft.provider === "azure")
  const showRegion = ttsDraft?.provider === "azure"
  const showAsyaSpeed = ttsDraft?.provider === "asya"
  const showKeyFallback =
    ttsDraft?.provider === "openai" || ttsDraft?.provider === "google"

  const builtinProviders = providers.filter((item) => item.provider_type === "builtin")
  const customProviders = providers.filter((item) => item.provider_type === "openai_compatible")

  const renderProviderForm = (item: InstanceProvider) => {
    const draft = drafts[item.provider] ?? emptyDraft()
    const isVertex = item.provider === "vertex"
    const isAzure = item.provider === "azure"

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
            {item.is_configured ? (
              <Button variant="outline" onClick={() => removeProvider(item)}>
                {t("instance_providers_remove")}
              </Button>
            ) : null}
          </div>
        </CardContent>
      </Card>
    )
  }

  if (!authChecked || !isSuperAdmin) {
    return null
  }

  return (
    <SettingsShell title={t("instance_providers_title")}>
      <SettingsPage wide>
        <div className="space-y-6">
          {error ? <p className="text-sm text-destructive">{error}</p> : null}
          {loading && providers.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("diagnosis_checking")}</p>
          ) : null}

          <div className="space-y-4">
            <h2 className="text-base font-medium">{t("tts_section")}</h2>
            <Card>
              <CardContent className="space-y-3 pt-6">
                {ttsError ? <p className="text-sm text-destructive">{ttsError}</p> : null}
                {ttsDraft ? (
                  <>
                    <div className="flex items-center justify-between gap-3">
                      <span className="text-sm">{t("tts_enabled")}</span>
                      <Switch
                        checked={ttsDraft.is_enabled}
                        onCheckedChange={(checked) => updateTtsDraft({ is_enabled: checked })}
                      />
                    </div>
                    <div className="space-y-1.5">
                      <span className="text-sm">{t("tts_provider")}</span>
                      <Select
                        value={ttsDraft.provider}
                        onValueChange={(value) =>
                          updateTtsDraft({
                            provider: value,
                            voice_id: "",
                            voice_label: "",
                            base_url:
                              value === "asya"
                                ? ttsDraft.base_url || "https://ttsapi.asya.ai"
                                : value === "browser"
                                  ? ""
                                  : ttsDraft.base_url,
                          })
                        }
                      >
                        <SelectTrigger className="w-full">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {(ttsSettings?.providers ?? Object.keys(TTS_PROVIDER_LABEL)).map(
                            (provider) => (
                              <SelectItem key={provider} value={provider}>
                                {t(TTS_PROVIDER_LABEL[provider] ?? "tts_provider")}
                              </SelectItem>
                            )
                          )}
                        </SelectContent>
                      </Select>
                      {providerNote ? (
                        <p className="text-xs text-muted-foreground">{providerNote}</p>
                      ) : null}
                    </div>
                    {showApiKey ? (
                      <div className="space-y-1.5">
                        <Input
                          type="password"
                          placeholder={
                            ttsSettings?.api_key_set
                              ? t("org_provider_override_set")
                              : t("tts_api_key")
                          }
                          value={ttsDraft.api_key}
                          onChange={(event) => updateTtsDraft({ api_key: event.target.value })}
                        />
                        {showKeyFallback ? (
                          <p className="text-xs text-muted-foreground">{t("tts_api_key_fallback")}</p>
                        ) : null}
                      </div>
                    ) : null}
                    {showBaseUrl ? (
                      <Input
                        placeholder={t("tts_base_url")}
                        value={ttsDraft.base_url}
                        onChange={(event) => updateTtsDraft({ base_url: event.target.value })}
                      />
                    ) : null}
                    {showRegion ? (
                      <Input
                        placeholder={t("tts_region")}
                        value={ttsDraft.region}
                        onChange={(event) => updateTtsDraft({ region: event.target.value })}
                      />
                    ) : null}
                    {showAsyaSpeed ? (
                      <div className="space-y-1.5">
                        <span className="text-sm">{t("tts_speed")}</span>
                        <Input
                          type="number"
                          min={0.5}
                          max={2}
                          step={0.05}
                          value={ttsDraft.speed}
                          onChange={(event) => updateTtsDraft({ speed: event.target.value })}
                        />
                      </div>
                    ) : null}
                    <div className="space-y-1.5">
                      <span className="text-sm">{t("tts_voice")}</span>
                      <Select
                        value={ttsDraft.voice_id || undefined}
                        onValueChange={(value) => {
                          const selected = ttsVoices.find((voice) => voice.id === value)
                          updateTtsDraft({
                            voice_id: value,
                            voice_label: selected?.name ?? "",
                          })
                        }}
                      >
                        <SelectTrigger className="w-full">
                          <SelectValue placeholder={t("tts_voice_placeholder")} />
                        </SelectTrigger>
                        <SelectContent>
                          {ttsVoicesLoading ? (
                            <SelectItem value="__loading" disabled>
                              {t("tts_voice_loading")}
                            </SelectItem>
                          ) : null}
                          {ttsVoices.map((voice) => (
                            <SelectItem key={voice.id} value={voice.id}>
                              {voice.name}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <Button onClick={() => void saveTts()} disabled={ttsSaving}>
                      {t("tts_save")}
                    </Button>
                  </>
                ) : (
                  <p className="text-sm text-muted-foreground">{t("diagnosis_checking")}</p>
                )}
              </CardContent>
            </Card>
          </div>

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
      </SettingsPage>
    </SettingsShell>
  )
}
