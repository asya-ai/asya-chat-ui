import { useCallback, useEffect, useState } from "react"

import { SettingsShell } from "@/components/SettingsShell"
import { SettingsPage, SettingsPageHeader } from "@/components/settings/SettingsPanel"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
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
import { authApi, mcpApi, orgApi } from "@/lib/api"
import { useI18n } from "@/lib/i18n-context"
import { orgStore } from "@/lib/storage"
import type {
  McpAuthType,
  McpBinding,
  McpBindingMode,
  McpServer,
  McpServerWrite,
  McpSettings,
  McpTransport,
  McpUserAuthMethod,
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

  const canManageOrg = isSuperAdmin || isAdmin

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
          setOverviewServers(overview.user_provided_servers)
          setPersonalServers(overview.personal_servers)
          setPolicy({
            allow_org_servers: overview.policy.allow_org_servers,
            allow_user_servers: overview.policy.allow_user_servers,
            org_allow_user_servers: overview.policy.org_allow_user_servers ?? null,
          })
        }),
      ]

      if (isSuperAdmin) {
        tasks.push(
          mcpApi.instanceSettings().then(setInstanceSettings),
          mcpApi.listInstanceServers().then(setInstanceServers)
        )
      }
      if (canManageOrg && orgId) {
        tasks.push(
          mcpApi.orgSettings(orgId).then((s) => setOrgAllowUserServers(s.allow_user_servers)),
          mcpApi.listOrgServers(orgId).then(setOrgServers),
          mcpApi.listBindings(orgId).then(setBindings)
        )
      }
      await Promise.all(tasks)
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common_error"))
    } finally {
      setLoading(false)
    }
  }, [canManageOrg, isSuperAdmin, selectedOrgId, t])

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

  return (
    <SettingsShell title={t("integrations_title")}>
      <SettingsPage wide>
        <SettingsPageHeader
          title={t("integrations_title")}
          description={t("integrations_description")}
        />
        {error ? <p className="text-sm text-destructive">{error}</p> : null}
        {loading ? <p className="text-sm text-muted-foreground">{t("common_loading")}</p> : null}

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
      </SettingsPage>
    </SettingsShell>
  )
}
