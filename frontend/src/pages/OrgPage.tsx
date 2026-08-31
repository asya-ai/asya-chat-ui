import { useEffect, useMemo, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { useLocation, useNavigate } from "@tanstack/react-router"

import { authApi, modelApi, orgApi } from "@/lib/api"
import { orgStore } from "@/lib/storage"
import { useI18n } from "@/lib/i18n-context"
import { SettingsShell } from "@/components/SettingsShell"
import type {
  ChatModel,
  Invite,
  ModelSuggestionProvider,
  OpenRouterEndpoint,
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
import { Database, GripVertical, Image } from "lucide-react"
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core"
import {
  SortableContext,
  arrayMove,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable"
import { CSS } from "@dnd-kit/utilities"

type SettingsSection = "orgs" | "users" | "models"

type SortableRowRenderProps = {
  setNodeRef: (node: HTMLElement | null) => void
  style: React.CSSProperties
  attributes: React.HTMLAttributes<HTMLElement>
  listeners: Record<string, (event: React.SyntheticEvent) => void> | undefined
  isDragging: boolean
}

const SortableModelRow = ({
  id,
  children,
}: {
  id: string
  children: (props: SortableRowRenderProps) => React.ReactNode
}) => {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id,
  })
  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    zIndex: isDragging ? 10 : undefined,
    opacity: isDragging ? 0.8 : undefined,
  }
  return (
    <>
      {children({
        setNodeRef,
        style,
        attributes: attributes as React.HTMLAttributes<HTMLElement>,
        listeners: listeners as SortableRowRenderProps["listeners"],
        isDragging,
      })}
    </>
  )
}

const PROVIDERS = ["openai", "azure", "gemini", "groq", "anthropic", "openrouter", "vertex"] as const
const OPENROUTER_ENDPOINT_AUTO = "__auto__"

type ProviderConfigUI = ProviderConfig & {
  mode: "disabled" | "default" | "override"
}

export const OrgPage = () => {
  const navigate = useNavigate()
  const location = useLocation()
  const queryClient = useQueryClient()
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
  const [retentionDraftsByOrgId, setRetentionDraftsByOrgId] = useState<
    Record<string, { fileRetentionDays: string | null; chatRetentionDays: string | null }>
  >({})
  const [name, setName] = useState("")
  const [inviteEmail, setInviteEmail] = useState("")
  const [inviteRole, setInviteRole] = useState("member")
  const [modelProvider, setModelProvider] = useState("openai")
  const [modelName, setModelName] = useState("")
  const [modelDisplayName, setModelDisplayName] = useState("")
  const [modelReasoningEffort, setModelReasoningEffort] = useState("none")
  const [modelOpenRouterEndpoint, setModelOpenRouterEndpoint] = useState("")
  const [openRouterEndpointsByModel, setOpenRouterEndpointsByModel] = useState<
    Record<string, OpenRouterEndpoint[]>
  >({})
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
  const [retentionOrgId, setRetentionOrgId] = useState<string | null>(null)
  const [costCeilingOrgId, setCostCeilingOrgId] = useState<string | null>(null)
  const [costCeilingDraftsByOrgId, setCostCeilingDraftsByOrgId] = useState<
    Record<string, string | null>
  >({})
  const [deleteOrgId, setDeleteOrgId] = useState<string | null>(null)
  const retentionOrg = orgs.find((org) => org.id === retentionOrgId) ?? null
  const costCeilingOrg = orgs.find((org) => org.id === costCeilingOrgId) ?? null

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

  const persistModelOrder = async (ordered: ChatModel[]) => {
    const next = ordered.map((model, idx) => ({
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

  const handleModelDragEnd = (event: DragEndEvent) => {
    if (!isSuperAdmin) return
    const { active, over } = event
    if (!over || active.id === over.id) return
    const oldIndex = orderedModels.findIndex((model) => model.id === active.id)
    const newIndex = orderedModels.findIndex((model) => model.id === over.id)
    if (oldIndex < 0 || newIndex < 0) return
    void persistModelOrder(arrayMove(orderedModels, oldIndex, newIndex))
  }

  const modelSensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
  )

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
    setRetentionDraftsByOrgId(
      Object.fromEntries(
        data.map((org) => [
          org.id,
          {
            fileRetentionDays:
              org.file_retention_days === null ? null : String(org.file_retention_days ?? 30),
            chatRetentionDays:
              org.chat_retention_days === null ? null : String(org.chat_retention_days ?? 90),
          },
        ])
      )
    )
    setCostCeilingDraftsByOrgId(
      Object.fromEntries(
        data.map((org) => [
          org.id,
          org.cost_ceiling_usd === null || org.cost_ceiling_usd === undefined
            ? null
            : String(org.cost_ceiling_usd),
        ])
      )
    )
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
      navigate({ to: "/settings/me" })
      return
    }
    if (!isSuperAdmin && location.pathname !== "/settings/users") {
      navigate({ to: "/settings/users" })
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
      modelApi.list(undefined, { scope: "org" }).then(setModels).catch(() => null)
    } else {
      modelApi.list(selectedOrg, { scope: "org" }).then(setModels).catch(() => null)
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
    if (!isSuperAdmin || activeSection !== "models") return
    const names = new Set<string>()
    if (modelProvider === "openrouter" && modelName.includes("/")) {
      names.add(modelName.trim())
    }
    for (const model of models) {
      if (model.provider === "openrouter" && model.model_name.includes("/")) {
        names.add(model.model_name)
      }
    }
    const missing = [...names].filter((name) => !(name in openRouterEndpointsByModel))
    if (missing.length === 0) return
    let cancelled = false
    const load = async () => {
      const entries = await Promise.all(
        missing.map(async (name) => {
          try {
            const result = await modelApi.openRouterEndpoints(name)
            return [name, result.endpoints] as const
          } catch {
            return [name, [] as OpenRouterEndpoint[]] as const
          }
        })
      )
      if (cancelled) return
      setOpenRouterEndpointsByModel((prev) => {
        const next = { ...prev }
        for (const [name, endpoints] of entries) {
          next[name] = endpoints
        }
        return next
      })
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [
    activeSection,
    isSuperAdmin,
    modelProvider,
    modelName,
    models,
    openRouterEndpointsByModel,
  ])

  useEffect(() => {
    if (!isSuperAdmin || activeSection !== "models" || orgs.length <= 1) {
      setAccessByOrgId({})
      return
    }
    let cancelled = false
    const loadAccessMatrix = async () => {
      const entries = await Promise.all(
        orgs.map(async (org) => {
          try {
            const orgModels = await modelApi.list(org.id, { scope: "org" })
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
              config.config_json_set
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
      emails.map((email) => authApi.createInvite(usersOrgId, email, inviteRole))
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
    const updated = await authApi.resendInvite(inviteId)
    setInvites((prev) =>
      prev.map((item) => (item.id === updated.id ? updated : item))
    )
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

  const updateInviteRole = async (invite: Invite, nextRole: string) => {
    if (!usersOrgId) return
    const updated = await authApi.updateInviteRole(invite.id, nextRole)
    setInvites((prev) =>
      prev.map((item) => (item.id === updated.id ? updated : item))
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
    const orgId = usersOrgId ?? selectedOrg
    if (!orgId) return
    try {
      await orgApi.removeMember(orgId, member.user_id)
      setMembers((prev) => prev.filter((item) => item.user_id !== member.user_id))
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common_error"))
    }
  }

  const createModel = async () => {
    if (!selectedOrg) return
    const trimmedName = modelName.trim()
    const matchedSuggestion = currentProviderSuggestions.find(
      (item) => item.model_name === trimmedName
    )
    const model = await modelApi.create({
      org_id: selectedOrg,
      provider: modelProvider,
      model_name: trimmedName,
      display_name: modelDisplayName || trimmedName,
      context_length: matchedSuggestion?.context_length ?? null,
      supports_image_input: matchedSuggestion?.supports_image_input ?? null,
      supports_image_output: matchedSuggestion?.supports_image_output ?? null,
      reasoning_effort: modelReasoningEffort,
      openrouter_endpoint:
        modelProvider === "openrouter" ? modelOpenRouterEndpoint || null : null,
      is_active: true,
    })
    await modelApi.setOrgModels(selectedOrg, [{ model_id: model.id, is_enabled: true }])
    await queryClient.invalidateQueries({ queryKey: ["models"] })
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
    setModelOpenRouterEndpoint("")
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

  const updateOpenRouterEndpoint = async (modelId: string, value: string) => {
    const updated = await modelApi.update(modelId, {
      openrouter_endpoint: value === OPENROUTER_ENDPOINT_AUTO ? "" : value,
    })
    setModels((prev) =>
      prev.map((model) =>
        model.id === modelId
          ? { ...model, openrouter_endpoint: updated.openrouter_endpoint }
          : model
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
      await queryClient.invalidateQueries({ queryKey: ["models"] })
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
    const fromSuggestions = modelSuggestions.map((item) => item.provider)
    return Array.from(new Set([...PROVIDERS, ...fromSuggestions]))
  }, [modelSuggestions])
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
  const endpointLabel = (endpoint: OpenRouterEndpoint) =>
    endpoint.quantization ? `${endpoint.tag} (${endpoint.quantization})` : endpoint.tag
  const currentOpenRouterEndpoints = openRouterEndpointsByModel[modelName.trim()] ?? []

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

  const updateRetentionDraft = (
    orgId: string,
    field: "fileRetentionDays" | "chatRetentionDays",
    value: string
  ) => {
    setRetentionDraftsByOrgId((prev) => {
      const current = prev[orgId]
      return {
        ...prev,
        [orgId]: {
          fileRetentionDays: current ? current.fileRetentionDays : "30",
          chatRetentionDays: current ? current.chatRetentionDays : "90",
          [field]: value,
        },
      }
    })
  }

  const saveOrgRetention = async (org: Org) => {
    const draft = retentionDraftsByOrgId[org.id]
    if (!draft) return
    const fileRetentionDays =
      draft.fileRetentionDays === null ? null : Number(draft.fileRetentionDays)
    const chatRetentionDays =
      draft.chatRetentionDays === null ? null : Number(draft.chatRetentionDays)
    if (
      (fileRetentionDays !== null &&
        (!Number.isInteger(fileRetentionDays) || fileRetentionDays < 1)) ||
      (chatRetentionDays !== null &&
        (!Number.isInteger(chatRetentionDays) || chatRetentionDays < 1))
    ) {
      setError("Retention periods must be whole numbers of at least one day.")
      return
    }
    const updated = await orgApi.update(org.id, {
      file_retention_days: fileRetentionDays,
      chat_retention_days: chatRetentionDays,
    })
    setOrgs((prev) => prev.map((item) => (item.id === updated.id ? updated : item)))
    setRetentionDraftsByOrgId((prev) => ({
      ...prev,
      [updated.id]: {
        fileRetentionDays:
          updated.file_retention_days === null ? null : String(updated.file_retention_days ?? 30),
        chatRetentionDays:
          updated.chat_retention_days === null ? null : String(updated.chat_retention_days ?? 90),
      },
    }))
    setError(null)
    setRetentionOrgId(null)
  }

  const neverExpireOrgRetention = (
    orgId: string,
    field: "fileRetentionDays" | "chatRetentionDays"
  ) => {
    setRetentionDraftsByOrgId((prev) => ({
      ...prev,
      [orgId]: {
        fileRetentionDays: prev[orgId]?.fileRetentionDays ?? "30",
        chatRetentionDays: prev[orgId]?.chatRetentionDays ?? "90",
        [field]: null,
      },
    }))
  }

  const openCostCeilingDialog = (org: Org) => {
    setCostCeilingDraftsByOrgId((prev) => ({
      ...prev,
      [org.id]:
        org.cost_ceiling_usd === null || org.cost_ceiling_usd === undefined
          ? null
          : String(org.cost_ceiling_usd),
    }))
    setCostCeilingOrgId(org.id)
  }

  const saveOrgCostCeiling = async (org: Org) => {
    const draft = costCeilingDraftsByOrgId[org.id]
    const costCeilingUsd =
      draft === null || draft === undefined || draft.trim() === "" ? null : Number(draft)
    if (
      costCeilingUsd !== null &&
      (!Number.isFinite(costCeilingUsd) || costCeilingUsd < 0)
    ) {
      setError(t("org_usage_limit_invalid"))
      return
    }
    const updated = await orgApi.update(org.id, {
      cost_ceiling_usd: costCeilingUsd,
    })
    setOrgs((prev) => prev.map((item) => (item.id === updated.id ? updated : item)))
    setCostCeilingDraftsByOrgId((prev) => ({
      ...prev,
      [updated.id]:
        updated.cost_ceiling_usd === null || updated.cost_ceiling_usd === undefined
          ? null
          : String(updated.cost_ceiling_usd),
    }))
    setError(null)
    setCostCeilingOrgId(null)
  }

  const updateMemberCostCeiling = async (member: OrgMember, rawValue: string) => {
    if (!usersOrgId) return
    const trimmed = rawValue.trim()
    const costCeilingUsd = trimmed === "" ? null : Number(trimmed)
    if (
      costCeilingUsd !== null &&
      (!Number.isFinite(costCeilingUsd) || costCeilingUsd < 0)
    ) {
      setError(t("org_usage_limit_invalid"))
      return
    }
    const updated = await orgApi.updateMember(usersOrgId, member.user_id, {
      cost_ceiling_usd: costCeilingUsd,
    })
    setMembers((prev) =>
      prev.map((item) => (item.user_id === updated.user_id ? updated : item))
    )
    setError(null)
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
          api_key_override: "",
          config_json: "",
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
      login_domains: authSettings.login_domains,
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
      label: t("org_section_teams"),
      href: "/settings/teams",
      visible: true,
      active: false,
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
      label: t("instance_providers_title"),
      href: "/settings/providers",
      visible: isSuperAdmin,
      active: location.pathname.startsWith("/settings/providers"),
    },
    {
      label: t("diagnosis_title"),
      href: "/settings/diagnosis",
      visible: isSuperAdmin,
      active: false,
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
          <Button variant="outline" onClick={() => navigate({ to: "/chat/{-$chatId}" })} disabled={!selectedOrg}>
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
                      className="flex flex-col gap-3 rounded-md border p-4"
                    >
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div className="flex flex-col gap-1">
                          <p className="font-medium">{org.name}</p>
                          <p className="text-muted-foreground text-xs">{org.id}</p>
                          {!org.is_active ? (
                            <p className="text-destructive text-xs">{t("org_deleted")}</p>
                          ) : org.is_frozen ? (
                            <p className="text-muted-foreground text-xs">{t("org_frozen")}</p>
                          ) : null}
                        </div>
                        <div className="flex flex-wrap items-center justify-end gap-2">
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
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => setRetentionOrgId(org.id)}
                          >
                            Retention
                          </Button>
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => openCostCeilingDialog(org)}
                          >
                            {t("org_usage_limit")}
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
                                          !org.is_active
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
                      </div>
                    </div>
                  ) : null}
                  {orgSettingsId
                    ? (() => {
                        const org = orgs.find((item) => item.id === orgSettingsId)
                        if (!org) return null
                        return (
                          <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border p-4">
                            <div>
                              <p className="font-medium text-sm">{t("org_usage_limit_title")}</p>
                              <p className="text-muted-foreground text-xs">
                                {org.cost_ceiling_usd === null ||
                                org.cost_ceiling_usd === undefined
                                  ? t("org_usage_limit_unlimited")
                                  : `$${org.cost_ceiling_usd}`}
                              </p>
                            </div>
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => openCostCeilingDialog(org)}
                              disabled={!org.is_active}
                            >
                              {t("org_usage_limit")}
                            </Button>
                          </div>
                        )
                      })()
                    : null}
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
                <div className="flex flex-col gap-3 sm:flex-row">
                  <Input
                    placeholder={t("org_users_invite_email")}
                    value={inviteEmail}
                    onChange={(event) => setInviteEmail(event.target.value)}
                    className="sm:flex-1"
                  />
                  <Select value={inviteRole} onValueChange={setInviteRole}>
                    <SelectTrigger className="w-full sm:w-40">
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
                </div>
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
                    <TableHead>{t("org_users_teams")}</TableHead>
                    {canManageOrgSettings ? (
                      <TableHead>{t("org_users_cost_ceiling")}</TableHead>
                    ) : null}
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
                      <TableCell className="text-muted-foreground text-sm">
                        {member.teams && member.teams.length > 0
                          ? member.teams.map((team) => team.name).join(", ")
                          : t("org_users_no_teams")}
                      </TableCell>
                      {canManageOrgSettings ? (
                        <TableCell>
                          <Input
                            type="number"
                            min="0"
                            step="0.01"
                            className="w-28"
                            defaultValue={
                              member.cost_ceiling_usd === null ||
                              member.cost_ceiling_usd === undefined
                                ? ""
                                : String(member.cost_ceiling_usd)
                            }
                            key={`${member.user_id}-${member.cost_ceiling_usd ?? "none"}`}
                            placeholder={t("org_usage_limit_unlimited")}
                            aria-label={t("org_users_cost_ceiling")}
                            onBlur={(event) => {
                              const next = event.target.value.trim()
                              const current =
                                member.cost_ceiling_usd === null ||
                                member.cost_ceiling_usd === undefined
                                  ? ""
                                  : String(member.cost_ceiling_usd)
                              if (next === current) return
                              void updateMemberCostCeiling(member, event.target.value)
                            }}
                          />
                        </TableCell>
                      ) : null}
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
                            type="button"
                            size="sm"
                            variant="outline"
                            onClick={() => void removeMember(member)}
                            disabled={member.user_id === currentUserId}
                          >
                            {t("org_users_remove_member")}
                          </Button>
                        ) : null}
                      </TableCell>
                    </TableRow>
                  ))}
                  {invites.map((invite) => {
                    const isExpired =
                      new Date(invite.expires_at).getTime() < Date.now()
                    return (
                    <TableRow key={invite.id}>
                      <TableCell>
                        <div className="flex flex-col">
                          <span>{invite.email}</span>
                          <span
                            className={
                              isExpired
                                ? "text-destructive text-xs"
                                : "text-muted-foreground text-xs"
                            }
                          >
                            {isExpired
                              ? t("org_users_expired")
                              : t("org_users_invited")}
                          </span>
                        </div>
                      </TableCell>
                      <TableCell>
                        {canManageOrgSettings ? (
                          <Select
                            value={invite.role || "member"}
                            onValueChange={(value) => updateInviteRole(invite, value)}
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
                          roleLabel(invite.role || "member")
                        )}
                      </TableCell>
                      <TableCell />
                      {canManageOrgSettings ? <TableCell /> : null}
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
                    )
                  })}
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
                      <div className="gap-3 grid md:grid-cols-4 xl:grid-cols-5">
                        <Select
                          value={modelProvider}
                          onValueChange={(value) => {
                            setModelProvider(value)
                            setModelOpenRouterEndpoint("")
                          }}
                        >
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
                            setModelOpenRouterEndpoint("")
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
                        {modelProvider === "openrouter" ? (
                          <Select
                            value={modelOpenRouterEndpoint || OPENROUTER_ENDPOINT_AUTO}
                            onValueChange={(value) =>
                              setModelOpenRouterEndpoint(
                                value === OPENROUTER_ENDPOINT_AUTO ? "" : value
                              )
                            }
                          >
                            <SelectTrigger>
                              <SelectValue placeholder={t("org_models_endpoint_placeholder")} />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value={OPENROUTER_ENDPOINT_AUTO}>
                                {t("org_models_endpoint_auto")}
                              </SelectItem>
                              {currentOpenRouterEndpoints.map((endpoint) => (
                                <SelectItem key={endpoint.tag} value={endpoint.tag}>
                                  {endpointLabel(endpoint)}
                                </SelectItem>
                              ))}
                              {modelOpenRouterEndpoint &&
                              !currentOpenRouterEndpoints.some(
                                (endpoint) => endpoint.tag === modelOpenRouterEndpoint
                              ) ? (
                                <SelectItem value={modelOpenRouterEndpoint}>
                                  {modelOpenRouterEndpoint}
                                </SelectItem>
                              ) : null}
                            </SelectContent>
                          </Select>
                        ) : null}
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
                      <DndContext
                        sensors={modelSensors}
                        collisionDetection={closestCenter}
                        onDragEnd={handleModelDragEnd}
                      >
                        <SortableContext
                          items={orderedModels.map((model) => model.id)}
                          strategy={verticalListSortingStrategy}
                        >
                      <div className="space-y-2">
                    {orderedModels.map((model) => (
                      <SortableModelRow key={model.id} id={model.id}>
                        {({ setNodeRef, style, attributes, listeners }) => (
                      <div
                        ref={setNodeRef}
                        style={style}
                        {...attributes}
                        className="flex justify-between items-center px-3 py-2 border rounded-md bg-background"
                      >
                        <div className="flex items-center gap-2">
                          {isSuperAdmin ? (
                            <button
                              type="button"
                              className="touch-none cursor-grab active:cursor-grabbing text-muted-foreground hover:text-foreground"
                              aria-label={t("org_reorder_model")}
                              {...listeners}
                            >
                              <GripVertical aria-hidden="true" className="w-4 h-4" />
                            </button>
                          ) : null}
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
                            {model.provider === "openrouter" && model.openrouter_endpoint
                              ? ` · ${model.openrouter_endpoint}`
                              : ""}
                          </p>
                        </div>
                        </div>
                        {isSuperAdmin ? (
                          <div className="flex flex-wrap items-center gap-2">
                            {model.provider === "openrouter" ? (
                              <Select
                                value={model.openrouter_endpoint || OPENROUTER_ENDPOINT_AUTO}
                                onValueChange={(value) =>
                                  updateOpenRouterEndpoint(model.id, value)
                                }
                              >
                                <SelectTrigger className="w-52 h-8">
                                  <SelectValue
                                    placeholder={t("org_models_endpoint_placeholder")}
                                  />
                                </SelectTrigger>
                                <SelectContent>
                                  <SelectItem value={OPENROUTER_ENDPOINT_AUTO}>
                                    {t("org_models_endpoint_auto")}
                                  </SelectItem>
                                  {(openRouterEndpointsByModel[model.model_name] ?? []).map(
                                    (endpoint) => (
                                      <SelectItem key={endpoint.tag} value={endpoint.tag}>
                                        {endpointLabel(endpoint)}
                                      </SelectItem>
                                    )
                                  )}
                                  {model.openrouter_endpoint &&
                                  !(openRouterEndpointsByModel[model.model_name] ?? []).some(
                                    (endpoint) => endpoint.tag === model.openrouter_endpoint
                                  ) ? (
                                    <SelectItem value={model.openrouter_endpoint}>
                                      {model.openrouter_endpoint}
                                    </SelectItem>
                                  ) : null}
                                </SelectContent>
                              </Select>
                            ) : null}
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
                        )}
                      </SortableModelRow>
                    ))}
                  </div>
                        </SortableContext>
                      </DndContext>
                      {orderedModels.length === 0 ? (
                      <p className="text-muted-foreground text-sm">
                        {t("org_models_no_models")}
                      </p>
                    ) : null}
                </CardContent>
              </Card>
            ) : null}

            {isSuperAdmin && orgs.length > 1 ? (
              <Card>
                <CardHeader>
                  <CardTitle>{t("org_models_access")}</CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
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
              placeholder={t("org_auth_login_domains")}
              value={authSettings?.login_domains?.join(", ") ?? ""}
              onChange={(event) =>
                updateAuthField(
                  "login_domains",
                  event.target.value
                    .split(/[,;\s]+/)
                    .map((item) => item.trim())
                    .filter(Boolean)
                )
              }
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
                        placeholder={
                          config.config_json_set
                            ? t("org_provider_override_set")
                            : t("org_provider_vertex_config_placeholder")
                        }
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

      <Dialog open={Boolean(retentionOrg)} onOpenChange={(open) => (!open ? setRetentionOrgId(null) : null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Retention policy</DialogTitle>
            <DialogDescription>
              Set how long inactive chat files and chat history are retained for {retentionOrg?.name}.
            </DialogDescription>
          </DialogHeader>
          {retentionOrg ? (
            <div className="flex flex-col gap-4">
              <div className="flex flex-col gap-2">
                <p className="font-medium text-sm">Uploaded and generated files</p>
                <div className="flex flex-wrap items-center gap-2">
                  <Input
                    type="number"
                    min="1"
                    className="w-24"
                    aria-label={`File retention days for ${retentionOrg.name}`}
                    placeholder="30"
                    value={retentionDraftsByOrgId[retentionOrg.id]?.fileRetentionDays ?? ""}
                    onChange={(event) =>
                      updateRetentionDraft(retentionOrg.id, "fileRetentionDays", event.target.value)
                    }
                    disabled={!retentionOrg.is_active}
                  />
                  <span className="text-muted-foreground text-sm">days after last chat activity</span>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => neverExpireOrgRetention(retentionOrg.id, "fileRetentionDays")}
                    disabled={!retentionOrg.is_active}
                  >
                    Never expire
                  </Button>
                </div>
                {retentionDraftsByOrgId[retentionOrg.id]?.fileRetentionDays === null ? (
                  <p className="text-muted-foreground text-sm">Files are retained indefinitely.</p>
                ) : null}
              </div>
              <div className="flex flex-col gap-2">
                <p className="font-medium text-sm">Chat history</p>
                <div className="flex flex-wrap items-center gap-2">
                  <Input
                    type="number"
                    min="1"
                    className="w-24"
                    aria-label={`Chat retention days for ${retentionOrg.name}`}
                    placeholder="90"
                    value={retentionDraftsByOrgId[retentionOrg.id]?.chatRetentionDays ?? ""}
                    onChange={(event) =>
                      updateRetentionDraft(retentionOrg.id, "chatRetentionDays", event.target.value)
                    }
                    disabled={!retentionOrg.is_active}
                  />
                  <span className="text-muted-foreground text-sm">days after last chat activity</span>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => neverExpireOrgRetention(retentionOrg.id, "chatRetentionDays")}
                    disabled={!retentionOrg.is_active}
                  >
                    Never expire
                  </Button>
                </div>
                {retentionDraftsByOrgId[retentionOrg.id]?.chatRetentionDays === null ? (
                  <p className="text-muted-foreground text-sm">Chats are retained indefinitely.</p>
                ) : null}
              </div>
            </div>
          ) : null}
          <DialogFooter>
            <Button variant="outline" onClick={() => setRetentionOrgId(null)}>
              {t("common_cancel")}
            </Button>
            <Button
              onClick={() => retentionOrg && saveOrgRetention(retentionOrg)}
              disabled={!retentionOrg?.is_active}
            >
              {t("common_save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={Boolean(costCeilingOrg)}
        onOpenChange={(open) => (!open ? setCostCeilingOrgId(null) : null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("org_usage_limit_title")}</DialogTitle>
            <DialogDescription>
              {t("org_usage_limit_desc")}
              {costCeilingOrg ? ` (${costCeilingOrg.name})` : ""}
            </DialogDescription>
          </DialogHeader>
          {costCeilingOrg ? (
            <div className="flex flex-col gap-3">
              <label className="font-medium text-sm" htmlFor="org-cost-ceiling">
                {t("org_usage_limit_usd")}
              </label>
              <div className="flex flex-wrap items-center gap-2">
                <Input
                  id="org-cost-ceiling"
                  type="number"
                  min="0"
                  step="0.01"
                  className="w-40"
                  placeholder={t("org_usage_limit_unlimited")}
                  value={costCeilingDraftsByOrgId[costCeilingOrg.id] ?? ""}
                  onChange={(event) =>
                    setCostCeilingDraftsByOrgId((prev) => ({
                      ...prev,
                      [costCeilingOrg.id]: event.target.value,
                    }))
                  }
                  disabled={!costCeilingOrg.is_active}
                />
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() =>
                    setCostCeilingDraftsByOrgId((prev) => ({
                      ...prev,
                      [costCeilingOrg.id]: null,
                    }))
                  }
                  disabled={!costCeilingOrg.is_active}
                >
                  {t("org_usage_limit_clear")}
                </Button>
              </div>
              {costCeilingDraftsByOrgId[costCeilingOrg.id] === null ||
              costCeilingDraftsByOrgId[costCeilingOrg.id] === "" ? (
                <p className="text-muted-foreground text-sm">{t("org_usage_limit_unlimited")}</p>
              ) : null}
            </div>
          ) : null}
          <DialogFooter>
            <Button variant="outline" onClick={() => setCostCeilingOrgId(null)}>
              {t("common_cancel")}
            </Button>
            <Button
              onClick={() => costCeilingOrg && saveOrgCostCeiling(costCeilingOrg)}
              disabled={!costCeilingOrg?.is_active}
            >
              {t("common_save")}
            </Button>
          </DialogFooter>
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
