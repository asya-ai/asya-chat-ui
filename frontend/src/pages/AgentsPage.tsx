import { useEffect, useMemo, useState } from "react"
import { useLocation, useNavigate } from "react-router-dom"

import { agentApi, authApi, modelApi, orgApi } from "@/lib/api"
import type { Agent, AgentSource, ChatModel, Org } from "@/lib/types"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { ScrollArea } from "@/components/ui/scroll-area"
import { readFilesAsAttachments } from "@/lib/file-utils"
import { orgStore } from "@/lib/storage"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { SettingsShell } from "@/components/SettingsShell"
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { useI18n } from "@/lib/i18n-context"

export const AgentsPage = () => {
  const navigate = useNavigate()
  const location = useLocation()
  const { t } = useI18n()
  const [orgs, setOrgs] = useState<Org[]>([])
  const [selectedOrgId, setSelectedOrgId] = useState<string>(orgStore.get() ?? "")
  const [agents, setAgents] = useState<Agent[]>([])
  const [models, setModels] = useState<ChatModel[]>([])
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null)
  const [sources, setSources] = useState<AgentSource[]>([])
  const [newAgentName, setNewAgentName] = useState("")
  const [newAgentDescription, setNewAgentDescription] = useState("")
  const [newAgentPrompt, setNewAgentPrompt] = useState("")
  const [newAgentModelId, setNewAgentModelId] = useState<string>("")
  const [sourceTitle, setSourceTitle] = useState("")
  const [sourceFiles, setSourceFiles] = useState<File[]>([])
  const [sourceUrl, setSourceUrl] = useState("")
  const [sourceUrlTitle, setSourceUrlTitle] = useState("")
  const [sourceInputKey, setSourceInputKey] = useState(0)
  const [sourceTitleDrafts, setSourceTitleDrafts] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [editOpen, setEditOpen] = useState(false)
  const [editName, setEditName] = useState("")
  const [editDescription, setEditDescription] = useState("")
  const [editPrompt, setEditPrompt] = useState("")
  const [editModelId, setEditModelId] = useState("")
  const [isAdmin, setIsAdmin] = useState(false)
  const [isSuperAdmin, setIsSuperAdmin] = useState(false)

  const selectedAgent = useMemo(
    () => agents.find((agent) => agent.id === selectedAgentId) ?? null,
    [agents, selectedAgentId]
  )

  useEffect(() => {
    authApi
      .me()
      .then((me) => {
        setIsAdmin(me.is_admin)
        setIsSuperAdmin(me.is_super_admin)
      })
      .catch(() => {
        setIsAdmin(false)
        setIsSuperAdmin(false)
      })
  }, [])

  useEffect(() => {
    if (!selectedAgent) {
      setEditName("")
      setEditDescription("")
      setEditPrompt("")
      setEditModelId("")
      return
    }
    setEditName(selectedAgent.name)
    setEditDescription(selectedAgent.description ?? "")
    setEditPrompt(selectedAgent.master_prompt ?? "")
    setEditModelId(selectedAgent.preferred_model_id ?? "")
  }, [selectedAgent])

  const selectOrg = (orgId: string) => {
    orgStore.set(orgId)
    setSelectedOrgId(orgId)
  }

  const loadAgents = async () => {
    const items = await agentApi.list()
    setAgents(items)
    if (items.length === 0) {
      setSelectedAgentId(null)
      return
    }
    if (!selectedAgentId || !items.some((item) => item.id === selectedAgentId)) {
      setSelectedAgentId(items[0].id)
    }
  }

  const loadSources = async (agentId: string) => {
    const items = await agentApi.listSources(agentId)
    setSources(items)
  }

  useEffect(() => {
    void (async () => {
      try {
        setLoading(true)
        const orgItems = await orgApi.list()
        setOrgs(orgItems)
        const initialOrgId =
          selectedOrgId && orgItems.some((item) => item.id === selectedOrgId)
            ? selectedOrgId
            : orgItems[0]?.id ?? ""
        if (initialOrgId) {
          selectOrg(initialOrgId)
        } else {
          setError("No organization selected. Open Settings and create/select an organization.")
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load organizations")
      } finally {
        setLoading(false)
      }
    })()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!selectedOrgId) return
    void (async () => {
      try {
        setLoading(true)
        setError(null)
        const [agentItems, modelItems] = await Promise.all([
          agentApi.list(),
          modelApi.list(selectedOrgId),
        ])
        setAgents(agentItems)
        setModels(modelItems.filter((model) => model.is_available !== false))
        if (agentItems.length > 0) {
          setSelectedAgentId((current) =>
            current && agentItems.some((item) => item.id === current)
              ? current
              : agentItems[0].id
          )
        } else {
          setSelectedAgentId(null)
          setSources([])
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load agents")
      } finally {
        setLoading(false)
      }
    })()
  }, [selectedOrgId])

  useEffect(() => {
    if (!selectedAgentId) {
      setSources([])
      setSourceTitleDrafts({})
      return
    }
    void loadSources(selectedAgentId).catch((err: unknown) => {
      setError(err instanceof Error ? err.message : "Failed to load sources")
    })
  }, [selectedAgentId])

  useEffect(() => {
    setSourceTitleDrafts(
      sources.reduce<Record<string, string>>((acc, source) => {
        acc[source.id] = source.title
        return acc
      }, {})
    )
  }, [sources])

  const handleCreateAgent = async () => {
    setError(null)
    setSuccess(null)
    if (!selectedOrgId) {
      setError("Select an organization first.")
      return
    }
    if (!newAgentName.trim()) {
      setError("Agent name is required.")
      return
    }
    try {
      setLoading(true)
      const created = await agentApi.create({
        name: newAgentName.trim(),
        description: newAgentDescription.trim() || null,
        preferred_model_id: newAgentModelId || null,
        master_prompt: newAgentPrompt.trim(),
      })
      setNewAgentName("")
      setNewAgentDescription("")
      setNewAgentPrompt("")
      setNewAgentModelId("")
      setCreateOpen(false)
      await loadAgents()
      setSelectedAgentId(created.id)
      setSuccess("Agent created successfully.")
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create agent")
    } finally {
      setLoading(false)
    }
  }

  const handleAddSource = async () => {
    if (!selectedAgent || sourceFiles.length === 0) return
    setError(null)
    setSuccess(null)
    try {
      setLoading(true)
      const attachments = await readFilesAsAttachments(sourceFiles)
      if (attachments.length === 0) {
        throw new Error("Failed to read selected files")
      }
      await Promise.all(
        attachments.map((attachment, index) =>
          agentApi.createSource(selectedAgent.id, {
            kind: "file",
            title:
              sourceTitle.trim() && attachments.length === 1
                ? sourceTitle.trim()
                : sourceFiles[index]?.name || attachment.file_name,
            file_name: attachment.file_name,
            content_type: attachment.content_type,
            data_base64: attachment.data_base64,
          })
        )
      )
      setSourceFiles([])
      setSourceInputKey((current) => current + 1)
      setSourceTitle("")
      await loadSources(selectedAgent.id)
      setSuccess(
        attachments.length === 1
          ? "Source uploaded and indexed."
          : `${attachments.length} sources uploaded and indexed.`
      )
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add file source")
    } finally {
      setLoading(false)
    }
  }

  const handleAddUrlSource = async () => {
    if (!selectedAgent) return
    const nextUrl = sourceUrl.trim()
    if (!nextUrl) {
      setError("Source URL is required.")
      return
    }
    setError(null)
    setSuccess(null)
    try {
      setLoading(true)
      await agentApi.createSource(selectedAgent.id, {
        kind: "url",
        title: sourceUrlTitle.trim() || null,
        url: nextUrl,
      })
      setSourceUrl("")
      setSourceUrlTitle("")
      await loadSources(selectedAgent.id)
      setSuccess("URL source added and indexed.")
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add URL source")
    } finally {
      setLoading(false)
    }
  }

  const handleSaveAgent = async () => {
    if (!selectedAgent) return
    setError(null)
    setSuccess(null)
    if (!editName.trim()) {
      setError("Agent name is required.")
      return
    }
    try {
      setLoading(true)
      await agentApi.update(selectedAgent.id, {
        name: editName.trim(),
        description: editDescription.trim() || null,
        master_prompt: editPrompt.trim() || null,
        preferred_model_id: editModelId || null,
      })
      await loadAgents()
      setSuccess("Agent updated.")
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update agent")
    } finally {
      setLoading(false)
    }
  }

  const handleSaveSource = async (sourceId: string) => {
    if (!selectedAgent) return
    const nextTitle = sourceTitleDrafts[sourceId]?.trim()
    if (!nextTitle) {
      setError("Source title is required.")
      return
    }
    try {
      setLoading(true)
      setError(null)
      setSuccess(null)
      await agentApi.updateSource(selectedAgent.id, sourceId, { title: nextTitle })
      await loadSources(selectedAgent.id)
      setSuccess("Source updated.")
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update source")
    } finally {
      setLoading(false)
    }
  }

  const handleDeleteSource = async (sourceId: string) => {
    if (!selectedAgent) return
    try {
      setLoading(true)
      setError(null)
      setSuccess(null)
      await agentApi.removeSource(selectedAgent.id, sourceId)
      await loadSources(selectedAgent.id)
      setSuccess("Source deleted.")
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete source")
    } finally {
      setLoading(false)
    }
  }

  const handleReindexSource = async (sourceId: string) => {
    if (!selectedAgent) return
    try {
      setLoading(true)
      setError(null)
      setSuccess(null)
      await agentApi.reindexSource(selectedAgent.id, sourceId)
      await loadSources(selectedAgent.id)
      setSuccess("Source reindexed.")
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to reindex source")
    } finally {
      setLoading(false)
    }
  }

  const handleDeleteAgent = async () => {
    if (!selectedAgent) return
    setError(null)
    setSuccess(null)
    try {
      setLoading(true)
      await agentApi.remove(selectedAgent.id)
      await loadAgents()
      setSources([])
      setSuccess("Agent deleted.")
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete agent")
    } finally {
      setLoading(false)
    }
  }

  const navItems = [
    { label: t("me_settings"), href: "/settings/me", active: location.pathname.startsWith("/settings/me") },
    {
      label: t("org_section_users"),
      href: "/settings/users",
      visible: isAdmin,
      active: location.pathname.startsWith("/settings/users"),
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
      label: "Agents",
      href: "/settings/agents",
      visible: true,
      active: location.pathname.startsWith("/settings/agents"),
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
      title="Agents"
      items={navItems}
      actions={
        <Button variant="outline" onClick={() => navigate("/chat")}>
          {t("common_back_to_chat")}
        </Button>
      }
    >
      <div className="space-y-6">
        <p className="text-muted-foreground text-sm">
          Create, edit, delete agents and manage their sources.
        </p>

      {error ? (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
      {success ? (
        <Alert>
          <AlertDescription>{success}</AlertDescription>
        </Alert>
      ) : null}

      <div className="flex items-center gap-3">
        <span className="text-sm text-muted-foreground">Organization</span>
        <Select value={selectedOrgId} onValueChange={selectOrg}>
          <SelectTrigger className="w-72">
            <SelectValue placeholder="Select organization" />
          </SelectTrigger>
          <SelectContent>
            {orgs.map((org) => (
              <SelectItem key={org.id} value={org.id}>
                {org.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0">
          <CardTitle>Agents</CardTitle>
          <Button size="sm" onClick={() => setCreateOpen(true)}>
            New Agent
          </Button>
        </CardHeader>
        <CardContent>
          <ScrollArea className="max-h-56">
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3 pr-2">
              {agents.map((agent) => (
                <Button
                  key={agent.id}
                  variant={agent.id === selectedAgentId ? "default" : "outline"}
                  className="w-full justify-start text-left h-auto py-2"
                  onClick={() => {
                    setSelectedAgentId(agent.id)
                    setEditOpen(true)
                  }}
                >
                  <div className="min-w-0">
                    <p className="truncate">{agent.name}</p>
                    {agent.description ? (
                      <p className="text-xs text-muted-foreground truncate">{agent.description}</p>
                    ) : null}
                  </div>
                </Button>
              ))}
              {agents.length === 0 && !loading ? (
                <p className="text-sm text-muted-foreground">No agents yet. Create your first agent.</p>
              ) : null}
            </div>
          </ScrollArea>
        </CardContent>
      </Card>
      </div>

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Create Agent</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <Input
              placeholder="Name"
              value={newAgentName}
              onChange={(event) => setNewAgentName(event.target.value)}
            />
            <Select value={newAgentModelId || "__none__"} onValueChange={(value) => setNewAgentModelId(value === "__none__" ? "" : value)}>
              <SelectTrigger>
                <SelectValue placeholder="Preferred model (optional)" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__none__">No preferred model</SelectItem>
                {models.map((model) => (
                  <SelectItem key={model.id} value={model.id}>
                    {model.display_name} ({model.provider})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Input
              placeholder="Description"
              value={newAgentDescription}
              onChange={(event) => setNewAgentDescription(event.target.value)}
            />
            <Textarea
              rows={4}
              placeholder="Master prompt (optional)"
              value={newAgentPrompt}
              onChange={(event) => setNewAgentPrompt(event.target.value)}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateOpen(false)}>
              Cancel
            </Button>
            <Button onClick={handleCreateAgent} disabled={loading}>
              Create Agent
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={editOpen && !!selectedAgent} onOpenChange={setEditOpen}>
        <DialogContent className="w-[95vw] max-w-4xl max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Edit Agent</DialogTitle>
          </DialogHeader>
          {selectedAgent ? (
            <div className="space-y-6">
              <section className="space-y-3 rounded-lg border p-4">
                <h3 className="font-semibold">Details</h3>
                <div className="space-y-2">
                  <p className="text-sm text-muted-foreground">Name</p>
                  <div className="grid gap-2 lg:grid-cols-2">
                    <Input
                      placeholder="Name"
                      value={editName}
                      onChange={(event) => setEditName(event.target.value)}
                    />
                    <Select
                      value={editModelId || "__none__"}
                      onValueChange={(value) => setEditModelId(value === "__none__" ? "" : value)}
                    >
                      <SelectTrigger>
                        <SelectValue placeholder="Preferred model (optional)" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="__none__">No preferred model</SelectItem>
                        {models.map((model) => (
                          <SelectItem key={model.id} value={model.id}>
                            {model.display_name} ({model.provider})
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </div>
                <div className="space-y-2">
                  <p className="text-sm text-muted-foreground">Description</p>
                  <Input
                    placeholder="Description"
                    value={editDescription}
                    onChange={(event) => setEditDescription(event.target.value)}
                  />
                </div>
                <div className="space-y-2">
                  <p className="text-sm text-muted-foreground">Master prompt (optional)</p>
                  <Textarea
                    rows={4}
                    placeholder="Master prompt (optional)"
                    value={editPrompt}
                    onChange={(event) => setEditPrompt(event.target.value)}
                  />
                </div>
                <div className="flex gap-2">
                  <Button onClick={handleSaveAgent} disabled={loading}>
                    Save Changes
                  </Button>
                  <Button variant="destructive" onClick={handleDeleteAgent} disabled={loading}>
                    Delete Agent
                  </Button>
                </div>
              </section>

              <section className="space-y-3 rounded-lg border p-4">
                <h3 className="font-semibold">Sources</h3>
                <p className="text-sm text-muted-foreground">Upload files</p>
                <Input
                  key={sourceInputKey}
                  type="file"
                  multiple
                  accept=".doc,.docx,.xls,.xlsx,.md,.txt,.pdf,.ppt,.pptx,.csv,.json,.xml,.html,.htm,.yaml,.yml"
                  onChange={(event) => setSourceFiles(Array.from(event.target.files ?? []))}
                  disabled={!selectedAgent}
                />
                <Input
                  placeholder="Source title (optional, used only for single file upload)"
                  value={sourceTitle}
                  onChange={(event) => setSourceTitle(event.target.value)}
                  disabled={!selectedAgent}
                />
                {sourceFiles.length > 0 ? (
                  <p className="text-xs text-muted-foreground">
                    {sourceFiles.length} file{sourceFiles.length === 1 ? "" : "s"} selected
                  </p>
                ) : null}
                <Button onClick={handleAddSource} disabled={!selectedAgent || sourceFiles.length === 0 || loading}>
                  Upload Source
                </Button>
                <div className="border-t pt-3 space-y-2">
                  <p className="text-sm text-muted-foreground">Or add URL</p>
                  <Input
                    placeholder="https://example.com/article"
                    value={sourceUrl}
                    onChange={(event) => setSourceUrl(event.target.value)}
                    disabled={!selectedAgent || loading}
                  />
                  <Input
                    placeholder="URL title (optional)"
                    value={sourceUrlTitle}
                    onChange={(event) => setSourceUrlTitle(event.target.value)}
                    disabled={!selectedAgent || loading}
                  />
                  <Button
                    onClick={handleAddUrlSource}
                    disabled={!selectedAgent || !sourceUrl.trim() || loading}
                  >
                    Add URL Source
                  </Button>
                </div>
                <ScrollArea className="max-h-56">
                  <div className="space-y-2 pr-2">
                    {sources.map((source) => (
                      <div key={source.id} className="rounded-md border p-2 space-y-2">
                        <div className="flex items-center gap-2">
                          <Input
                            value={sourceTitleDrafts[source.id] ?? source.title}
                            onChange={(event) =>
                              setSourceTitleDrafts((current) => ({
                                ...current,
                                [source.id]: event.target.value,
                              }))
                            }
                            placeholder="Source title"
                            className="h-8"
                          />
                          <span className="text-xs text-muted-foreground shrink-0">{source.status}</span>
                        </div>
                        {source.summary ? (
                          <p className="text-xs text-muted-foreground line-clamp-3">
                            {source.summary}
                          </p>
                        ) : null}
                        {source.url ? (
                          <p className="text-xs text-muted-foreground truncate">{source.url}</p>
                        ) : null}
                        <div className="flex gap-2">
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => handleSaveSource(source.id)}
                            disabled={loading}
                          >
                            Save
                          </Button>
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => handleReindexSource(source.id)}
                            disabled={loading}
                          >
                            Reindex
                          </Button>
                          <Button
                            size="sm"
                            variant="destructive"
                            onClick={() => handleDeleteSource(source.id)}
                            disabled={loading}
                          >
                            Delete
                          </Button>
                        </div>
                      </div>
                    ))}
                    {sources.length === 0 ? (
                      <p className="text-sm text-muted-foreground">No sources yet.</p>
                    ) : null}
                  </div>
                </ScrollArea>
              </section>
            </div>
          ) : null}
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditOpen(false)}>
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </SettingsShell>
  )
}

export default AgentsPage
