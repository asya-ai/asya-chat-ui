import { useCallback, useEffect, useMemo, useState } from "react"
import { Info, Settings2 } from "lucide-react"

import { SettingsShell } from "@/components/SettingsShell"
import { SettingsPage, SettingsPageHeader } from "@/components/settings/SettingsPanel"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
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
import { Textarea } from "@/components/ui/textarea"
import { authApi, integrationsApi, mcpApi, orgApi } from "@/lib/api"
import { useI18n } from "@/lib/i18n-context"
import { orgStore } from "@/lib/storage"
import { cn } from "@/lib/utils"
import type {
  CatalogIntegrationStatus,
  McpAuthType,
  McpBinding,
  McpBindingMode,
  McpServer,
  McpServerWrite,
  McpSettings,
  McpTransport,
  McpUserAuthMethod,
  OAuthProviderConfig,
  Org,
} from "@/lib/types"

type ServerScope = "instance" | "org" | "user"

const emptyServerForm = (): McpServerWrite => ({
  slug: "",
  name: "",
  description: "",
  transport: "http",
  url: "",
  command: "",
  args: [],
  stdio_env: {},
  auth_type: "none",
  include_tools: true,
  include_resources: true,
  include_prompts: true,
  is_enabled: true,
})

export const IntegrationsPage = () => {
  const { t } = useI18n()
  const [authChecked, setAuthChecked] = useState(false)
  const [isSuperAdmin, setIsSuperAdmin] = useState(false)
  const [isAdmin, setIsAdmin] = useState(false)
  const [orgs, setOrgs] = useState<Org[]>([])
  const [selectedOrgId, setSelectedOrgId] = useState<string | null>(orgStore.get())
  const [instanceSettings, setInstanceSettings] = useState<McpSettings | null>(null)
  const [instanceServers, setInstanceServers] = useState<McpServer[]>([])
  const [orgServers, setOrgServers] = useState<McpServer[]>([])
  const [bindings, setBindings] = useState<McpBinding[]>([])
  const [orgAllowUserServers, setOrgAllowUserServers] = useState(true)
  const [overviewServers, setOverviewServers] = useState<McpServer[]>([])
  const [personalServers, setPersonalServers] = useState<McpServer[]>([])
  const [policy, setPolicy] = useState({
    allow_org_servers: false,
    allow_user_servers: false,
    org_allow_user_servers: true as boolean | null,
  })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [dialogScope, setDialogScope] = useState<ServerScope>("instance")
  const [editingServer, setEditingServer] = useState<McpServer | null>(null)
  const [form, setForm] = useState<McpServerWrite>(emptyServerForm())
  const [connectionTokens, setConnectionTokens] = useState<Record<string, string>>({})
  const [catalogApps, setCatalogApps] = useState<CatalogIntegrationStatus[]>([])
  const [catalogKeys, setCatalogKeys] = useState<Record<string, string>>({})
  const [catalogUrlDrafts, setCatalogUrlDrafts] = useState<Record<string, string>>({})
  const [catalogOriginsDrafts, setCatalogOriginsDrafts] = useState<Record<string, string>>({})
  const [embedCopied, setEmbedCopied] = useState<string | null>(null)
  const [configuringAppId, setConfiguringAppId] = useState<string | null>(null)
  const [connectingAppId, setConnectingAppId] = useState<string | null>(null)
  const [oauthProviders, setOauthProviders] = useState<OAuthProviderConfig[]>([])
  const [oauthProviderDrafts, setOauthProviderDrafts] = useState<
    Record<string, Record<string, string>>
  >({})
  const [entraSetupOpen, setEntraSetupOpen] = useState(false)

  const microsoftGraphPermissions = useMemo(() => {
    const scopes = new Set<string>()
    for (const app of catalogApps) {
      if (app.oauth_provider_id !== "microsoft") continue
      for (const scope of app.oauth_scopes ?? []) {
        const name = scope.includes("/") ? scope.split("/").pop() : scope
        if (
          name &&
          name !== "openid" &&
          name !== "profile" &&
          name !== "offline_access"
        ) {
          scopes.add(name)
        }
      }
    }
    return [...scopes].sort((a, b) => a.localeCompare(b))
  }, [catalogApps])

  const canManageOrg = isSuperAdmin || isAdmin
  // Instance catalog setup (MCP URL, embed origins, offer) — not for org managers/members.
  const canConfigureCatalogApps = isSuperAdmin
  const configuringApp =
    catalogApps.find((app) => app.catalog_id === configuringAppId) ?? null
  const connectingApp =
    catalogApps.find((app) => app.catalog_id === connectingAppId) ?? null
  const oauthCallbackUrl =
    typeof window !== "undefined"
      ? `${window.location.origin}/api/integrations/oauth/callback`
      : "/api/integrations/oauth/callback"

  const loadAll = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const orgList = isSuperAdmin ? await orgApi.list() : await orgApi.mine()
      setOrgs(orgList)
      const orgId = selectedOrgId && orgList.some((o) => o.id === selectedOrgId)
        ? selectedOrgId
        : orgList[0]?.id ?? null
      if (orgId !== selectedOrgId) {
        setSelectedOrgId(orgId)
        if (orgId) orgStore.set(orgId)
      }

      const tasks: Promise<void>[] = [
        mcpApi.overview(orgId ?? undefined).then((overview) => {
          setOverviewServers(
            overview.user_provided_servers.filter((s) => s.origin !== "catalog")
          )
          setPersonalServers(overview.personal_servers)
          setPolicy({
            allow_org_servers: overview.policy.allow_org_servers,
            allow_user_servers: overview.policy.allow_user_servers,
            org_allow_user_servers: overview.policy.org_allow_user_servers ?? null,
          })
        }),
        integrationsApi.listCatalog(orgId ?? undefined).then((apps) => {
          setCatalogApps(apps)
          setCatalogUrlDrafts((prev) => {
            const next = { ...prev }
            for (const app of apps) {
              const mcpField = app.admin_config_fields.find((f) => f.key === "mcp_url")
              if (!mcpField) continue
              if (next[app.catalog_id] == null) {
                next[app.catalog_id] = app.effective_mcp_url ?? mcpField.default ?? ""
              }
            }
            return next
          })
          setCatalogOriginsDrafts((prev) => {
            const next = { ...prev }
            for (const app of apps) {
              if (app.catalog_id !== "pitchpatterns") continue
              if (next[app.catalog_id] == null) {
                next[app.catalog_id] = app.allowed_embed_origins ?? ""
              }
            }
            return next
          })
        }),
      ]

      if (isSuperAdmin) {
        tasks.push(
          mcpApi.instanceSettings().then(setInstanceSettings),
          mcpApi.listInstanceServers().then((servers) =>
            setInstanceServers(servers.filter((s) => s.origin !== "catalog"))
          ),
          integrationsApi.listInstanceProviders().then((providers) => {
            setOauthProviders(providers)
            setOauthProviderDrafts((prev) => {
              const next = { ...prev }
              for (const provider of providers) {
                if (next[provider.provider_id]) continue
                const draft: Record<string, string> = {}
                for (const field of provider.fields) {
                  if (field.readonly || field.key === "redirect_uri") continue
                  const existing = provider.values?.[field.key]
                  draft[field.key] = typeof existing === "string" ? existing : ""
                }
                next[provider.provider_id] = draft
              }
              return next
            })
          })
        )
      }
      if (canManageOrg && orgId) {
        tasks.push(
          mcpApi.orgSettings(orgId).then((s) => setOrgAllowUserServers(s.allow_user_servers)),
          mcpApi.listOrgServers(orgId).then(setOrgServers),
          mcpApi.listBindings(orgId).then((rows) =>
            setBindings(rows.filter((b) => !b.instance_server_slug.startsWith("catalog-")))
          )
        )
      }
      await Promise.all(tasks)
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common_error"))
    } finally {
      setLoading(false)
    }
  }, [canManageOrg, isSuperAdmin, oauthCallbackUrl, selectedOrgId, t])

  useEffect(() => {
    authApi
      .me()
      .then((me) => {
        setIsSuperAdmin(me.is_super_admin)
        setIsAdmin(me.is_admin)
        setAuthChecked(true)
      })
      .catch(() => setAuthChecked(true))
  }, [])

  useEffect(() => {
    if (!authChecked) return
    void loadAll()
  }, [authChecked, loadAll])

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const oauth = params.get("oauth")
    if (!oauth) return
    if (oauth === "connected") {
      setError(null)
    } else if (oauth === "error") {
      setError(params.get("detail") || t("common_error"))
    }
    params.delete("oauth")
    params.delete("catalog_id")
    params.delete("detail")
    const next = params.toString()
    const url = `${window.location.pathname}${next ? `?${next}` : ""}`
    window.history.replaceState({}, "", url)
  }, [t])

  const openCreateDialog = (scope: ServerScope) => {
    setDialogScope(scope)
    setEditingServer(null)
    setForm(emptyServerForm())
    setFormError(null)
    setDialogOpen(true)
  }

  const openEditDialog = (scope: ServerScope, server: McpServer) => {
    setDialogScope(scope)
    setEditingServer(server)
    setForm({
      slug: server.slug,
      name: server.name,
      description: server.description ?? "",
      transport: server.transport,
      url: server.url ?? "",
      command: server.command ?? "",
      args: server.args ?? [],
      stdio_env: server.stdio_env ?? {},
      auth_type: server.auth_type,
      include_tools: server.include_tools,
      include_resources: server.include_resources,
      include_prompts: server.include_prompts,
      user_auth_method:
        server.user_auth_method === "bearer" || server.user_auth_method === "api_token"
          ? server.user_auth_method
          : "bearer",
      user_instructions: server.user_instructions ?? "",
      header_name: server.api_token_header_name ?? "",
      header_format: server.api_token_header_format ?? "",
      is_enabled: server.is_enabled,
    })
    setFormError(null)
    setDialogOpen(true)
  }

  const saveServer = async () => {
    setFormError(null)
    try {
      const payload = { ...form }
      if (dialogScope === "instance") {
        if (editingServer) {
          await mcpApi.updateInstanceServer(editingServer.id, payload)
        } else {
          await mcpApi.createInstanceServer(payload)
        }
      } else if (dialogScope === "org" && selectedOrgId) {
        if (editingServer) {
          await mcpApi.updateOrgServer(selectedOrgId, editingServer.id, payload)
        } else {
          await mcpApi.createOrgServer(selectedOrgId, payload)
        }
      } else {
        if (editingServer) {
          await mcpApi.updatePersonalServer(editingServer.id, payload)
        } else {
          await mcpApi.createPersonalServer(payload, selectedOrgId ?? undefined)
        }
      }
      setDialogOpen(false)
      await loadAll()
    } catch (err) {
      setFormError(err instanceof Error ? err.message : t("common_error"))
    }
  }

  const deleteServer = async (scope: ServerScope, server: McpServer) => {
    setError(null)
    try {
      if (scope === "instance") await mcpApi.deleteInstanceServer(server.id)
      else if (scope === "org" && selectedOrgId) await mcpApi.deleteOrgServer(selectedOrgId, server.id)
      else await mcpApi.deletePersonalServer(server.id)
      await loadAll()
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common_error"))
    }
  }

  const testServer = async (scope: ServerScope, server: McpServer) => {
    setError(null)
    try {
      const result =
        scope === "instance"
          ? await mcpApi.testInstanceServer(server.id)
          : scope === "org" && selectedOrgId
            ? await mcpApi.testOrgServer(selectedOrgId, server.id)
            : await mcpApi.testPersonalServer(server.id)
      alert(
        result.status === "ok"
          ? t("mcp_test_ok", { tools: String(result.tools ?? 0) })
          : result.detail ?? result.status
      )
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common_error"))
    }
  }

  const testConnection = async (server: McpServer) => {
    setError(null)
    try {
      const result = await mcpApi.testConnection(server.id, selectedOrgId ?? undefined)
      alert(
        result.status === "ok"
          ? t("mcp_test_ok", { tools: String(result.tools ?? 0) })
          : result.detail ?? result.status
      )
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common_error"))
    }
  }

  const saveInstancePolicy = async (patch: Partial<McpSettings>) => {
    if (!instanceSettings) return
    const updated = await mcpApi.updateInstanceSettings(patch)
    setInstanceSettings(updated)
    await loadAll()
  }

  const saveOrgPolicy = async (allow: boolean) => {
    if (!selectedOrgId) return
    await mcpApi.updateOrgSettings(selectedOrgId, { allow_user_servers: allow })
    setOrgAllowUserServers(allow)
    await loadAll()
  }

  const saveBinding = async (binding: McpBinding, mode: McpBindingMode) => {
    if (!selectedOrgId) return
    await mcpApi.upsertBinding(selectedOrgId, binding.instance_server_id, { mode })
    await loadAll()
  }

  const saveConnectionToken = async (server: McpServer) => {
    const token = connectionTokens[server.id]?.trim()
    if (!token) return
    await mcpApi.upsertConnection(server.id, { token })
    setConnectionTokens((prev) => ({ ...prev, [server.id]: "" }))
    await loadAll()
  }

  const disconnect = async (server: McpServer) => {
    await mcpApi.deleteConnection(server.id)
    await loadAll()
  }

  const saveCatalogUrl = async (app: CatalogIntegrationStatus, scope: "instance" | "org") => {
    const url = (catalogUrlDrafts[app.catalog_id] ?? "").trim()
    if (!url) return
    setError(null)
    try {
      const origins = (catalogOriginsDrafts[app.catalog_id] ?? "").trim()
      const values: Record<string, string> = { mcp_url: url }
      if (app.catalog_id === "pitchpatterns") {
        values.allowed_embed_origins = origins
      }
      if (scope === "instance") {
        await integrationsApi.upsertInstanceCatalogConfig(app.catalog_id, {
          is_enabled: true,
          values,
        })
        await integrationsApi.setInstanceEnablement(app.catalog_id, true)
      } else if (selectedOrgId) {
        await integrationsApi.upsertOrgCatalogConfig(selectedOrgId, app.catalog_id, {
          is_enabled: true,
          values,
        })
        await integrationsApi.setOrgEnablement(selectedOrgId, app.catalog_id, true)
      }
      await loadAll()
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common_error"))
    }
  }

  const copyEmbedUrl = async (catalogId: string) => {
    const url = `${window.location.origin}/embed/${catalogId}`
    try {
      await navigator.clipboard.writeText(url)
      setEmbedCopied(catalogId)
      window.setTimeout(() => setEmbedCopied(null), 1500)
    } catch {
      setError(t("common_error"))
    }
  }

  const toggleCatalogEnabled = async (app: CatalogIntegrationStatus, enabled: boolean) => {
    setError(null)
    try {
      if (isSuperAdmin && !selectedOrgId) {
        await integrationsApi.setInstanceEnablement(app.catalog_id, enabled)
      } else if (selectedOrgId && canManageOrg) {
        await integrationsApi.setOrgEnablement(selectedOrgId, app.catalog_id, enabled)
      } else if (isSuperAdmin) {
        await integrationsApi.setInstanceEnablement(app.catalog_id, enabled)
      }
      await loadAll()
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common_error"))
    }
  }

  const connectCatalogApp = async (app: CatalogIntegrationStatus) => {
    if (!selectedOrgId) return
    const key = (catalogKeys[app.catalog_id] ?? "").trim()
    if (!key) return
    setError(null)
    try {
      await integrationsApi.connect(app.catalog_id, selectedOrgId, key)
      setCatalogKeys((prev) => ({ ...prev, [app.catalog_id]: "" }))
      setConnectingAppId(null)
      await loadAll()
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common_error"))
    }
  }

  const connectCatalogOAuth = async (app: CatalogIntegrationStatus) => {
    if (!selectedOrgId) return
    setError(null)
    try {
      const { authorize_url } = await integrationsApi.oauthStart(
        app.catalog_id,
        selectedOrgId
      )
      window.location.assign(authorize_url)
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common_error"))
    }
  }

  const connectAllMicrosoftOAuth = async () => {
    if (!selectedOrgId) return
    setError(null)
    try {
      const { authorize_url } = await integrationsApi.oauthStartProvider(
        "microsoft",
        selectedOrgId
      )
      window.location.assign(authorize_url)
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common_error"))
    }
  }

  const saveOauthProvider = async (provider: OAuthProviderConfig) => {
    const draft = oauthProviderDrafts[provider.provider_id] ?? {}
    setError(null)
    try {
      const values: Record<string, string> = { ...draft }
      // Callback URL is fixed; never take a hand-edited value.
      if (provider.fields.some((f) => f.key === "redirect_uri")) {
        values.redirect_uri = oauthCallbackUrl
      }
      if (provider.provider_id === "microsoft" && !values.tenant_id?.trim()) {
        values.tenant_id = "common"
      }
      await integrationsApi.upsertInstanceProvider(provider.provider_id, {
        is_enabled: true,
        values,
      })
      await loadAll()
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common_error"))
    }
  }

  const disconnectCatalogApp = async (app: CatalogIntegrationStatus) => {
    if (!selectedOrgId) return
    setError(null)
    try {
      await integrationsApi.disconnect(app.catalog_id, selectedOrgId)
      await loadAll()
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common_error"))
    }
  }

  const testCatalogApp = async (app: CatalogIntegrationStatus) => {
    if (!selectedOrgId) return
    setError(null)
    try {
      const result = await integrationsApi.test(app.catalog_id, selectedOrgId)
      alert(
        result.status === "ok"
          ? t("mcp_test_ok", { tools: String(result.tools ?? 0) })
          : result.detail ?? result.status
      )
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common_error"))
    }
  }

  const renderAuthFields = () => (
    <div className="space-y-3">
      <div className="space-y-1">
        <p className="text-sm font-medium">{t("mcp_auth_type")}</p>
        <Select
          value={form.auth_type ?? "none"}
          onValueChange={(value) => setForm((prev) => ({ ...prev, auth_type: value as McpAuthType }))}
        >
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="none">{t("mcp_auth_none")}</SelectItem>
            <SelectItem value="bearer">{t("mcp_auth_bearer")}</SelectItem>
            <SelectItem value="api_token">{t("mcp_auth_api_token")}</SelectItem>
            <SelectItem value="user_provided">{t("mcp_auth_user_provided")}</SelectItem>
          </SelectContent>
        </Select>
      </div>
      {(form.auth_type === "bearer" || form.auth_type === "api_token") && (
        <Input
          type="password"
          placeholder={
            editingServer?.token_set ? t("org_provider_override_set") : t("mcp_token_placeholder")
          }
          value={form.token ?? ""}
          onChange={(e) => setForm((prev) => ({ ...prev, token: e.target.value }))}
        />
      )}
      {form.auth_type === "api_token" && (
        <>
          <Input
            placeholder={t("mcp_header_name")}
            value={form.header_name ?? ""}
            onChange={(e) => setForm((prev) => ({ ...prev, header_name: e.target.value }))}
          />
          <Input
            placeholder={t("mcp_header_format")}
            value={form.header_format ?? ""}
            onChange={(e) => setForm((prev) => ({ ...prev, header_format: e.target.value }))}
          />
        </>
      )}
      {form.auth_type === "user_provided" && (
        <>
          <div className="space-y-1">
            <p className="text-sm font-medium">{t("mcp_user_auth_method")}</p>
            <Select
              value={form.user_auth_method ?? "bearer"}
              onValueChange={(value) =>
                setForm((prev) => ({ ...prev, user_auth_method: value as McpUserAuthMethod }))
              }
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="bearer">{t("mcp_method_bearer")}</SelectItem>
                <SelectItem value="api_token">{t("mcp_method_api_token")}</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <Textarea
            placeholder={t("mcp_user_instructions")}
            value={form.user_instructions ?? ""}
            onChange={(e) => setForm((prev) => ({ ...prev, user_instructions: e.target.value }))}
          />
          {form.user_auth_method === "api_token" && (
            <>
              <Input
                placeholder={t("mcp_header_name")}
                value={form.header_name ?? ""}
                onChange={(e) => setForm((prev) => ({ ...prev, header_name: e.target.value }))}
              />
              <Input
                placeholder={t("mcp_header_format")}
                value={form.header_format ?? ""}
                onChange={(e) => setForm((prev) => ({ ...prev, header_format: e.target.value }))}
              />
            </>
          )}
        </>
      )}
    </div>
  )

  const renderServerCard = (scope: ServerScope, server: McpServer) => (
    <Card key={server.id}>
      <CardHeader className="flex flex-row items-start justify-between gap-3 pb-2">
        <div>
          <CardTitle className="text-base font-medium">{server.name}</CardTitle>
          <p className="font-mono text-xs text-muted-foreground">{server.slug}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Badge variant="outline">{server.transport}</Badge>
          <Badge variant="secondary">{server.auth_type}</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {server.description ? (
          <p className="text-sm text-muted-foreground">{server.description}</p>
        ) : null}
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="outline" onClick={() => openEditDialog(scope, server)}>
            {t("common_edit")}
          </Button>
          <Button size="sm" variant="outline" onClick={() => void testServer(scope, server)}>
            {t("mcp_test")}
          </Button>
          <Button size="sm" variant="outline" onClick={() => void deleteServer(scope, server)}>
            {t("common_delete")}
          </Button>
        </div>
      </CardContent>
    </Card>
  )

  const orgSectionVisible = canManageOrg && selectedOrgId
  const visibleCatalogApps = canConfigureCatalogApps
    ? catalogApps
    : catalogApps.filter((app) => app.installable || app.connected)
  const showCatalogSection = canConfigureCatalogApps || visibleCatalogApps.length > 0
  const microsoftConnectable = visibleCatalogApps.filter(
    (app) =>
      app.oauth_provider_id === "microsoft" &&
      app.connect_kind === "oauth" &&
      app.installable
  )
  const showMicrosoftConnectAll =
    Boolean(selectedOrgId) && microsoftConnectable.length > 1

  return (
    <SettingsShell title={t("integrations_title")}>
      <SettingsPage wide>
        <SettingsPageHeader
          title={t("integrations_title")}
          description={t("integrations_description")}
        />
        {error ? <p className="text-sm text-destructive">{error}</p> : null}
        {loading ? <p className="text-sm text-muted-foreground">{t("common_loading")}</p> : null}

        {showCatalogSection ? (
          <section className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 className="text-lg font-medium">{t("integrations_apps")}</h2>
              <div className="flex flex-wrap items-center gap-2">
                {showMicrosoftConnectAll ? (
                  <Button
                    size="sm"
                    onClick={() => void connectAllMicrosoftOAuth()}
                  >
                    {t("integrations_connect_all_microsoft")}
                  </Button>
                ) : null}
                {(orgs.length > 1 || isSuperAdmin) && selectedOrgId ? (
                  <Select
                    value={selectedOrgId}
                    onValueChange={(value) => {
                      setSelectedOrgId(value)
                      orgStore.set(value)
                    }}
                  >
                    <SelectTrigger className="w-56">
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
              </div>
            </div>
            {visibleCatalogApps.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t("integrations_apps_empty")}</p>
            ) : (
              <div className="grid gap-3 sm:grid-cols-2">
                {visibleCatalogApps.map((app) => {
                  const canConnect =
                    selectedOrgId &&
                    (app.installable || app.connected) &&
                    (app.connect_kind === "bearer_api_key" ||
                      app.connect_kind === "oauth")
                  const statusLabel = app.connected
                    ? t("mcp_connected")
                    : !app.configured && canConfigureCatalogApps
                      ? t("integrations_setup_needed")
                      : !app.enabled && canConfigureCatalogApps
                        ? t("integrations_disabled")
                        : canConnect
                          ? t("mcp_not_connected")
                          : null
                  return (
                    <Card key={app.catalog_id} className="gap-4 py-5">
                      <CardHeader className="flex flex-row items-start gap-3 space-y-0">
                        <div
                          className={cn(
                            "flex size-11 shrink-0 items-center justify-center rounded-xl",
                            "bg-muted text-base font-semibold tracking-tight text-foreground"
                          )}
                          aria-hidden
                        >
                          {app.name.slice(0, 1).toUpperCase()}
                        </div>
                        <div className="min-w-0 flex-1 space-y-1">
                          <div className="flex items-start justify-between gap-2">
                            <CardTitle className="font-sans text-base font-medium leading-5">
                              {app.name}
                            </CardTitle>
                            <div className="flex shrink-0 items-center gap-1">
                              {statusLabel ? (
                                <Badge
                                  variant={app.connected ? "secondary" : "outline"}
                                  className="whitespace-nowrap"
                                >
                                  {statusLabel}
                                </Badge>
                              ) : null}
                              {canConfigureCatalogApps ? (
                                <Button
                                  type="button"
                                  size="icon-sm"
                                  variant="ghost"
                                  aria-label={t("common_settings")}
                                  onClick={() => setConfiguringAppId(app.catalog_id)}
                                >
                                  <Settings2 />
                                </Button>
                              ) : null}
                            </div>
                          </div>
                          {app.description ? (
                            <CardDescription className="text-sm leading-5">
                              {app.description}
                            </CardDescription>
                          ) : null}
                        </div>
                      </CardHeader>
                      {canConnect ? (
                        <CardContent className="flex flex-wrap gap-2">
                          {app.connected ? (
                            <>
                              {app.connect_kind === "bearer_api_key" ? (
                                <Button
                                  size="sm"
                                  variant="outline"
                                  onClick={() => setConnectingAppId(app.catalog_id)}
                                >
                                  {t("integrations_update_key")}
                                </Button>
                              ) : (
                                <Button
                                  size="sm"
                                  variant="outline"
                                  onClick={() => void connectCatalogOAuth(app)}
                                >
                                  {t("integrations_reconnect")}
                                </Button>
                              )}
                              <Button
                                size="sm"
                                variant="outline"
                                onClick={() => void disconnectCatalogApp(app)}
                              >
                                {t("mcp_disconnect")}
                              </Button>
                            </>
                          ) : app.connect_kind === "oauth" ? (
                            <Button
                              size="sm"
                              onClick={() => void connectCatalogOAuth(app)}
                            >
                              {t("integrations_connect")}
                            </Button>
                          ) : (
                            <Button
                              size="sm"
                              onClick={() => setConnectingAppId(app.catalog_id)}
                            >
                              {t("integrations_connect")}
                            </Button>
                          )}
                        </CardContent>
                      ) : null}
                    </Card>
                  )
                })}
              </div>
            )}
          </section>
        ) : null}

        {isSuperAdmin && oauthProviders.length > 0 ? (
          <section className="space-y-4">
            <div>
              <h2 className="text-lg font-medium">{t("integrations_oauth_providers")}</h2>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              {oauthProviders.map((provider) => {
                const draft = oauthProviderDrafts[provider.provider_id] ?? {}
                return (
                  <Card key={provider.provider_id} className="gap-4 py-5">
                    <CardHeader className="pb-0">
                      <div className="flex items-start justify-between gap-2">
                        <CardTitle className="font-sans text-base font-medium leading-5">
                          {provider.name}
                        </CardTitle>
                        {provider.provider_id === "microsoft" ? (
                          <Button
                            type="button"
                            size="icon-sm"
                            variant="ghost"
                            aria-label={t("integrations_entra_setup")}
                            onClick={() => setEntraSetupOpen(true)}
                          >
                            <Info />
                          </Button>
                        ) : null}
                      </div>
                      <CardDescription className="text-sm">
                        {provider.description}
                      </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-3">
                      {provider.fields.map((field) => {
                        if (field.readonly || field.key === "redirect_uri") {
                          return (
                            <div key={field.key} className="space-y-1.5">
                              <p className="text-sm font-medium">{field.label}</p>
                              <div className="flex flex-wrap gap-2">
                                <Input
                                  readOnly
                                  className="min-w-0 flex-1 font-mono text-xs"
                                  value={oauthCallbackUrl}
                                />
                                <Button
                                  size="sm"
                                  variant="outline"
                                  onClick={() => {
                                    void navigator.clipboard.writeText(oauthCallbackUrl)
                                  }}
                                >
                                  {t("integrations_embed_copy")}
                                </Button>
                              </div>
                            </div>
                          )
                        }
                        return (
                          <div key={field.key} className="space-y-1.5">
                            <p className="text-sm font-medium">{field.label}</p>
                            <Input
                              type={field.secret ? "password" : "text"}
                              placeholder={
                                field.secret &&
                                Boolean(
                                  (provider as Record<string, unknown>)[`${field.key}_set`]
                                )
                                  ? t("org_provider_override_set")
                                  : field.placeholder ?? ""
                              }
                              value={draft[field.key] ?? ""}
                              onChange={(e) =>
                                setOauthProviderDrafts((prev) => ({
                                  ...prev,
                                  [provider.provider_id]: {
                                    ...(prev[provider.provider_id] ?? {}),
                                    [field.key]: e.target.value,
                                  },
                                }))
                              }
                            />
                          </div>
                        )
                      })}
                      <Button size="sm" onClick={() => void saveOauthProvider(provider)}>
                        {t("integrations_save_provider")}
                      </Button>
                    </CardContent>
                  </Card>
                )
              })}
            </div>
          </section>
        ) : null}

        {isSuperAdmin ? (
          <section className="space-y-4">
            <h2 className="text-lg font-medium">{t("mcp_instance_policy")}</h2>
            <Card>
              <CardContent className="space-y-4 pt-6">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-sm">{t("mcp_allow_org_servers")}</span>
                  <Switch
                    checked={instanceSettings?.allow_org_servers ?? false}
                    onCheckedChange={(checked) => void saveInstancePolicy({ allow_org_servers: checked })}
                  />
                </div>
                <div className="flex items-center justify-between gap-3">
                  <span className="text-sm">{t("mcp_allow_user_servers")}</span>
                  <Switch
                    checked={instanceSettings?.allow_user_servers ?? false}
                    onCheckedChange={(checked) => void saveInstancePolicy({ allow_user_servers: checked })}
                  />
                </div>
              </CardContent>
            </Card>
          </section>
        ) : null}

        {isSuperAdmin ? (
          <section className="space-y-4">
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-lg font-medium">{t("mcp_instance_servers")}</h2>
              <Button onClick={() => openCreateDialog("instance")}>{t("mcp_add_server")}</Button>
            </div>
            <div className="grid gap-3">{instanceServers.map((s) => renderServerCard("instance", s))}</div>
          </section>
        ) : null}

        {orgSectionVisible ? (
          <section className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 className="text-lg font-medium">{t("mcp_org_section")}</h2>
              {isSuperAdmin ? (
                <Select value={selectedOrgId ?? ""} onValueChange={(value) => {
                  setSelectedOrgId(value)
                  orgStore.set(value)
                }}>
                  <SelectTrigger className="w-56">
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
            </div>
            {policy.allow_user_servers ? (
              <Card>
                <CardContent className="pt-6">
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-sm">{t("mcp_org_allow_user_servers")}</span>
                    <Switch
                      checked={orgAllowUserServers}
                      onCheckedChange={(checked) => void saveOrgPolicy(checked)}
                    />
                  </div>
                </CardContent>
              </Card>
            ) : null}
            <div className="space-y-3">
              <h3 className="text-sm font-medium">{t("mcp_instance_bindings")}</h3>
              {bindings.map((binding) => (
                <Card key={binding.instance_server_id}>
                  <CardContent className="flex flex-wrap items-center justify-between gap-3 pt-6">
                    <div>
                      <p className="font-medium">{binding.instance_server_name}</p>
                      <p className="font-mono text-xs text-muted-foreground">
                        {binding.instance_server_slug}
                      </p>
                    </div>
                    <Select
                      value={binding.mode}
                      onValueChange={(value) =>
                        void saveBinding(binding, value as McpBindingMode)
                      }
                    >
                      <SelectTrigger className="w-40">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="inherit">{t("mcp_mode_inherit")}</SelectItem>
                        <SelectItem value="override">{t("mcp_mode_override")}</SelectItem>
                        <SelectItem value="disabled">{t("mcp_mode_disabled")}</SelectItem>
                      </SelectContent>
                    </Select>
                  </CardContent>
                </Card>
              ))}
            </div>
            {policy.allow_org_servers ? (
              <div className="space-y-3">
                <div className="flex items-center justify-between gap-3">
                  <h3 className="text-sm font-medium">{t("mcp_org_servers")}</h3>
                  <Button size="sm" onClick={() => openCreateDialog("org")}>
                    {t("mcp_add_server")}
                  </Button>
                </div>
                <div className="grid gap-3">{orgServers.map((s) => renderServerCard("org", s))}</div>
              </div>
            ) : null}
          </section>
        ) : null}

        <section className="space-y-4">
          <h2 className="text-lg font-medium">{t("mcp_my_connections")}</h2>
          {overviewServers.filter((server) => String(server.user_auth_method) !== "oauth")
            .length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("mcp_no_connections")}</p>
          ) : (
            overviewServers
              .filter((server) => String(server.user_auth_method) !== "oauth")
              .map((server) => (
              <Card key={server.id}>
                <CardHeader className="pb-2">
                  <CardTitle className="text-base">{server.name}</CardTitle>
                  {server.user_instructions ? (
                    <p className="text-sm text-muted-foreground">{server.user_instructions}</p>
                  ) : null}
                </CardHeader>
                <CardContent className="space-y-3">
                  <Badge variant={server.connection_status === "connected" ? "outline" : "secondary"}>
                    {server.connection_status === "connected"
                      ? t("mcp_connected")
                      : t("mcp_not_connected")}
                  </Badge>
                  <div className="flex flex-wrap gap-2">
                    <Input
                      type="password"
                      className="max-w-sm"
                      placeholder={t("mcp_token_placeholder")}
                      value={connectionTokens[server.id] ?? ""}
                      onChange={(e) =>
                        setConnectionTokens((prev) => ({ ...prev, [server.id]: e.target.value }))
                      }
                    />
                    <Button size="sm" onClick={() => void saveConnectionToken(server)}>
                      {t("mcp_save_connection")}
                    </Button>
                    {server.connection_status === "connected" ? (
                      <>
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => void testConnection(server)}
                        >
                          {t("mcp_test")}
                        </Button>
                        <Button size="sm" variant="outline" onClick={() => void disconnect(server)}>
                          {t("mcp_disconnect")}
                        </Button>
                      </>
                    ) : null}
                  </div>
                </CardContent>
              </Card>
            ))
          )}
        </section>

        {policy.allow_user_servers && (policy.org_allow_user_servers ?? true) ? (
          <section className="space-y-4">
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-lg font-medium">{t("mcp_my_servers")}</h2>
              <Button onClick={() => openCreateDialog("user")}>{t("mcp_add_server")}</Button>
            </div>
            <div className="grid gap-3">
              {personalServers.map((s) => renderServerCard("user", s))}
            </div>
          </section>
        ) : null}

        <Dialog
          open={dialogOpen}
          onOpenChange={(open) => {
            setDialogOpen(open)
            if (!open) setFormError(null)
          }}
        >
          <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
            <DialogHeader>
              <DialogTitle>
                {editingServer ? t("mcp_edit_server") : t("mcp_add_server")}
              </DialogTitle>
            </DialogHeader>
            <div className="space-y-3">
              <Input
                placeholder={t("mcp_name")}
                value={form.name}
                onChange={(e) => setForm((prev) => ({ ...prev, name: e.target.value }))}
              />
              <Input
                placeholder={t("mcp_slug")}
                value={form.slug}
                onChange={(e) => setForm((prev) => ({ ...prev, slug: e.target.value }))}
              />
              <Textarea
                placeholder={t("mcp_description")}
                value={form.description ?? ""}
                onChange={(e) => setForm((prev) => ({ ...prev, description: e.target.value }))}
              />
              <Select
                value={form.transport}
                onValueChange={(value) =>
                  setForm((prev) => ({ ...prev, transport: value as McpTransport }))
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="http">HTTP</SelectItem>
                  <SelectItem value="sse">SSE</SelectItem>
                  {dialogScope === "instance" ? (
                    <SelectItem value="stdio">Stdio</SelectItem>
                  ) : null}
                </SelectContent>
              </Select>
              {form.transport === "stdio" ? (
                <>
                  <Input
                    placeholder={t("mcp_command")}
                    value={form.command ?? ""}
                    onChange={(e) => setForm((prev) => ({ ...prev, command: e.target.value }))}
                  />
                </>
              ) : (
                <Input
                  placeholder={t("mcp_url")}
                  value={form.url ?? ""}
                  onChange={(e) => setForm((prev) => ({ ...prev, url: e.target.value }))}
                />
              )}
              {renderAuthFields()}
              <div className="flex items-center justify-between gap-3">
                <span className="text-sm">{t("mcp_enabled")}</span>
                <Switch
                  checked={form.is_enabled ?? true}
                  onCheckedChange={(checked) => setForm((prev) => ({ ...prev, is_enabled: checked }))}
                />
              </div>
              {formError ? <p className="text-sm text-destructive">{formError}</p> : null}
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setDialogOpen(false)}>
                {t("common_cancel")}
              </Button>
              <Button onClick={() => void saveServer()}>{t("common_save")}</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        <Dialog
          open={configuringApp != null}
          onOpenChange={(open) => {
            if (!open) setConfiguringAppId(null)
          }}
        >
          <DialogContent className="sm:max-w-lg">
            <DialogHeader>
              <DialogTitle>
                {configuringApp
                  ? t("integrations_configure_app", { name: configuringApp.name })
                  : t("common_settings")}
              </DialogTitle>
            </DialogHeader>
            {configuringApp ? (
              <div className="space-y-4">
                {configuringApp.admin_config_fields.some((f) => f.key === "mcp_url") ? (
                  <div className="space-y-1.5">
                    <p className="text-sm font-medium">{t("integrations_mcp_url")}</p>
                    <Input
                      value={catalogUrlDrafts[configuringApp.catalog_id] ?? ""}
                      placeholder={
                        configuringApp.admin_config_fields.find((f) => f.key === "mcp_url")
                          ?.placeholder ?? ""
                      }
                      onChange={(e) =>
                        setCatalogUrlDrafts((prev) => ({
                          ...prev,
                          [configuringApp.catalog_id]: e.target.value,
                        }))
                      }
                    />
                  </div>
                ) : null}
                {configuringApp.catalog_id === "pitchpatterns" ? (
                  <div className="space-y-1.5">
                    <p className="text-sm font-medium">{t("integrations_embed_origins")}</p>
                    <Input
                      value={catalogOriginsDrafts[configuringApp.catalog_id] ?? ""}
                      placeholder="*.pitchpatterns.com"
                      onChange={(e) =>
                        setCatalogOriginsDrafts((prev) => ({
                          ...prev,
                          [configuringApp.catalog_id]: e.target.value,
                        }))
                      }
                    />
                  </div>
                ) : null}
                <div className="flex items-center justify-between gap-3">
                  <span className="text-sm">{t("integrations_offer_app")}</span>
                  <Switch
                    checked={configuringApp.enabled}
                    onCheckedChange={(checked) =>
                      void toggleCatalogEnabled(configuringApp, checked)
                    }
                  />
                </div>
                {configuringApp.catalog_id === "pitchpatterns" &&
                configuringApp.enabled &&
                configuringApp.configured ? (
                  <div className="space-y-1.5">
                    <p className="text-sm font-medium">{t("integrations_embed_url")}</p>
                    <div className="flex flex-wrap gap-2">
                      <Input
                        readOnly
                        className="min-w-0 flex-1 font-mono text-xs"
                        value={`${window.location.origin}/embed/pitchpatterns`}
                      />
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => void copyEmbedUrl("pitchpatterns")}
                      >
                        {embedCopied === "pitchpatterns"
                          ? t("integrations_embed_copied")
                          : t("integrations_embed_copy")}
                      </Button>
                    </div>
                  </div>
                ) : null}
              </div>
            ) : null}
            <DialogFooter className="gap-2 sm:gap-2">
              {configuringApp?.connected ? (
                <Button
                  variant="outline"
                  onClick={() => void testCatalogApp(configuringApp)}
                >
                  {t("mcp_test")}
                </Button>
              ) : null}
              <Button variant="outline" onClick={() => setConfiguringAppId(null)}>
                {t("common_close")}
              </Button>
              {configuringApp?.admin_config_fields.some((f) => f.key === "mcp_url") ||
              configuringApp?.catalog_id === "pitchpatterns" ? (
                <Button
                  onClick={() => void saveCatalogUrl(configuringApp, "instance")}
                >
                  {t("common_save")}
                </Button>
              ) : null}
            </DialogFooter>
          </DialogContent>
        </Dialog>

        <Dialog
          open={connectingApp != null}
          onOpenChange={(open) => {
            if (!open) setConnectingAppId(null)
          }}
        >
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>
                {connectingApp?.connected
                  ? t("integrations_update_key")
                  : t("integrations_connect")}
              </DialogTitle>
            </DialogHeader>
            {connectingApp ? (
              <div className="space-y-1.5">
                <p className="text-sm font-medium">{t("integrations_api_key")}</p>
                <Input
                  type="password"
                  autoFocus
                  placeholder={t("integrations_api_key")}
                  value={catalogKeys[connectingApp.catalog_id] ?? ""}
                  onChange={(e) =>
                    setCatalogKeys((prev) => ({
                      ...prev,
                      [connectingApp.catalog_id]: e.target.value,
                    }))
                  }
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void connectCatalogApp(connectingApp)
                  }}
                />
              </div>
            ) : null}
            <DialogFooter>
              <Button variant="outline" onClick={() => setConnectingAppId(null)}>
                {t("common_cancel")}
              </Button>
              {connectingApp ? (
                <Button onClick={() => void connectCatalogApp(connectingApp)}>
                  {connectingApp.connected
                    ? t("integrations_update_key")
                    : t("integrations_connect")}
                </Button>
              ) : null}
            </DialogFooter>
          </DialogContent>
        </Dialog>

        <Dialog open={entraSetupOpen} onOpenChange={setEntraSetupOpen}>
          <DialogContent className="sm:max-w-lg">
            <DialogHeader>
              <DialogTitle>{t("integrations_entra_setup")}</DialogTitle>
            </DialogHeader>
            <div className="space-y-4 text-sm">
              <ol className="list-decimal space-y-2 pl-4 text-muted-foreground">
                <li>{t("integrations_entra_step_register")}</li>
                <li>
                  {t("integrations_entra_step_redirect")}
                  <div className="mt-1.5 flex flex-wrap gap-2">
                    <Input
                      readOnly
                      className="min-w-0 flex-1 font-mono text-xs text-foreground"
                      value={oauthCallbackUrl}
                    />
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => {
                        void navigator.clipboard.writeText(oauthCallbackUrl)
                      }}
                    >
                      {t("integrations_embed_copy")}
                    </Button>
                  </div>
                </li>
                <li>{t("integrations_entra_step_secret")}</li>
                <li>{t("integrations_entra_step_permissions")}</li>
                <li>{t("integrations_entra_step_consent")}</li>
                <li>{t("integrations_entra_step_paste")}</li>
              </ol>
              {microsoftGraphPermissions.length > 0 ? (
                <div className="space-y-1.5">
                  <p className="font-medium text-foreground">
                    {t("integrations_entra_permissions")}
                  </p>
                  <ul className="max-h-48 space-y-1 overflow-y-auto rounded-md border px-3 py-2 font-mono text-xs">
                    {microsoftGraphPermissions.map((perm) => (
                      <li key={perm}>{perm}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </div>
            <DialogFooter>
              <Button onClick={() => setEntraSetupOpen(false)}>{t("common_close")}</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </SettingsPage>
    </SettingsShell>
  )
}
