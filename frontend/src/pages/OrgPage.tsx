import { useEffect, useMemo, useState } from "react"
import { useLocation, useNavigate } from "react-router-dom"

import { authApi, modelApi, orgApi } from "@/lib/api"
import { orgStore } from "@/lib/storage"
import { useI18n } from "@/lib/i18n-context"
import { SettingsShell } from "@/components/SettingsShell"
import type {
  ChatModel,
  Invite,
  ModelSuggestionProvider,
  Org,
  OrgMember,
  OrgWebSettings,
  ProviderConfig,
} from "@/lib/types"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { ArrowDown, ArrowUp, Image } from "lucide-react"

type SettingsSection = "orgs" | "users" | "models"

const PROVIDERS = ["openai", "azure", "gemini", "groq", "anthropic"] as const

export const OrgPage = () => {
  const navigate = useNavigate()
  const location = useLocation()
  const [activeSection, setActiveSection] = useState<SettingsSection>("orgs")
  const [orgs, setOrgs] = useState<Org[]>([])
  const [models, setModels] = useState<ChatModel[]>([])
  const [members, setMembers] = useState<OrgMember[]>([])
  const [invites, setInvites] = useState<Invite[]>([])
  const [providerConfigs, setProviderConfigs] = useState<ProviderConfig[]>([])
  const [webSettings, setWebSettings] = useState<OrgWebSettings | null>(null)
  const [accessEnabledIds, setAccessEnabledIds] = useState<string[]>([])
  const [name, setName] = useState("")
  const [inviteEmail, setInviteEmail] = useState("")
  const [modelProvider, setModelProvider] = useState("openai")
  const [modelName, setModelName] = useState("")
  const [modelDisplayName, setModelDisplayName] = useState("")
  const [modelReasoningEffort, setModelReasoningEffort] = useState("none")
  const { t } = useI18n()
  const [selectedOrg, setSelectedOrg] = useState<string | null>(orgStore.get())
  const [orgSettingsId, setOrgSettingsId] = useState<string | null>(orgStore.get())
  const [orgSettingsName, setOrgSettingsName] = useState("")
  const [usersOrgId, setUsersOrgId] = useState<string | null>(orgStore.get())
  const [modelsOrgId, setModelsOrgId] = useState<string | null>(orgStore.get())
  const [isSuperAdmin, setIsSuperAdmin] = useState(false)
  const [isAdmin, setIsAdmin] = useState(false)
  const [authChecked, setAuthChecked] = useState(false)
  const [currentUserId, setCurrentUserId] = useState<string | null>(null)
  const [editingModelId, setEditingModelId] = useState<string | null>(null)
  const [editingName, setEditingName] = useState("")
  const [suggestions, setSuggestions] = useState<ModelSuggestionProvider[]>([])
  const [error, setError] = useState<string | null>(null)
  const [renameOrgId, setRenameOrgId] = useState<string | null>(null)
  const [renameOrgName, setRenameOrgName] = useState("")
  const [deleteOrgId, setDeleteOrgId] = useState<string | null>(null)

  const isImageModel = (model: ChatModel) => {
    if (model.supports_image_output === true) return true
    if (model.supports_image_output === false) return false
    const name = `${model.display_name} ${model.model_name}`.toLowerCase()
    return name.includes("image")
  }

  const orderedModels = useMemo(() => {
    return [...models].sort((a, b) => {
      const orderA = a.display_order ?? 0
      const orderB = b.display_order ?? 0
      if (orderA !== orderB) return orderA - orderB
      return a.display_name.localeCompare(b.display_name)
    })
  }, [models])

  const moveModel = async (modelId: string, direction: -1 | 1) => {
    if (!isSuperAdmin) return
    const ordered = [...orderedModels]
    const index = ordered.findIndex((model) => model.id === modelId)
    const nextIndex = index + direction
    if (index < 0 || nextIndex < 0 || nextIndex >= ordered.length) return
    const swapped = [...ordered]
    ;[swapped[index], swapped[nextIndex]] = [swapped[nextIndex], swapped[index]]
    const next = swapped.map((model, idx) => ({
      ...model,
      display_order: idx + 1,
    }))
    setModels(next)
    try {
      await modelApi.updateOrder(
        next.map((model) => ({
          model_id: model.id,
          display_order: model.display_order ?? 0,
        }))
      )
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common_save_failed"))
    }
  }

  const selectOrg = (orgId: string | null) => {
    if (orgId) {
      orgStore.set(orgId)
    } else {
      orgStore.clear()
    }
    setSelectedOrg(orgId)
    setOrgSettingsId(orgId)
    setUsersOrgId(orgId)
    setModelsOrgId(orgId)
  }

  const loadOrgs = async () => {
    const data = await orgApi.list()
    setOrgs(data)
    if (data.length > 0) {
      const firstId = data[0].id
      const storedId = orgStore.get()
      const nextId = storedId && data.some((org) => org.id === storedId) ? storedId : firstId
      selectOrg(nextId)
    } else {
      selectOrg(null)
    }
  }

  useEffect(() => {
    loadOrgs().catch((err) =>
      setError(err instanceof Error ? err.message : t("common_load_failed"))
    )
    authApi
      .me()
      .then((me) => {
        setIsSuperAdmin(me.is_super_admin)
        setIsAdmin(me.is_admin)
        setCurrentUserId(me.id)
        setAuthChecked(true)
      })
      .catch(() => {
        setIsSuperAdmin(false)
        setIsAdmin(false)
        setCurrentUserId(null)
        setAuthChecked(true)
      })
  }, [])

  useEffect(() => {
    if (!authChecked) return
    if (!isSuperAdmin && !isAdmin) {
      navigate("/settings/me")
      return
    }
    if (!isSuperAdmin && location.pathname !== "/settings/users") {
      navigate("/settings/users")
    }
  }, [authChecked, isAdmin, isSuperAdmin, navigate, location.pathname])

  useEffect(() => {
    const path = location.pathname
    if (!isSuperAdmin) {
      setActiveSection("users")
    } else if (path.startsWith("/settings/users")) {
      setActiveSection("users")
    } else if (path.startsWith("/settings/models")) {
      setActiveSection("models")
    } else if (path.startsWith("/settings/organisation")) {
      setActiveSection("orgs")
    } else if (path.startsWith("/settings/organisations")) {
      setActiveSection("orgs")
    }
  }, [location.pathname, isSuperAdmin])

  useEffect(() => {
    if (!isSuperAdmin) return
    modelApi
      .suggestions()
      .then(setSuggestions)
      .catch(() => setSuggestions([]))
  }, [isSuperAdmin])

  useEffect(() => {
    if (!selectedOrg) return
    if (isSuperAdmin) {
      modelApi.list().then(setModels).catch(() => null)
    } else {
      modelApi.list(selectedOrg).then(setModels).catch(() => null)
    }
  }, [selectedOrg, isSuperAdmin])

  useEffect(() => {
    if (!isSuperAdmin || !modelsOrgId) return
    modelApi
      .list(modelsOrgId)
      .then((orgModels) => setAccessEnabledIds(orgModels.map((model) => model.id)))
      .catch(() => setAccessEnabledIds([]))
  }, [modelsOrgId, isSuperAdmin])

  useEffect(() => {
    if (!usersOrgId) return
    orgApi
      .members(usersOrgId)
      .then(setMembers)
      .catch(() => setMembers([]))
    authApi
      .invites(usersOrgId)
      .then(setInvites)
      .catch(() => setInvites([]))
  }, [usersOrgId])

  useEffect(() => {
    if (!orgSettingsId) return
    const selected = orgs.find((org) => org.id === orgSettingsId)
    setOrgSettingsName(selected?.name ?? "")
    orgApi
      .providers(orgSettingsId)
      .then((configs) =>
        setProviderConfigs(
          configs.map((config) => ({
            ...config,
            api_key_override: "",
          }))
        )
      )
      .catch(() => setProviderConfigs([]))
    orgApi
      .webSettings(orgSettingsId)
      .then(setWebSettings)
      .catch(() => setWebSettings(null))
  }, [orgSettingsId, orgs])

  const createOrg = async () => {
    setError(null)
    const org = await orgApi.create(name)
    setName("")
    setOrgs((prev) => [...prev, org])
  }

  const sendInvite = async () => {
    if (!usersOrgId) return
    await authApi.createInvite(usersOrgId, inviteEmail)
    const updated = await authApi.invites(usersOrgId)
    setInvites(updated)
    setInviteEmail("")
  }

  const resendInvite = async (inviteId: string) => {
    if (!usersOrgId) return
    await authApi.resendInvite(inviteId)
    const updated = await authApi.invites(usersOrgId)
    setInvites(updated)
  }

  const cancelInvite = async (inviteId: string) => {
    if (!usersOrgId) return
    await authApi.cancelInvite(inviteId)
    setInvites((prev) => prev.filter((invite) => invite.id !== inviteId))
  }

  const copyInviteLink = async (invite: Invite) => {
    const url = `${window.location.origin}/invite?token=${invite.token}`
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(url)
    } else {
      const textarea = document.createElement("textarea")
      textarea.value = url
      textarea.style.position = "fixed"
      textarea.style.left = "-9999px"
      document.body.appendChild(textarea)
      textarea.select()
      document.execCommand("copy")
      document.body.removeChild(textarea)
    }
  }

  const updateMemberRole = async (member: OrgMember, nextRole: string) => {
    if (!usersOrgId) return
    const updated = await orgApi.updateMemberRole(usersOrgId, member.user_id, nextRole)
    setMembers((prev) =>
      prev.map((item) => (item.user_id === updated.user_id ? updated : item))
    )
  }

  const updateMemberSuperAdmin = async (member: OrgMember, nextValue: boolean) => {
    if (!isSuperAdmin) return
    const updated = await authApi.updateSuperAdmin(member.user_id, nextValue)
    setMembers((prev) =>
      prev.map((item) =>
        item.user_id === member.user_id
          ? { ...item, is_super_admin: updated.is_super_admin }
          : item
      )
    )
  }

  const createModel = async () => {
    if (!selectedOrg) return
    const matched = modelOptions.find((model) => model.model_name === modelName)
    const model = await modelApi.create({
      org_id: selectedOrg,
      provider: modelProvider,
      model_name: modelName,
      display_name: modelDisplayName || modelName,
      context_length: matched?.context_length ?? null,
      supports_image_input: matched?.supports_image_input ?? null,
      supports_image_output: matched?.supports_image_output ?? null,
      reasoning_effort: modelReasoningEffort,
      is_active: true,
    })
    setModels((prev) => [...prev, model])
    if (modelsOrgId) {
      setAccessEnabledIds((prev) => Array.from(new Set([...prev, model.id])))
    }
    setModelName("")
    setModelDisplayName("")
    setModelReasoningEffort("none")
  }

  const removeModel = async (modelId: string) => {
    await modelApi.remove(modelId)
    setModels((prev) => prev.filter((model) => model.id !== modelId))
    setAccessEnabledIds((prev) => prev.filter((id) => id !== modelId))
  }

  const startRename = (model: ChatModel) => {
    setEditingModelId(model.id)
    setEditingName(model.display_name)
  }

  const cancelRename = () => {
    setEditingModelId(null)
    setEditingName("")
  }

  const saveRename = async (modelId: string) => {
    const trimmed = editingName.trim()
    if (!trimmed) return
    const updated = await modelApi.rename(modelId, trimmed)
    setModels((prev) =>
      prev.map((model) =>
        model.id === modelId ? { ...model, display_name: updated.display_name } : model
      )
    )
    cancelRename()
  }

  const updateReasoningEffort = async (modelId: string, value: string) => {
    const updated = await modelApi.update(modelId, { reasoning_effort: value })
    setModels((prev) =>
      prev.map((model) =>
        model.id === modelId ? { ...model, reasoning_effort: updated.reasoning_effort } : model
      )
    )
  }

  const toggleModelAccess = async (modelId: string) => {
    if (!modelsOrgId) return
    const enabled = accessEnabledIds.includes(modelId)
    await modelApi.setOrgModels(modelsOrgId, [{ model_id: modelId, is_enabled: !enabled }])
    setAccessEnabledIds((prev) =>
      enabled ? prev.filter((id) => id !== modelId) : [...prev, modelId]
    )
  }

  const providerOptions = useMemo(() => {
    const fromSuggestions = suggestions.map((provider) => provider.provider)
    return fromSuggestions.length > 0 ? fromSuggestions : [...PROVIDERS]
  }, [suggestions])

  const reasoningOptions = ["none", "low", "medium", "high"]
  const reasoningLabel = (value: string) => {
    switch (value) {
      case "low":
        return t("org_reasoning_low")
      case "medium":
        return t("org_reasoning_medium")
      case "high":
        return t("org_reasoning_high")
      default:
        return t("org_reasoning_none")
    }
  }

  const modelOptions = useMemo(
    () => suggestions.find((provider) => provider.provider === modelProvider)?.models ?? [],
    [modelProvider, suggestions]
  )

  const sectionTitle = useMemo(() => {
    switch (activeSection) {
      case "users":
        return t("org_section_users")
      case "models":
        return t("org_section_models")
      default:
        return t("org_section_orgs")
    }
  }, [activeSection, t])

  const canManageOrgSettings = isSuperAdmin || isAdmin
  const roleOptions = ["admin", "member"]
  const roleLabel = (value: string) =>
    value === "admin" ? t("org_role_admin") : t("org_role_member")

  const openRenameDialog = (org: Org) => {
    setRenameOrgId(org.id)
    setRenameOrgName(org.name)
  }

  const closeRenameDialog = () => {
    setRenameOrgId(null)
    setRenameOrgName("")
  }

  const saveOrgRename = async () => {
    if (!renameOrgId || !renameOrgName.trim()) return
    const updated = await orgApi.update(renameOrgId, { name: renameOrgName.trim() })
    setOrgs((prev) => prev.map((org) => (org.id === updated.id ? updated : org)))
    if (orgSettingsId === updated.id) {
      setOrgSettingsName(updated.name)
    }
    closeRenameDialog()
  }

  const toggleOrgFrozen = async (org: Org) => {
    const updated = await orgApi.update(org.id, { is_frozen: !org.is_frozen })
    setOrgs((prev) => prev.map((item) => (item.id === updated.id ? updated : item)))
  }

  const openDeleteDialog = (orgId: string) => {
    setDeleteOrgId(orgId)
  }

  const closeDeleteDialog = () => {
    setDeleteOrgId(null)
  }

  const confirmDeleteOrg = async () => {
    if (!deleteOrgId) return
    await orgApi.remove(deleteOrgId)
    setOrgs((prev) => prev.filter((org) => org.id !== deleteOrgId))
    if (selectedOrg === deleteOrgId) {
      orgStore.clear()
      setSelectedOrg(null)
    }
    closeDeleteDialog()
  }

  const saveOrgSettingsName = async () => {
    if (!orgSettingsId || !orgSettingsName.trim()) return
    const updated = await orgApi.update(orgSettingsId, { name: orgSettingsName.trim() })
    setOrgs((prev) => prev.map((org) => (org.id === updated.id ? updated : org)))
    setOrgSettingsName(updated.name)
  }

  const updateProviderConfig = async (config: ProviderConfig) => {
    if (!orgSettingsId) return
    const payload = [
      {
        provider: config.provider,
        is_enabled: config.is_enabled,
        api_key_override: config.api_key_override ?? "",
        base_url_override: config.base_url_override ?? "",
        endpoint_override: config.endpoint_override ?? "",
      },
    ]
    const updated = await orgApi.updateProviders(orgSettingsId, payload)
    setProviderConfigs(
      updated.map((item) => ({
        ...item,
        api_key_override: "",
      }))
    )
  }

  const updateWebSettings = async (payload: Partial<OrgWebSettings>) => {
    if (!orgSettingsId) return
    const updated = await orgApi.updateWebSettings(orgSettingsId, payload)
    setWebSettings(updated)
  }

  const updateProviderField = <K extends keyof ProviderConfig>(
    provider: string,
    field: K,
    value: ProviderConfig[K]
  ) => {
    setProviderConfigs((prev) =>
      prev.map((config) =>
        config.provider === provider ? { ...config, [field]: value } : config
      )
    )
  }

  if (!authChecked) {
    return null
  }

  if (!isSuperAdmin && !isAdmin) {
    return null
  }

  const navItems = [
    { label: t("me_settings"), href: "/settings/me", active: false },
    {
      label: t("org_section_users"),
      href: "/settings/users",
      visible: true,
      active: activeSection === "users",
    },
    {
      label: t("org_section_orgs"),
      href: "/settings/organisation",
      visible: isSuperAdmin,
      active: activeSection === "orgs",
    },
    {
      label: t("org_section_models"),
      href: "/settings/models",
      visible: isSuperAdmin,
      active: activeSection === "models",
    },
    {
      label: t("usage_title"),
      href: "/usage",
      visible: isAdmin,
      active: false,
    },
  ]

  return (
    <SettingsShell
      title={sectionTitle}
      items={navItems}
      actions={
        <div className="flex items-center gap-2">
          {isSuperAdmin ? (
            <Select value={selectedOrg ?? ""} onValueChange={selectOrg}>
              <SelectTrigger className="w-64">
                <SelectValue placeholder={t("org_select_placeholder")} />
              </SelectTrigger>
              <SelectContent>
                {orgs.map((org) => (
                  <SelectItem key={org.id} value={org.id}>
                    {org.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : null}
          <Button variant="outline" onClick={() => navigate("/chat")} disabled={!selectedOrg}>
            {t("common_back_to_chat")}
          </Button>
        </div>
      }
    >
      <div className="space-y-6">
        {error ? (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}

        {activeSection === "orgs" ? (
          <>
            {isSuperAdmin ? (
              <Card>
                <CardHeader>
                  <CardTitle>{t("org_section_orgs")}</CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                  {orgs.map((org) => (
                    <div
                      key={org.id}
                      className="flex flex-wrap items-center justify-between gap-3 rounded-md border px-3 py-2"
                    >
                      <div>
                        <p className="font-medium">{org.name}</p>
                        <p className="text-xs text-muted-foreground">{org.id}</p>
                        {!org.is_active ? (
                          <p className="text-xs text-red-500">{t("org_deleted")}</p>
                        ) : org.is_frozen ? (
                          <p className="text-xs text-amber-500">{t("org_frozen")}</p>
                        ) : null}
                      </div>
                      <div className="flex flex-wrap items-center gap-2">
                        <Button variant="outline" size="sm" onClick={() => openRenameDialog(org)}>
                          {t("org_rename")}
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => toggleOrgFrozen(org)}
                        >
                          {org.is_frozen ? t("org_unfreeze") : t("org_freeze")}
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => openDeleteDialog(org.id)}
                        >
                          {t("common_delete")}
                        </Button>
                      </div>
                    </div>
                  ))}
                  {orgs.length === 0 ? (
                    <p className="text-sm text-muted-foreground">{t("org_no_orgs")}</p>
                  ) : null}
                </CardContent>
              </Card>
            ) : null}

            {isSuperAdmin ? (
              <Card>
                <CardHeader>
                  <CardTitle>{t("org_add_org")}</CardTitle>
                </CardHeader>
                <CardContent className="flex gap-3">
                  <Input
                    placeholder={t("org_org_name")}
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                  />
                  <Button onClick={createOrg} disabled={!name.trim()}>
                    {t("org_create")}
                  </Button>
                </CardContent>
              </Card>
            ) : null}

            {canManageOrgSettings ? (
              <Card>
                <CardHeader>
                  <CardTitle>{t("org_settings")}</CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="flex flex-col gap-2">
                    <Input
                      placeholder={t("org_settings_name")}
                      value={orgSettingsName}
                      onChange={(event) => setOrgSettingsName(event.target.value)}
                    />
                    <Button onClick={saveOrgSettingsName} disabled={!orgSettingsName.trim()}>
                      {t("org_save_name")}
                    </Button>
                  </div>
                  <div className="space-y-4">
                    {providerConfigs.map((config) => (
                      <div key={config.provider} className="rounded-md border p-4 space-y-3">
                        <div className="flex items-center justify-between">
                          <p className="text-sm font-semibold">{config.provider}</p>
                          <Select
                            value={config.is_enabled ? "enabled" : "disabled"}
                            onValueChange={(value) =>
                              updateProviderField(
                                config.provider,
                                "is_enabled",
                                value === "enabled"
                              )
                            }
                          >
                            <SelectTrigger className="w-32">
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="enabled">
                                {t("org_provider_enabled")}
                              </SelectItem>
                              <SelectItem value="disabled">
                                {t("org_provider_disabled")}
                              </SelectItem>
                            </SelectContent>
                          </Select>
                        </div>
                        <Textarea
                          placeholder={
                            config.api_key_override_set
                              ? t("org_provider_override_set")
                              : t("org_provider_override_set_short")
                          }
                          value={config.api_key_override ?? ""}
                          onChange={(event) =>
                            updateProviderField(
                              config.provider,
                              "api_key_override",
                              event.target.value
                            )
                          }
                        />
                        {config.provider !== "azure" ? (
                          <Input
                            placeholder={t("org_provider_base_url")}
                            value={config.base_url_override ?? ""}
                            onChange={(event) =>
                              updateProviderField(
                                config.provider,
                                "base_url_override",
                                event.target.value
                              )
                            }
                          />
                        ) : (
                          <Input
                            placeholder={t("org_provider_endpoint")}
                            value={config.endpoint_override ?? ""}
                            onChange={(event) =>
                              updateProviderField(
                                config.provider,
                                "endpoint_override",
                                event.target.value
                              )
                            }
                          />
                        )}
                        <div className="flex gap-2">
                          <Button onClick={() => updateProviderConfig(config)}>
                            {t("common_save")}
                          </Button>
                          {config.api_key_override_set ? (
                            <Button
                              variant="outline"
                              onClick={() => {
                                updateProviderField(config.provider, "api_key_override", "")
                                updateProviderConfig({
                                  ...config,
                                  api_key_override: "",
                                })
                              }}
                            >
                              {t("org_provider_clear_key")}
                            </Button>
                          ) : null}
                        </div>
                      </div>
                    ))}
                    {providerConfigs.length === 0 ? (
                      <p className="text-sm text-muted-foreground">
                        {t("org_provider_none")}
                      </p>
                    ) : null}
                  </div>
                  {webSettings ? (
                    <div className="rounded-md border p-4 space-y-4">
                      <div className="flex items-center justify-between">
                        <div>
                          <p className="text-sm font-semibold">{t("org_web_tools")}</p>
                          <p className="text-xs text-muted-foreground">
                            {t("org_web_tools_desc")}
                          </p>
                        </div>
                        <Switch
                          checked={webSettings.web_tools_enabled}
                          onCheckedChange={(value) =>
                            updateWebSettings({ web_tools_enabled: value })
                          }
                          disabled={!canManageOrgSettings}
                        />
                      </div>
                      <div className="flex items-center justify-between">
                        <div>
                          <p className="text-sm font-medium">{t("org_web_search")}</p>
                          <p className="text-xs text-muted-foreground">
                            {t("org_web_search_desc")}
                          </p>
                        </div>
                        <Switch
                          checked={webSettings.web_search_enabled}
                          onCheckedChange={(value) =>
                            updateWebSettings({ web_search_enabled: value })
                          }
                          disabled={!canManageOrgSettings || !webSettings.web_tools_enabled}
                        />
                      </div>
                      <div className="flex items-center justify-between">
                        <div>
                          <p className="text-sm font-medium">{t("org_web_scrape")}</p>
                          <p className="text-xs text-muted-foreground">
                            {t("org_web_scrape_desc")}
                          </p>
                        </div>
                        <Switch
                          checked={webSettings.web_scrape_enabled}
                          onCheckedChange={(value) =>
                            updateWebSettings({ web_scrape_enabled: value })
                          }
                          disabled={!canManageOrgSettings || !webSettings.web_tools_enabled}
                        />
                      </div>
                      <div className="border-t pt-3 space-y-3">
                        <p className="text-sm font-semibold">{t("org_grounding")}</p>
                        <p className="text-xs text-muted-foreground">
                          {t("org_grounding_warning")}
                        </p>
                        <div className="flex items-center justify-between">
                          <div>
                            <p className="text-sm font-medium">
                              {t("org_grounding_openai")}
                            </p>
                            <p className="text-xs text-muted-foreground">
                              {t("org_grounding_openai_desc")}
                            </p>
                          </div>
                          <Switch
                            checked={webSettings.web_grounding_openai}
                            onCheckedChange={(value) =>
                              updateWebSettings({ web_grounding_openai: value })
                            }
                            disabled={!canManageOrgSettings}
                          />
                        </div>
                        <div className="flex items-center justify-between">
                          <div>
                            <p className="text-sm font-medium">
                              {t("org_grounding_gemini")}
                            </p>
                            <p className="text-xs text-muted-foreground">
                              {t("org_grounding_gemini_desc")}
                            </p>
                          </div>
                          <Switch
                            checked={webSettings.web_grounding_gemini}
                            onCheckedChange={(value) =>
                              updateWebSettings({ web_grounding_gemini: value })
                            }
                            disabled={!canManageOrgSettings}
                          />
                        </div>
                      </div>
                      <div className="border-t pt-3 space-y-3">
                        <p className="text-sm font-semibold">
                          {t("org_code_execution")}
                        </p>
                        <div className="flex items-center justify-between">
                          <div>
                            <p className="text-sm font-medium">
                              {t("org_code_execution_allow")}
                            </p>
                            <p className="text-xs text-muted-foreground">
                              {t("org_code_execution_desc")}
                            </p>
                          </div>
                          <Select
                            value={webSettings.exec_policy}
                            onValueChange={(value) =>
                              updateWebSettings({
                                exec_policy: value as OrgWebSettings["exec_policy"],
                              })
                            }
                            disabled={!canManageOrgSettings}
                          >
                            <SelectTrigger className="w-52">
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="off">
                                {t("org_code_execution_off")}
                              </SelectItem>
                              <SelectItem value="prompt">
                                {t("org_code_execution_prompt")}
                              </SelectItem>
                              <SelectItem value="always">
                                {t("org_code_execution_always")}
                              </SelectItem>
                            </SelectContent>
                          </Select>
                        </div>
                        <div className="flex items-center justify-between">
                          <div>
                            <p className="text-sm font-medium">
                              {t("org_code_execution_network")}
                            </p>
                            <p className="text-xs text-muted-foreground">
                              {t("org_code_execution_network_desc")}
                            </p>
                          </div>
                          <Switch
                            checked={webSettings.exec_network_enabled}
                            onCheckedChange={(value) =>
                              updateWebSettings({ exec_network_enabled: value })
                            }
                            disabled={
                              !canManageOrgSettings || webSettings.exec_policy === "off"
                            }
                          />
                        </div>
                      </div>
                    </div>
                  ) : null}
                </CardContent>
              </Card>
            ) : null}
          </>
        ) : null}

        {activeSection === "users" ? (
          <Card>
            <CardHeader>
              <CardTitle>{t("org_section_users")}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex flex-col gap-3">
                <Input
                  placeholder={t("org_users_invite_email")}
                  value={inviteEmail}
                  onChange={(event) => setInviteEmail(event.target.value)}
                />
                <Button disabled={!inviteEmail || !usersOrgId} onClick={sendInvite}>
                  {t("org_users_generate_invite")}
                </Button>
              </div>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("org_users_email")}</TableHead>
                    <TableHead>{t("org_users_role")}</TableHead>
                    {isSuperAdmin ? <TableHead>{t("org_users_superadmin")}</TableHead> : null}
                    <TableHead>{t("org_users_actions")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {members.map((member) => (
                    <TableRow key={member.user_id}>
                      <TableCell>{member.email}</TableCell>
                      <TableCell>
                        {canManageOrgSettings ? (
                          <Select
                            value={member.role}
                            onValueChange={(value) => updateMemberRole(member, value)}
                            disabled={
                              member.user_id === currentUserId && member.role === "admin"
                            }
                          >
                            <SelectTrigger className="w-32">
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              {roleOptions.map((role) => (
                                <SelectItem key={role} value={role}>
                                  {roleLabel(role)}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                        ) : (
                          roleLabel(member.role)
                        )}
                      </TableCell>
                      {isSuperAdmin ? (
                        <TableCell>
                          <Switch
                            checked={member.is_super_admin}
                            onCheckedChange={(value) =>
                              updateMemberSuperAdmin(member, value)
                            }
                            disabled={member.user_id === currentUserId}
                          />
                        </TableCell>
                      ) : null}
                      <TableCell />
                    </TableRow>
                  ))}
                  {invites.map((invite) => (
                    <TableRow key={invite.id}>
                      <TableCell>{invite.email}</TableCell>
                      <TableCell>{t("org_users_invited")}</TableCell>
                      {isSuperAdmin ? <TableCell /> : null}
                      <TableCell>
                        <div className="flex flex-wrap gap-2">
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => copyInviteLink(invite)}
                          >
                            {t("org_users_copy_link")}
                          </Button>
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => resendInvite(invite.id)}
                          >
                            {t("org_users_resend")}
                          </Button>
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => cancelInvite(invite.id)}
                          >
                            {t("common_cancel")}
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                  {members.length === 0 && invites.length === 0 ? (
                    <TableRow>
                      <TableCell
                        colSpan={isSuperAdmin ? 4 : 3}
                        className="text-sm text-muted-foreground"
                      >
                        {t("org_users_no_members")}
                      </TableCell>
                    </TableRow>
                  ) : null}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        ) : null}

        {activeSection === "models" ? (
          <>
            {isSuperAdmin || models.length > 0 ? (
              <Card>
                <CardHeader>
                  <CardTitle>{t("org_models_registry")}</CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  {isSuperAdmin ? (
                    <>
                      <div className="grid gap-3 md:grid-cols-4">
                        <Input
                          placeholder={t("org_models_provider_placeholder")}
                          value={modelProvider}
                          onChange={(event) => setModelProvider(event.target.value)}
                          list="provider-options"
                        />
                        <Input
                          placeholder={t("org_models_name_placeholder")}
                          value={modelName}
                          onChange={(event) => {
                            const next = event.target.value
                            setModelName(next)
                            const match = modelOptions.find((model) => model.model_name === next)
                            if (match) {
                              setModelDisplayName(match.display_name)
                              setModelReasoningEffort(match.reasoning_effort ?? "none")
                            }
                          }}
                          list="model-options"
                        />
                        <Input
                          placeholder={t("org_models_display_placeholder")}
                          value={modelDisplayName}
                          onChange={(event) => setModelDisplayName(event.target.value)}
                        />
                        <Select
                          value={modelReasoningEffort}
                          onValueChange={setModelReasoningEffort}
                        >
                          <SelectTrigger>
                          <SelectValue placeholder={t("org_models_reasoning_placeholder")} />
                          </SelectTrigger>
                          <SelectContent>
                            {reasoningOptions.map((option) => (
                              <SelectItem key={option} value={option}>
                                {reasoningLabel(option)}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </div>
                      <datalist id="provider-options">
                        {providerOptions.map((provider) => (
                          <option key={provider} value={provider} />
                        ))}
                      </datalist>
                      <datalist id="model-options">
                        {modelOptions.map((model) => (
                          <option key={model.model_name} value={model.model_name} />
                        ))}
                      </datalist>
                      <div className="flex flex-wrap items-center gap-2">
                        <Button onClick={createModel} disabled={!selectedOrg || !modelName.trim()}>
                          {t("org_models_add")}
                        </Button>
                      </div>
                    </>
                  ) : null}
                      <div className="space-y-2">
                    {orderedModels.map((model, index) => (
                      <div
                        key={model.id}
                        className="flex items-center justify-between rounded-md border px-3 py-2"
                      >
                        <div>
                          {editingModelId === model.id ? (
                            <Input
                              value={editingName}
                              onChange={(event) => setEditingName(event.target.value)}
                              className="h-8"
                            />
                          ) : (
                            <div className="flex items-center gap-2">
                              <p className="text-sm font-medium">{model.display_name}</p>
                              {isImageModel(model) ? (
                                <Image className="h-4 w-4 text-muted-foreground" />
                              ) : null}
                            </div>
                          )}
                          <p className="text-xs text-muted-foreground">
                            {model.provider} · {model.model_name}
                          </p>
                        </div>
                        {isSuperAdmin ? (
                          <div className="flex items-center gap-2">
                            <div className="flex items-center gap-1">
                              <Button
                                variant="ghost"
                                size="icon"
                                className="h-8 w-8"
                                onClick={() => moveModel(model.id, -1)}
                                disabled={index === 0}
                              >
                                <ArrowUp className="h-4 w-4" />
                              </Button>
                              <Button
                                variant="ghost"
                                size="icon"
                                className="h-8 w-8"
                                onClick={() => moveModel(model.id, 1)}
                                disabled={index === orderedModels.length - 1}
                              >
                                <ArrowDown className="h-4 w-4" />
                              </Button>
                            </div>
                            <Select
                              value={model.reasoning_effort ?? "none"}
                              onValueChange={(value) => updateReasoningEffort(model.id, value)}
                            >
                              <SelectTrigger className="h-8 w-28">
                                <SelectValue />
                              </SelectTrigger>
                              <SelectContent>
                            {reasoningOptions.map((option) => (
                              <SelectItem key={option} value={option}>
                                {reasoningLabel(option)}
                              </SelectItem>
                            ))}
                              </SelectContent>
                            </Select>
                            {editingModelId === model.id ? (
                              <>
                                <Button
                                  size="sm"
                                  onClick={() => saveRename(model.id)}
                                  disabled={!editingName.trim()}
                                >
                                  {t("common_save")}
                                </Button>
                                <Button variant="outline" size="sm" onClick={cancelRename}>
                                  {t("common_cancel")}
                                </Button>
                              </>
                            ) : (
                              <>
                                <Button
                                  variant="outline"
                                  size="sm"
                                  onClick={() => startRename(model)}
                                >
                                  {t("org_rename")}
                                </Button>
                                <Button
                                  variant="outline"
                                  size="sm"
                                  onClick={() => removeModel(model.id)}
                                >
                                  {t("org_models_remove")}
                                </Button>
                              </>
                            )}
                          </div>
                        ) : null}
                      </div>
                    ))}
                      {orderedModels.length === 0 ? (
                      <p className="text-sm text-muted-foreground">
                        {t("org_models_no_models")}
                      </p>
                    ) : null}
                  </div>
                </CardContent>
              </Card>
            ) : null}

            {isSuperAdmin ? (
              <Card>
                <CardHeader>
                  <CardTitle>{t("org_models_access")}</CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  {modelsOrgId ? (
                    <div className="space-y-2">
                      {orderedModels.map((model) => {
                        const enabled = accessEnabledIds.includes(model.id)
                        return (
                          <div
                            key={`access-${model.id}`}
                            className="flex items-center justify-between rounded-md border px-3 py-2"
                          >
                            <div>
                              <div className="flex items-center gap-2">
                                <p className="text-sm font-medium">{model.display_name}</p>
                                {isImageModel(model) ? (
                                  <Image className="h-4 w-4 text-muted-foreground" />
                                ) : null}
                              </div>
                              <p className="text-xs text-muted-foreground">
                                {model.provider} · {model.model_name}
                              </p>
                            </div>
                            <Button variant="outline" onClick={() => toggleModelAccess(model.id)}>
                              {enabled ? t("org_models_enabled") : t("org_models_disabled")}
                            </Button>
                          </div>
                        )
                      })}
                      {orderedModels.length === 0 ? (
                        <p className="text-xs text-muted-foreground">
                          {t("org_models_no_access")}
                        </p>
                      ) : null}
                    </div>
                  ) : null}
                </CardContent>
              </Card>
            ) : null}
          </>
        ) : null}
      </div>
      <Dialog
        open={Boolean(renameOrgId)}
        onOpenChange={(open) => (!open ? closeRenameDialog() : null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("org_dialog_rename_title")}</DialogTitle>
            <DialogDescription>{t("org_dialog_rename_desc")}</DialogDescription>
          </DialogHeader>
          <Input value={renameOrgName} onChange={(event) => setRenameOrgName(event.target.value)} />
          <DialogFooter>
            <Button variant="outline" onClick={closeRenameDialog}>
              {t("common_cancel")}
            </Button>
            <Button onClick={saveOrgRename} disabled={!renameOrgName.trim()}>
              {t("common_save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={Boolean(deleteOrgId)}
        onOpenChange={(open) => (!open ? closeDeleteDialog() : null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("org_dialog_delete_title")}</DialogTitle>
            <DialogDescription>
              {t("org_dialog_delete_desc")}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={closeDeleteDialog}>
              {t("common_cancel")}
            </Button>
            <Button onClick={confirmDeleteOrg}>{t("common_delete")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </SettingsShell>
  )
}
