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
  OrgAuthSettings,
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
import { ArrowDown, ArrowUp, Database, Image } from "lucide-react"

type SettingsSection = "orgs" | "users" | "models"

const PROVIDERS = ["openai", "azure", "gemini", "groq", "anthropic", "openrouter", "vertex"] as const

type ProviderConfigUI = ProviderConfig & {
  mode: "disabled" | "default" | "override"
}

export const OrgPage = () => {
  const navigate = useNavigate()
  const location = useLocation()
  const [activeSection, setActiveSection] = useState<SettingsSection>("orgs")
  const [orgs, setOrgs] = useState<Org[]>([])
  const [models, setModels] = useState<ChatModel[]>([])
  const [members, setMembers] = useState<OrgMember[]>([])
  const [invites, setInvites] = useState<Invite[]>([])
  const [providerConfigs, setProviderConfigs] = useState<ProviderConfigUI[]>([])
  const [authSettings, setAuthSettings] = useState<OrgAuthSettings | null>(null)
  const [authSecret, setAuthSecret] = useState("")
  const [authModalOpen, setAuthModalOpen] = useState(false)
  const [providerModalOpen, setProviderModalOpen] = useState(false)
  const [webSettings, setWebSettings] = useState<OrgWebSettings | null>(null)
  const [webSettingsByOrgId, setWebSettingsByOrgId] = useState<Record<string, OrgWebSettings>>(
    {}
  )
  const [updatingWebSettingsByOrgId, setUpdatingWebSettingsByOrgId] = useState<
    Record<string, boolean>
  >({})
  const [accessByOrgId, setAccessByOrgId] = useState<Record<string, string[]>>({})
  const [updatingAccess, setUpdatingAccess] = useState<Record<string, boolean>>({})
  const [name, setName] = useState("")
  const [inviteEmail, setInviteEmail] = useState("")
  const [modelProvider, setModelProvider] = useState("openai")
  const [modelName, setModelName] = useState("")
  const [modelDisplayName, setModelDisplayName] = useState("")
  const [modelReasoningEffort, setModelReasoningEffort] = useState("none")
  const [modelSuggestions, setModelSuggestions] = useState<ModelSuggestionProvider[]>([])
  const { t } = useI18n()
  const [selectedOrg, setSelectedOrg] = useState<string | null>(orgStore.get())
  const [orgSettingsId, setOrgSettingsId] = useState<string | null>(orgStore.get())
  const [usersOrgId, setUsersOrgId] = useState<string | null>(orgStore.get())
  const [isSuperAdmin, setIsSuperAdmin] = useState(false)
  const [isAdmin, setIsAdmin] = useState(false)
  const [authChecked, setAuthChecked] = useState(false)
  const [currentUserId, setCurrentUserId] = useState<string | null>(null)
  const [editingModelId, setEditingModelId] = useState<string | null>(null)
  const [editingName, setEditingName] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [renameOrgId, setRenameOrgId] = useState<string | null>(null)
  const [renameOrgName, setRenameOrgName] = useState("")
  const [deleteOrgId, setDeleteOrgId] = useState<string | null>(null)

  const isImageModel = (model: ChatModel) => {
    if (model.supports_image_output === true) return true
    if (model.supports_image_output === false) return false
    const name = `${model.display_name} ${model.model_name}`.toLowerCase()
    return (
      name.includes("image") ||
      name.includes("dall-e") ||
      name.includes("gpt-image") ||
      name.includes("imagen")
    )
  }

  const isEmbeddingModel = (model: ChatModel) => {
    const name = `${model.display_name} ${model.model_name}`.toLowerCase()
    return /(^|[\s/_-])(embedding|embeddings|text-embedding|embed)([\s/_-]|$)/.test(
      name
    )
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
    if (!selectedOrg) return
    if (isSuperAdmin) {
      modelApi.list().then(setModels).catch(() => null)
    } else {
      modelApi.list(selectedOrg).then(setModels).catch(() => null)
    }
  }, [selectedOrg, isSuperAdmin])

  useEffect(() => {
    if (!isSuperAdmin || activeSection !== "models") {
      setModelSuggestions([])
      return
    }
    modelApi
      .suggestions(selectedOrg ?? undefined, false)
      .then(setModelSuggestions)
      .catch(() => setModelSuggestions([]))
  }, [activeSection, isSuperAdmin, selectedOrg])

  useEffect(() => {
    if (!isSuperAdmin || activeSection !== "models" || orgs.length === 0) {
      setAccessByOrgId({})
      return
    }
    let cancelled = false
    const loadAccessMatrix = async () => {
      const entries = await Promise.all(
        orgs.map(async (org) => {
          try {
            const orgModels = await modelApi.list(org.id)
            return [org.id, orgModels.map((model) => model.id)] as const
          } catch {
            return [org.id, []] as const
          }
        })
      )
      if (!cancelled) {
        setAccessByOrgId(Object.fromEntries(entries))
      }
    }
    loadAccessMatrix().catch(() => {
      if (!cancelled) {
        setAccessByOrgId({})
      }
    })
    return () => {
      cancelled = true
    }
  }, [activeSection, isSuperAdmin, orgs])

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
    orgApi
      .providers(orgSettingsId)
      .then((configs) =>
        setProviderConfigs(
          configs.map((config) => {
            let mode: ProviderConfigUI["mode"] = "default"
            if (!config.is_enabled) {
              mode = "disabled"
            } else if (
              config.api_key_override_set ||
              config.api_key_override ||
              config.base_url_override ||
              config.endpoint_override ||
              config.config_json
            ) {
              mode = "override"
            } else if (!config.has_global_config) {
              // If no global config and no override, it's effectively disabled or needs override
              // User said: "disable and do not allow setting to Enabled(Default) Providers that have no api keys/credentials set. Allow only Enabled(override) for those"
              // If is_enabled was true but no global config, it was likely defaulted to True in backend but actually broken.
              // We should probably show it as "disabled" initially if no override is set?
              // Or force user to pick.
              mode = "disabled"
            }
            return {
              ...config,
              api_key_override: "",
              mode,
            }
          })
        )
      )
      .catch(() => setProviderConfigs([]))
    orgApi
      .authSettings(orgSettingsId)
      .then((settings) => {
        setAuthSettings(settings)
        setAuthSecret("")
      })
      .catch(() => setAuthSettings(null))
    orgApi
      .webSettings(orgSettingsId)
      .then(setWebSettings)
      .catch(() => setWebSettings(null))
  }, [orgSettingsId, orgs])

  useEffect(() => {
    if (!isSuperAdmin || activeSection !== "orgs" || orgs.length === 0) {
      setWebSettingsByOrgId({})
      return
    }
    let cancelled = false
    const loadWebSettingsMatrix = async () => {
      const entries = await Promise.all(
        orgs.map(async (org) => {
          try {
            const settings = await orgApi.webSettings(org.id)
            return [org.id, settings] as const
          } catch {
            return null
          }
        })
      )
      if (cancelled) return
      const next = Object.fromEntries(entries.filter((item): item is readonly [string, OrgWebSettings] => item !== null))
      setWebSettingsByOrgId(next)
    }
    loadWebSettingsMatrix().catch(() => {
      if (!cancelled) {
        setWebSettingsByOrgId({})
      }
    })
    return () => {
      cancelled = true
    }
  }, [activeSection, isSuperAdmin, orgs])

  const createOrg = async () => {
    setError(null)
    const org = await orgApi.create(name)
    setName("")
    setOrgs((prev) => [...prev, org])
  }

  const sendInvite = async () => {
    if (!usersOrgId) return
    const emails = Array.from(
      new Set(
        inviteEmail
          .split(/[\s,]+/g)
          .map((item) => item.trim().toLowerCase())
          .filter(Boolean)
      )
    )
    if (emails.length === 0) return
    const results = await Promise.allSettled(
      emails.map((email) => authApi.createInvite(usersOrgId, email))
    )
    const failedEmails = results
      .map((result, index) => ({ result, email: emails[index] }))
      .filter((item) => item.result.status === "rejected")
      .map((item) => item.email)
    const updated = await authApi.invites(usersOrgId)
    setInvites(updated)
    if (failedEmails.length > 0) {
      setError(`Failed to send invites: ${failedEmails.join(", ")}`)
      return
    }
    setError(null)
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
    const inviteOrg = orgs.find((org) => org.id === invite.org_id)
    const orgHint = (inviteOrg?.slug ?? inviteOrg?.name ?? "").trim().toLowerCase()
    const params = new URLSearchParams({ token: invite.token })
    if (orgHint) {
      params.set("org", orgHint)
    }
    const url = `${window.location.origin}/invite?${params.toString()}`
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

  const removeMember = async (member: OrgMember) => {
    if (!usersOrgId) return
    await orgApi.removeMember(usersOrgId, member.user_id)
    setMembers((prev) => prev.filter((item) => item.user_id !== member.user_id))
  }

  const createModel = async () => {
    if (!selectedOrg) return
    const model = await modelApi.create({
      org_id: selectedOrg,
      provider: modelProvider,
      model_name: modelName,
      display_name: modelDisplayName || modelName,
      context_length: null,
      supports_image_input: null,
      supports_image_output: null,
      reasoning_effort: modelReasoningEffort,
      is_active: true,
    })
    setModels((prev) => [...prev, model])
    if (selectedOrg) {
      setAccessByOrgId((prev) => ({
        ...prev,
        [selectedOrg]: Array.from(new Set([...(prev[selectedOrg] ?? []), model.id])),
      }))
    }
    setModelName("")
    setModelDisplayName("")
    setModelReasoningEffort("none")
  }

  const removeModel = async (modelId: string) => {
    await modelApi.remove(modelId)
    setModels((prev) => prev.filter((model) => model.id !== modelId))
    setAccessByOrgId((prev) =>
      Object.fromEntries(
        Object.entries(prev).map(([orgId, enabledIds]) => [
          orgId,
          enabledIds.filter((id) => id !== modelId),
        ])
      )
    )
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

  const toggleModelAccess = async (orgId: string, modelId: string) => {
    const key = `${orgId}:${modelId}`
    const wasEnabled = (accessByOrgId[orgId] ?? []).includes(modelId)
    setUpdatingAccess((prev) => ({ ...prev, [key]: true }))
    setAccessByOrgId((prev) => {
      const current = prev[orgId] ?? []
      const next = wasEnabled
        ? current.filter((id) => id !== modelId)
        : Array.from(new Set([...current, modelId]))
      return { ...prev, [orgId]: next }
    })
    try {
      await modelApi.setOrgModels(orgId, [{ model_id: modelId, is_enabled: !wasEnabled }])
    } catch (err) {
      setAccessByOrgId((prev) => {
        const current = prev[orgId] ?? []
        const rolledBack = wasEnabled
          ? Array.from(new Set([...current, modelId]))
          : current.filter((id) => id !== modelId)
        return { ...prev, [orgId]: rolledBack }
      })
      setError(err instanceof Error ? err.message : t("common_save_failed"))
    } finally {
      setUpdatingAccess((prev) => ({ ...prev, [key]: false }))
    }
  }

  const providerOptions = useMemo(() => {
    return [...PROVIDERS]
  }, [])
  const currentProviderSuggestions = useMemo(() => {
    const entry = modelSuggestions.find((item) => item.provider === modelProvider)
    return entry?.models ?? []
  }, [modelProvider, modelSuggestions])
  const currentProviderSuggestionError = useMemo(() => {
    const entry = modelSuggestions.find((item) => item.provider === modelProvider)
    return entry?.error ?? null
  }, [modelProvider, modelSuggestions])

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
  const orgSettingsRows: Array<{
    key: keyof OrgWebSettings
    label: string
    type: "boolean" | "exec_policy"
  }> = [
    { key: "web_search_enabled", label: t("org_web_search"), type: "boolean" },
    { key: "web_scrape_enabled", label: t("org_web_scrape"), type: "boolean" },
    { key: "web_grounding_openai", label: t("org_grounding_openai"), type: "boolean" },
    { key: "web_grounding_gemini", label: t("org_grounding_gemini"), type: "boolean" },
    { key: "exec_policy", label: t("org_code_execution"), type: "exec_policy" },
    { key: "exec_network_enabled", label: t("org_code_execution_network"), type: "boolean" },
  ]

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

  const openAuthForOrg = (orgId: string) => {
    setOrgSettingsId(orgId)
    setAuthModalOpen(true)
  }

  const openProvidersForOrg = (orgId: string) => {
    setOrgSettingsId(orgId)
    setProviderModalOpen(true)
  }

  const updateProviderConfig = async (config: ProviderConfigUI) => {
    if (!orgSettingsId) return
    const payload = [
      {
        provider: config.provider,
        is_enabled: config.mode !== "disabled",
        api_key_override: config.mode === "override" ? (config.api_key_override ?? "") : "",
        base_url_override: config.mode === "override" ? (config.base_url_override ?? "") : "",
        endpoint_override: config.mode === "override" ? (config.endpoint_override ?? "") : "",
        config_json: config.mode === "override" ? (config.config_json ?? "") : "",
      },
    ]
    const updated = await orgApi.updateProviders(orgSettingsId, payload)
    setProviderConfigs((prev) => {
      // Update the saved config but preserve the UI state (mode) if consistent,
      // or update it based on response. Actually response reflects what we saved.
      // But we lose the "api_key_override" value (it comes back masked as api_key_override_set).
      // So we should re-map.
      return prev.map((prevConfig) => {
        const up = updated.find((u) => u.provider === prevConfig.provider)
        if (!up) return prevConfig
        // If we just saved "default", mode should be "default".
        // If "disabled", mode "disabled".
        // If "override", mode "override".
        return {
          ...up,
          api_key_override: "", // Always clear on save as it's secret
          mode: config.mode,
        }
      })
    })
  }

  const updateWebSettings = async (payload: Partial<OrgWebSettings>) => {
    if (!orgSettingsId) return
    const updated = await orgApi.updateWebSettings(orgSettingsId, payload)
    setWebSettings(updated)
    setWebSettingsByOrgId((prev) => ({ ...prev, [orgSettingsId]: updated }))
  }

  const updateWebSettingsForOrg = async (orgId: string, payload: Partial<OrgWebSettings>) => {
    setUpdatingWebSettingsByOrgId((prev) => ({ ...prev, [orgId]: true }))
    try {
      const updated = await orgApi.updateWebSettings(orgId, payload)
      setWebSettingsByOrgId((prev) => ({ ...prev, [orgId]: updated }))
      if (orgSettingsId === orgId) {
        setWebSettings(updated)
      }
    } finally {
      setUpdatingWebSettingsByOrgId((prev) => ({ ...prev, [orgId]: false }))
    }
  }

  const updateProviderField = <K extends keyof ProviderConfigUI>(
    provider: string,
    field: K,
    value: ProviderConfigUI[K]
  ) => {
    setProviderConfigs((prev) =>
      prev.map((config) =>
        config.provider === provider ? { ...config, [field]: value } : config
      )
    )
  }

  const updateAuthField = <K extends keyof OrgAuthSettings>(
    field: K,
    value: OrgAuthSettings[K]
  ) => {
    setAuthSettings((prev) => (prev ? { ...prev, [field]: value } : prev))
  }

  const saveAuthSettings = async () => {
    if (!orgSettingsId || !authSettings) return
    const payload = {
      slug: authSettings.slug,
      oidc_enabled: authSettings.oidc_enabled,
      oidc_issuer: authSettings.oidc_issuer ?? "",
      oidc_client_id: authSettings.oidc_client_id ?? "",
      oidc_client_secret: authSecret ? authSecret : undefined,
      oidc_scopes: authSettings.oidc_scopes,
      oidc_email_claim: authSettings.oidc_email_claim,
      oidc_username_claim: authSettings.oidc_username_claim ?? "",
      oidc_groups_claim: authSettings.oidc_groups_claim ?? "",
      oidc_auto_create_users: authSettings.oidc_auto_create_users,
    }
    const updated = await orgApi.updateAuthSettings(orgSettingsId, payload)
    setAuthSettings(updated)
    setAuthSecret("")
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
  const showOrgSelector = !(
    isSuperAdmin &&
    (activeSection === "orgs" || activeSection === "models")
  )

  return (
    <SettingsShell
      title={sectionTitle}
      items={navItems}
      actions={
        <div className="flex items-center gap-2">
          {isSuperAdmin && showOrgSelector ? (
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
                      className="flex flex-wrap justify-between items-center gap-3 px-3 py-2 border rounded-md"
                    >
                      <div>
                        <p className="font-medium">{org.name}</p>
                        <p className="text-muted-foreground text-xs">{org.id}</p>
                        {!org.is_active ? (
                          <p className="text-red-500 text-xs">{t("org_deleted")}</p>
                        ) : org.is_frozen ? (
                          <p className="text-amber-500 text-xs">{t("org_frozen")}</p>
                        ) : null}
                      </div>
                      <div className="flex flex-wrap items-center gap-2">
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => openAuthForOrg(org.id)}
                        >
                          {t("org_auth_open")}
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => openProvidersForOrg(org.id)}
                        >
                          {t("org_provider_configure")}
                        </Button>
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
                    <p className="text-muted-foreground text-sm">{t("org_no_orgs")}</p>
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
                  {isSuperAdmin ? (
                    <div className="border rounded-md w-full overflow-x-auto">
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead className="left-0 z-10 sticky bg-card min-w-56">
                              {t("org_settings")}
                            </TableHead>
                            {orgs.map((org) => (
                              <TableHead key={`web-settings-head-${org.id}`} className="min-w-52">
                                {org.name}
                              </TableHead>
                            ))}
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {orgSettingsRows.map((row) => (
                            <TableRow key={`web-settings-row-${row.key}`}>
                              <TableCell className="left-0 z-10 sticky bg-card font-medium text-sm">
                                {row.label}
                              </TableCell>
                              {orgs.map((org) => {
                                const settings = webSettingsByOrgId[org.id]
                                const isUpdating = Boolean(updatingWebSettingsByOrgId[org.id])
                                if (!settings) {
                                  return (
                                    <TableCell
                                      key={`web-settings-cell-${row.key}-${org.id}`}
                                      className="text-muted-foreground text-xs"
                                    >
                                      -
                                    </TableCell>
                                  )
                                }
                                if (row.type === "exec_policy") {
                                  return (
                                    <TableCell key={`web-settings-cell-${row.key}-${org.id}`}>
                                      <Select
                                        value={settings.exec_policy}
                                        onValueChange={(value) =>
                                          updateWebSettingsForOrg(org.id, {
                                            exec_policy: value as OrgWebSettings["exec_policy"],
                                          })
                                        }
                                        disabled={isUpdating || !org.is_active}
                                      >
                                        <SelectTrigger className="w-40">
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
                                    </TableCell>
                                  )
                                }
                                const checked = Boolean(settings[row.key] as boolean)
                                return (
                                  <TableCell
                                    key={`web-settings-cell-${row.key}-${org.id}`}
                                    className="text-center align-middle"
                                  >
                                    <div className="flex justify-center items-center">
                                      <Switch
                                        checked={checked}
                                        onCheckedChange={(value) =>
                                          updateWebSettingsForOrg(org.id, {
                                            [row.key]: value,
                                          } as Partial<OrgWebSettings>)
                                        }
                                        disabled={
                                          isUpdating ||
                                          !org.is_active ||
                                          (row.key === "exec_network_enabled" &&
                                            settings.exec_policy === "off")
                                        }
                                      />
                                    </div>
                                  </TableCell>
                                )
                              })}
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </div>
                  ) : webSettings ? (
                    <div className="space-y-4 p-4 border rounded-md">
                      <div className="flex justify-between items-center">
                        <div>
                          <p className="font-medium text-sm">{t("org_web_search")}</p>
                          <p className="text-muted-foreground text-xs">
                            {t("org_web_search_desc")}
                          </p>
                        </div>
                        <Switch
                          checked={webSettings.web_search_enabled}
                          onCheckedChange={(value) =>
                            updateWebSettings({ web_search_enabled: value })
                          }
                          disabled={!canManageOrgSettings}
                        />
                      </div>
                      <div className="flex justify-between items-center">
                        <div>
                          <p className="font-medium text-sm">{t("org_web_scrape")}</p>
                          <p className="text-muted-foreground text-xs">
                            {t("org_web_scrape_desc")}
                          </p>
                        </div>
                        <Switch
                          checked={webSettings.web_scrape_enabled}
                          onCheckedChange={(value) =>
                            updateWebSettings({ web_scrape_enabled: value })
                          }
                          disabled={!canManageOrgSettings}
                        />
                      </div>
                      <div className="space-y-3 pt-3 border-t">
                        <p className="font-semibold text-sm">{t("org_grounding")}</p>
                        <p className="text-muted-foreground text-xs">
                          {t("org_grounding_warning")}
                        </p>
                        <div className="flex justify-between items-center">
                          <div>
                            <p className="font-medium text-sm">
                              {t("org_grounding_openai")}
                            </p>
                            <p className="text-muted-foreground text-xs">
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
                        <div className="flex justify-between items-center">
                          <div>
                            <p className="font-medium text-sm">
                              {t("org_grounding_gemini")}
                            </p>
                            <p className="text-muted-foreground text-xs">
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
                      <div className="space-y-3 pt-3 border-t">
                        <p className="font-semibold text-sm">
                          {t("org_code_execution")}
                        </p>
                        <div className="flex justify-between items-center">
                          <div>
                            <p className="font-medium text-sm">
                              {t("org_code_execution_allow")}
                            </p>
                            <p className="text-muted-foreground text-xs">
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
                        <div className="flex justify-between items-center">
                          <div>
                            <p className="font-medium text-sm">
                              {t("org_code_execution_network")}
                            </p>
                            <p className="text-muted-foreground text-xs">
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
                <Button
                  disabled={!usersOrgId || inviteEmail.split(/[\s,]+/g).every((item) => !item.trim())}
                  onClick={sendInvite}
                >
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
                      <TableCell>
                        {canManageOrgSettings ? (
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => removeMember(member)}
                            disabled={member.user_id === currentUserId}
                          >
                            {t("org_users_remove_member")}
                          </Button>
                        ) : null}
                      </TableCell>
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
                        className="text-muted-foreground text-sm"
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
                      <div className="gap-3 grid md:grid-cols-4">
                        <Select value={modelProvider} onValueChange={setModelProvider}>
                          <SelectTrigger>
                            <SelectValue placeholder={t("org_models_provider_placeholder")} />
                          </SelectTrigger>
                          <SelectContent>
                            {providerOptions.map((provider) => (
                              <SelectItem key={provider} value={provider}>
                                {provider}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                        <Input
                          placeholder={t("org_models_name_placeholder")}
                          value={modelName}
                          onChange={(event) => {
                            const value = event.target.value
                            setModelName(value)
                            const match = currentProviderSuggestions.find(
                              (item) => item.model_name === value
                            )
                            if (match && !modelDisplayName.trim()) {
                              setModelDisplayName(match.display_name || value)
                            }
                          }}
                          list="model-name-suggestions"
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
                      <div className="flex flex-wrap items-center gap-2">
                        <Button onClick={createModel} disabled={!selectedOrg || !modelName.trim()}>
                          {t("org_models_add")}
                        </Button>
                      </div>
                      {currentProviderSuggestionError ? (
                        <p className="text-muted-foreground text-xs">
                          {currentProviderSuggestionError}
                        </p>
                      ) : null}
                      <datalist id="model-name-suggestions">
                        {currentProviderSuggestions.map((item) => (
                          <option key={item.model_name} value={item.model_name}>
                            {item.display_name}
                          </option>
                        ))}
                      </datalist>
                    </>
                  ) : null}
                      <div className="space-y-2">
                    {orderedModels.map((model, index) => (
                      <div
                        key={model.id}
                        className="flex justify-between items-center px-3 py-2 border rounded-md"
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
                              <p className="font-medium text-sm">{model.display_name}</p>
                              {isImageModel(model) ? (
                                <Image className="w-4 h-4 text-muted-foreground" />
                              ) : isEmbeddingModel(model) ? (
                                <Database className="w-4 h-4 text-muted-foreground" />
                              ) : null}
                            </div>
                          )}
                          <p className="text-muted-foreground text-xs">
                            {model.provider} · {model.model_name}
                          </p>
                        </div>
                        {isSuperAdmin ? (
                          <div className="flex items-center gap-2">
                            <div className="flex items-center gap-1">
                              <Button
                                variant="ghost"
                                size="icon"
                                className="w-8 h-8"
                                onClick={() => moveModel(model.id, -1)}
                                disabled={index === 0}
                              >
                                <ArrowUp className="w-4 h-4" />
                              </Button>
                              <Button
                                variant="ghost"
                                size="icon"
                                className="w-8 h-8"
                                onClick={() => moveModel(model.id, 1)}
                                disabled={index === orderedModels.length - 1}
                              >
                                <ArrowDown className="w-4 h-4" />
                              </Button>
                            </div>
                            <Select
                              value={model.reasoning_effort ?? "none"}
                              onValueChange={(value) => updateReasoningEffort(model.id, value)}
                            >
                              <SelectTrigger className="w-28 h-8">
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
                      <p className="text-muted-foreground text-sm">
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
                  {orgs.length > 0 ? (
                    <div className="border rounded-md w-full overflow-x-auto">
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead className="left-0 z-10 sticky bg-card min-w-64">
                              {t("usage_model")}
                            </TableHead>
                            {orgs.map((org) => (
                              <TableHead key={org.id} className="min-w-44 text-center">
                                {org.name}
                              </TableHead>
                            ))}
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {orderedModels.map((model) => (
                            <TableRow key={`access-${model.id}`}>
                              <TableCell className="left-0 z-10 sticky bg-card">
                                <div className="flex items-center gap-2">
                                  <p className="font-medium text-sm">{model.display_name}</p>
                                  {isImageModel(model) ? (
                                    <Image className="w-4 h-4 text-muted-foreground" />
                                  ) : isEmbeddingModel(model) ? (
                                    <Database className="w-4 h-4 text-muted-foreground" />
                                  ) : null}
                                </div>
                                <p className="text-muted-foreground text-xs">
                                  {model.provider} · {model.model_name}
                                </p>
                              </TableCell>
                              {orgs.map((org) => {
                                const enabled = (accessByOrgId[org.id] ?? []).includes(model.id)
                                const key = `${org.id}:${model.id}`
                                return (
                                  <TableCell key={key} className="text-center">
                                    <input
                                      type="checkbox"
                                      className="w-4 h-4 accent-primary"
                                      checked={enabled}
                                      onChange={() => toggleModelAccess(org.id, model.id)}
                                      disabled={Boolean(updatingAccess[key]) || !org.is_active}
                                    />
                                  </TableCell>
                                )
                              })}
                            </TableRow>
                          ))}
                          {orderedModels.length === 0 ? (
                            <TableRow>
                              <TableCell
                                colSpan={Math.max(orgs.length + 1, 2)}
                                className="text-muted-foreground text-xs"
                              >
                                {t("org_models_no_access")}
                              </TableCell>
                            </TableRow>
                          ) : null}
                        </TableBody>
                      </Table>
                    </div>
                  ) : null}
                </CardContent>
              </Card>
            ) : null}
          </>
        ) : null}
      </div>
      <Dialog open={authModalOpen} onOpenChange={(open) => setAuthModalOpen(open)}>
        <DialogContent className="max-w-3xl max-h-[80vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{t("org_auth_settings")}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3 p-4 border rounded-md">
            <div className="flex justify-between items-center">
              <p className="font-semibold text-sm">{t("org_auth_settings")}</p>
              <Switch
                checked={authSettings?.oidc_enabled ?? false}
                onCheckedChange={(value) => updateAuthField("oidc_enabled", value)}
              />
            </div>
            <div className="px-3 py-2 border rounded-md text-muted-foreground text-xs">
              <span className="font-semibold text-foreground">
                {t("org_auth_redirect_url")}:
              </span>{" "}
              {`${window.location.origin}/api/auth/oidc/callback`}
            </div>
            <Input
              placeholder={t("org_auth_org_slug")}
              value={authSettings?.slug ?? ""}
              onChange={(event) => updateAuthField("slug", event.target.value)}
            />
            <Input
              placeholder={t("org_auth_oidc_issuer")}
              value={authSettings?.oidc_issuer ?? ""}
              onChange={(event) => updateAuthField("oidc_issuer", event.target.value)}
            />
            <Input
              placeholder={t("org_auth_oidc_client_id")}
              value={authSettings?.oidc_client_id ?? ""}
              onChange={(event) => updateAuthField("oidc_client_id", event.target.value)}
            />
            <Input
              type="password"
              placeholder={t("org_auth_oidc_client_secret")}
              value={authSecret}
              onChange={(event) => setAuthSecret(event.target.value)}
            />
            <div className="gap-3 grid md:grid-cols-2">
              <Input
                placeholder={t("org_auth_oidc_scopes")}
                value={authSettings?.oidc_scopes ?? ""}
                onChange={(event) => updateAuthField("oidc_scopes", event.target.value)}
              />
              <Input
                placeholder={t("org_auth_oidc_email_claim")}
                value={authSettings?.oidc_email_claim ?? ""}
                onChange={(event) => updateAuthField("oidc_email_claim", event.target.value)}
              />
              <Input
                placeholder={t("org_auth_oidc_username_claim")}
                value={authSettings?.oidc_username_claim ?? ""}
                onChange={(event) =>
                  updateAuthField("oidc_username_claim", event.target.value)
                }
              />
              <Input
                placeholder={t("org_auth_oidc_groups_claim")}
                value={authSettings?.oidc_groups_claim ?? ""}
                onChange={(event) =>
                  updateAuthField("oidc_groups_claim", event.target.value)
                }
              />
            </div>
            <div className="flex justify-between items-center">
              <p className="font-medium text-sm">{t("org_auth_oidc_auto_create")}</p>
              <Switch
                checked={authSettings?.oidc_auto_create_users ?? false}
                onCheckedChange={(value) =>
                  updateAuthField("oidc_auto_create_users", value)
                }
              />
            </div>
            <div className="flex gap-2">
              <Button onClick={saveAuthSettings}>{t("common_save")}</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog
        open={providerModalOpen}
        onOpenChange={(open) => setProviderModalOpen(open)}
      >
        <DialogContent className="max-w-3xl max-h-[80vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{t("org_provider_settings")}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            {providerConfigs.map((config) => (
              <div key={config.provider} className="space-y-3 p-4 border rounded-md">
                <div className="flex justify-between items-center">
                  <p className="font-semibold text-sm">{config.provider}</p>
                  <Select
                    value={config.mode}
                    onValueChange={(value) =>
                      updateProviderField(
                        config.provider,
                        "mode",
                        value as ProviderConfigUI["mode"]
                      )
                    }
                  >
                    <SelectTrigger className="w-48">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="disabled">{t("org_provider_disabled")}</SelectItem>
                      <SelectItem value="default" disabled={!config.has_global_config}>
                        {t("org_provider_enabled_defaults")}
                      </SelectItem>
                      <SelectItem value="override">{t("org_provider_enabled_override")}</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                {config.mode === "override" ? (
                  <>
                    <Input
                      type="password"
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
                    {config.provider === "vertex" ? (
                      <Textarea
                        placeholder={t("org_provider_vertex_config_placeholder")}
                        value={config.config_json ?? ""}
                        onChange={(event) =>
                          updateProviderField(
                            config.provider,
                            "config_json",
                            event.target.value
                          )
                        }
                        className="h-24 font-mono text-xs"
                      />
                    ) : config.provider === "azure" ? (
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
                    ) : (
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
                    )}
                  </>
                ) : null}
                <div className="flex gap-2">
                  <Button onClick={() => updateProviderConfig(config)}>
                    {t("common_save")}
                  </Button>
                </div>
              </div>
            ))}
            {providerConfigs.length === 0 ? (
              <p className="text-muted-foreground text-sm">{t("org_provider_none")}</p>
            ) : null}
          </div>
        </DialogContent>
      </Dialog>

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
