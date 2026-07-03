import { useEffect, useMemo, useState } from "react"
import { useNavigate, useParams } from "react-router-dom"
import {
  ArrowLeft,
  FileText,
  Link2,
  MessageSquarePlus,
  MoreHorizontal,
  RefreshCw,
  Settings2,
  Trash2,
  Upload,
} from "lucide-react"

import { agentApi, chatApi, modelApi } from "@/lib/api"
import type {
  Agent,
  AgentShare,
  AgentShareSuggestion,
  AgentSource,
  Chat,
  ChatModel,
} from "@/lib/types"
import { orgStore } from "@/lib/storage"
import { readFilesAsAttachments } from "@/lib/file-utils"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Card } from "@/components/ui/card"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"

type Tab = "chats" | "files" | "instructions"

const SOURCE_ACCEPT =
  ".doc,.docx,.xls,.xlsx,.md,.txt,.pdf,.ppt,.pptx,.csv,.json,.xml,.html,.htm,.yaml,.yml"

const formatDate = (value: string) => {
  try {
    return new Date(value).toLocaleString()
  } catch {
    return value
  }
}

export const ProjectPage = () => {
  const navigate = useNavigate()
  const { projectId } = useParams<{ projectId: string }>()
  const orgId = orgStore.get() ?? ""

  const [project, setProject] = useState<Agent | null>(null)
  const [models, setModels] = useState<ChatModel[]>([])
  const [sources, setSources] = useState<AgentSource[]>([])
  const [chats, setChats] = useState<Chat[]>([])
  const [shares, setShares] = useState<AgentShare[]>([])
  const [tab, setTab] = useState<Tab>("chats")
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const [instrName, setInstrName] = useState("")
  const [instrDescription, setInstrDescription] = useState("")
  const [instrPrompt, setInstrPrompt] = useState("")
  const [instrModelId, setInstrModelId] = useState("")

  const [sourceFiles, setSourceFiles] = useState<File[]>([])
  const [sourceFileKey, setSourceFileKey] = useState(0)
  const [sourceUrl, setSourceUrl] = useState("")
  const [sourceUrlTitle, setSourceUrlTitle] = useState("")

  const [renameOpen, setRenameOpen] = useState(false)
  const [renameValue, setRenameValue] = useState("")
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [shareOpen, setShareOpen] = useState(false)
  const [shareEmail, setShareEmail] = useState("")
  const [shareRole, setShareRole] = useState<"owner" | "editor" | "viewer">("viewer")
  const [shareSuggestions, setShareSuggestions] = useState<AgentShareSuggestion[]>([])
  const [suggestionsOpen, setSuggestionsOpen] = useState(false)

  const canEdit = project?.role === "owner" || project?.role === "editor"
  const isOwner = project?.is_owner ?? false

  const loadProject = async () => {
    const items = await agentApi.list()
    const found = items.find((item) => item.id === projectId) ?? null
    setProject(found)
    if (found) {
      setInstrName(found.name)
      setInstrDescription(found.description ?? "")
      setInstrPrompt(found.master_prompt ?? "")
      setInstrModelId(found.preferred_model_id ?? "")
    }
    return found
  }

  const loadSources = async () => {
    if (!projectId) return
    setSources(await agentApi.listSources(projectId))
  }

  const loadChats = async () => {
    if (!orgId) return
    const all = await chatApi.list(orgId)
    setChats(all.filter((chat) => chat.agent_id === projectId))
  }

  useEffect(() => {
    if (!projectId) return
    void (async () => {
      try {
        setLoading(true)
        setError(null)
        const found = await loadProject()
        if (!found) {
          setError("Project not found or you no longer have access.")
          return
        }
        await Promise.all([
          loadSources(),
          loadChats(),
          orgId
            ? modelApi
                .list(orgId)
                .then((items) => setModels(items.filter((model) => model.is_available !== false)))
            : Promise.resolve(),
        ])
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load project")
      } finally {
        setLoading(false)
      }
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId])

  const hasPendingSources = useMemo(
    () => sources.some((source) => source.status === "queued" || source.status === "indexing"),
    [sources]
  )

  useEffect(() => {
    if (!hasPendingSources || !projectId) return
    const interval = window.setInterval(() => {
      void loadSources().catch(() => {})
    }, 3000)
    return () => window.clearInterval(interval)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasPendingSources, projectId])

  useEffect(() => {
    if (!shareOpen || !project) {
      setShareSuggestions([])
      return
    }
    let cancelled = false
    const handle = window.setTimeout(() => {
      void agentApi
        .shareSuggestions(project.id, shareEmail)
        .then((items) => {
          if (!cancelled) setShareSuggestions(items)
        })
        .catch(() => {
          if (!cancelled) setShareSuggestions([])
        })
    }, 200)
    return () => {
      cancelled = true
      window.clearTimeout(handle)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shareOpen, shareEmail, project, shares])

  const startChat = () => {
    if (!projectId) return
    navigate(`/chat?agent=${encodeURIComponent(projectId)}`)
  }

  const notify = (message: string) => {
    setError(null)
    setSuccess(message)
  }

  const withBusy = async (fn: () => Promise<void>) => {
    try {
      setBusy(true)
      setError(null)
      await fn()
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong")
    } finally {
      setBusy(false)
    }
  }

  const handleSaveInstructions = () =>
    withBusy(async () => {
      if (!project) return
      if (!instrName.trim()) {
        setError("Project name is required.")
        return
      }
      await agentApi.update(project.id, {
        name: instrName.trim(),
        description: instrDescription.trim() || null,
        master_prompt: instrPrompt.trim() || null,
        preferred_model_id: instrModelId || null,
      })
      await loadProject()
      notify("Project instructions saved.")
    })

  const handleUploadFiles = () =>
    withBusy(async () => {
      if (!project || sourceFiles.length === 0) return
      const attachments = await readFilesAsAttachments(sourceFiles)
      if (attachments.length === 0) throw new Error("Failed to read selected files")
      await Promise.all(
        attachments.map((attachment, index) =>
          agentApi.createSource(project.id, {
            kind: "file",
            title: sourceFiles[index]?.name || attachment.file_name,
            file_name: attachment.file_name,
            content_type: attachment.content_type,
            data_base64: attachment.data_base64,
          })
        )
      )
      setSourceFiles([])
      setSourceFileKey((key) => key + 1)
      await loadSources()
      notify(`${attachments.length} file${attachments.length === 1 ? "" : "s"} uploaded.`)
    })

  const handleAddUrl = () =>
    withBusy(async () => {
      if (!project) return
      const url = sourceUrl.trim()
      if (!url) {
        setError("URL is required.")
        return
      }
      await agentApi.createSource(project.id, {
        kind: "url",
        title: sourceUrlTitle.trim() || null,
        url,
      })
      setSourceUrl("")
      setSourceUrlTitle("")
      await loadSources()
      notify("URL added and indexing.")
    })

  const handleReindex = (sourceId: string) =>
    withBusy(async () => {
      if (!project) return
      await agentApi.reindexSource(project.id, sourceId)
      await loadSources()
      notify("Reindexing started.")
    })

  const handleDeleteSource = (sourceId: string) =>
    withBusy(async () => {
      if (!project) return
      await agentApi.removeSource(project.id, sourceId)
      await loadSources()
      notify("File removed.")
    })

  const handleRename = () =>
    withBusy(async () => {
      if (!project) return
      const name = renameValue.trim()
      if (!name) {
        setError("Project name is required.")
        return
      }
      await agentApi.update(project.id, { name })
      setRenameOpen(false)
      await loadProject()
      notify("Project renamed.")
    })

  const handleDeleteProject = () =>
    withBusy(async () => {
      if (!project) return
      await agentApi.remove(project.id)
      navigate("/projects")
    })

  const openShare = () => {
    setShareOpen(true)
    if (project) {
      void agentApi
        .listShares(project.id)
        .then(setShares)
        .catch(() => setShares([]))
    }
  }

  const handleAddShare = () =>
    withBusy(async () => {
      if (!project) return
      const email = shareEmail.trim()
      if (!email) {
        setError("Email is required.")
        return
      }
      await agentApi.share(project.id, { email, role: shareRole })
      setShareEmail("")
      setSuggestionsOpen(false)
      setShares(await agentApi.listShares(project.id))
      notify("Access granted.")
    })

  const handleRemoveShare = (userId: string) =>
    withBusy(async () => {
      if (!project) return
      await agentApi.unshare(project.id, userId)
      setShares(await agentApi.listShares(project.id))
    })

  const sortedChats = useMemo(
    () =>
      [...chats].sort(
        (a, b) =>
          new Date(b.last_activity_at).getTime() - new Date(a.last_activity_at).getTime()
      ),
    [chats]
  )

  if (loading) {
    return (
      <div className="mx-auto flex min-h-svh w-full max-w-4xl items-center justify-center px-4">
        <p className="text-muted-foreground text-sm">Loading project...</p>
      </div>
    )
  }

  if (!project) {
    return (
      <div className="mx-auto flex min-h-svh w-full max-w-4xl flex-col items-center justify-center gap-4 px-4 text-center">
        <p className="text-muted-foreground">{error ?? "Project not found."}</p>
        <Button onClick={() => navigate("/projects")}>Back to projects</Button>
      </div>
    )
  }

  const tabs: { id: Tab; label: string; count?: number }[] = [
    { id: "chats", label: "Chats", count: chats.length },
    { id: "files", label: "Files", count: sources.length },
    { id: "instructions", label: "Instructions" },
  ]

  return (
    <div className="mx-auto flex h-svh w-full max-w-4xl flex-col gap-6 overflow-y-auto px-4 py-8 sm:px-6">
      <div>
        <Button
          variant="ghost"
          size="sm"
          className="text-muted-foreground -ml-2 mb-2"
          onClick={() => navigate("/projects")}
        >
          <ArrowLeft className="h-4 w-4" aria-hidden="true" />
          Projects
        </Button>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h1 className="truncate text-2xl font-semibold">{project.name}</h1>
            {project.description ? (
              <p className="text-muted-foreground mt-1 text-sm">{project.description}</p>
            ) : null}
          </div>
          <div className="flex items-center gap-2">
            <Button onClick={startChat}>
              <MessageSquarePlus className="h-4 w-4" aria-hidden="true" />
              New chat
            </Button>
            {canEdit ? (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="outline" size="icon" aria-label="Project actions">
                    <MoreHorizontal className="h-4 w-4" aria-hidden="true" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem
                    onClick={() => {
                      setRenameValue(project.name)
                      setRenameOpen(true)
                    }}
                  >
                    Rename
                  </DropdownMenuItem>
                  {isOwner ? (
                    <DropdownMenuItem onClick={openShare}>Share</DropdownMenuItem>
                  ) : null}
                  {isOwner ? (
                    <>
                      <DropdownMenuSeparator />
                      <DropdownMenuItem variant="destructive" onClick={() => setDeleteOpen(true)}>
                        Delete project
                      </DropdownMenuItem>
                    </>
                  ) : null}
                </DropdownMenuContent>
              </DropdownMenu>
            ) : null}
          </div>
        </div>
      </div>

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

      <div className="border-b">
        <div className="flex gap-1">
          {tabs.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => setTab(item.id)}
              className={`relative -mb-px flex items-center gap-2 border-b-2 px-4 py-2 text-sm font-medium transition-colors ${
                tab === item.id
                  ? "border-primary text-foreground"
                  : "text-muted-foreground hover:text-foreground border-transparent"
              }`}
            >
              {item.label}
              {typeof item.count === "number" ? (
                <span className="text-muted-foreground text-xs">{item.count}</span>
              ) : null}
            </button>
          ))}
        </div>
      </div>

      {tab === "chats" ? (
        sortedChats.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed py-16 text-center">
            <MessageSquarePlus className="text-muted-foreground h-6 w-6" aria-hidden="true" />
            <p className="text-muted-foreground text-sm">No chats in this project yet.</p>
            <Button onClick={startChat}>Start a chat</Button>
          </div>
        ) : (
          <div className="space-y-2">
            {sortedChats.map((chat) => (
              <Card
                key={chat.id}
                role="button"
                tabIndex={0}
                onClick={() =>
                  navigate(`/chat/${chat.id}?agent=${encodeURIComponent(project.id)}`)
                }
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault()
                    navigate(`/chat/${chat.id}?agent=${encodeURIComponent(project.id)}`)
                  }
                }}
                className="hover:border-primary/60 hover:bg-accent/40 flex cursor-pointer flex-row items-center justify-between gap-3 px-4 py-3 transition-colors"
              >
                <p className="min-w-0 flex-1 truncate font-medium">
                  {chat.title || "Untitled chat"}
                </p>
                <span className="text-muted-foreground shrink-0 text-xs">
                  {formatDate(chat.last_activity_at)}
                </span>
              </Card>
            ))}
          </div>
        )
      ) : null}

      {tab === "files" ? (
        <div className="space-y-6">
          {canEdit ? (
            <Card className="gap-4 p-4">
              <div className="space-y-2">
                <p className="flex items-center gap-2 text-sm font-medium">
                  <Upload className="h-4 w-4" aria-hidden="true" />
                  Upload files
                </p>
                <Input
                  key={sourceFileKey}
                  type="file"
                  multiple
                  accept={SOURCE_ACCEPT}
                  onChange={(event) => setSourceFiles(Array.from(event.target.files ?? []))}
                />
                <div className="flex items-center gap-2">
                  <Button
                    onClick={handleUploadFiles}
                    disabled={sourceFiles.length === 0 || busy}
                    size="sm"
                  >
                    Upload
                  </Button>
                  {sourceFiles.length > 0 ? (
                    <span className="text-muted-foreground text-xs">
                      {sourceFiles.length} selected
                    </span>
                  ) : null}
                </div>
              </div>
              <div className="space-y-2 border-t pt-4">
                <p className="flex items-center gap-2 text-sm font-medium">
                  <Link2 className="h-4 w-4" aria-hidden="true" />
                  Add a URL
                </p>
                <Input
                  placeholder="https://example.com/article"
                  value={sourceUrl}
                  onChange={(event) => setSourceUrl(event.target.value)}
                />
                <Input
                  placeholder="Title (optional)"
                  value={sourceUrlTitle}
                  onChange={(event) => setSourceUrlTitle(event.target.value)}
                />
                <Button onClick={handleAddUrl} disabled={!sourceUrl.trim() || busy} size="sm">
                  Add URL
                </Button>
              </div>
            </Card>
          ) : null}

          <div className="space-y-2">
            {sources.length === 0 ? (
              <p className="text-muted-foreground text-sm">No files added yet.</p>
            ) : (
              sources.map((source) => (
                <Card key={source.id} className="gap-2 p-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex min-w-0 items-start gap-2">
                      <FileText
                        className="text-muted-foreground mt-0.5 h-4 w-4 shrink-0"
                        aria-hidden="true"
                      />
                      <div className="min-w-0">
                        <p className="truncate font-medium">{source.title}</p>
                        {source.url ? (
                          <p className="text-muted-foreground truncate text-xs">{source.url}</p>
                        ) : null}
                        {source.summary ? (
                          <p className="text-muted-foreground line-clamp-2 text-xs">
                            {source.summary}
                          </p>
                        ) : null}
                        {source.error_message ? (
                          <p className="text-destructive text-xs">{source.error_message}</p>
                        ) : null}
                      </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      <Badge variant={source.status === "ready" ? "secondary" : "outline"}>
                        {source.status}
                      </Badge>
                      {canEdit ? (
                        <>
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-8 w-8"
                            aria-label="Reindex file"
                            disabled={busy}
                            onClick={() => handleReindex(source.id)}
                          >
                            <RefreshCw className="h-4 w-4" aria-hidden="true" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon"
                            className="text-destructive h-8 w-8"
                            aria-label="Delete file"
                            disabled={busy}
                            onClick={() => handleDeleteSource(source.id)}
                          >
                            <Trash2 className="h-4 w-4" aria-hidden="true" />
                          </Button>
                        </>
                      ) : null}
                    </div>
                  </div>
                </Card>
              ))
            )}
          </div>
        </div>
      ) : null}

      {tab === "instructions" ? (
        <div className="max-w-2xl space-y-4">
          <div className="space-y-2">
            <label className="text-sm font-medium">Name</label>
            <Input
              value={instrName}
              onChange={(event) => setInstrName(event.target.value)}
              disabled={!canEdit}
            />
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium">Description</label>
            <Input
              value={instrDescription}
              onChange={(event) => setInstrDescription(event.target.value)}
              placeholder="What is this project about?"
              disabled={!canEdit}
            />
          </div>
          <div className="space-y-2">
            <label className="flex items-center gap-2 text-sm font-medium">
              <Settings2 className="h-4 w-4" aria-hidden="true" />
              Custom instructions
            </label>
            <p className="text-muted-foreground text-xs">
              These instructions are applied to every chat inside this project.
            </p>
            <Textarea
              rows={8}
              value={instrPrompt}
              onChange={(event) => setInstrPrompt(event.target.value)}
              placeholder="e.g. Always answer concisely and cite the project files when relevant."
              disabled={!canEdit}
            />
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium">Preferred model</label>
            <Select
              value={instrModelId || "__none__"}
              onValueChange={(value) => setInstrModelId(value === "__none__" ? "" : value)}
              disabled={!canEdit}
            >
              <SelectTrigger className="w-full">
                <SelectValue placeholder="No preferred model" />
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
          {canEdit ? (
            <Button onClick={handleSaveInstructions} disabled={busy}>
              Save changes
            </Button>
          ) : (
            <p className="text-muted-foreground text-sm">You have view-only access to this project.</p>
          )}
        </div>
      ) : null}

      <Dialog open={renameOpen} onOpenChange={setRenameOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Rename project</DialogTitle>
          </DialogHeader>
          <Input
            autoFocus
            value={renameValue}
            onChange={(event) => setRenameValue(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault()
                void handleRename()
              }
            }}
          />
          <DialogFooter>
            <Button variant="outline" onClick={() => setRenameOpen(false)}>
              Cancel
            </Button>
            <Button onClick={handleRename} disabled={busy || !renameValue.trim()}>
              Save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete project</DialogTitle>
          </DialogHeader>
          <p className="text-muted-foreground text-sm">
            This permanently deletes the project, its instructions, and files. Chats created in the
            project are kept but are no longer grouped. This cannot be undone.
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteOpen(false)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={handleDeleteProject} disabled={busy}>
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={shareOpen} onOpenChange={setShareOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Share project</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="flex items-start gap-2">
              <div className="relative flex-1">
                <Input
                  placeholder="Search by name or email"
                  value={shareEmail}
                  onChange={(event) => {
                    setShareEmail(event.target.value)
                    setSuggestionsOpen(true)
                  }}
                  onFocus={() => setSuggestionsOpen(true)}
                  onBlur={() => window.setTimeout(() => setSuggestionsOpen(false), 150)}
                />
                {suggestionsOpen && shareSuggestions.length > 0 ? (
                  <div className="bg-popover absolute z-50 mt-1 max-h-56 w-full overflow-y-auto rounded-md border p-1 shadow-md">
                    {shareSuggestions.map((suggestion) => (
                      <button
                        key={suggestion.user_id}
                        type="button"
                        className="hover:bg-accent flex w-full flex-col items-start rounded-sm px-2 py-1.5 text-left text-sm"
                        onMouseDown={(event) => {
                          event.preventDefault()
                          setShareEmail(suggestion.email)
                          setSuggestionsOpen(false)
                        }}
                      >
                        {suggestion.display_name ? (
                          <>
                            <span className="truncate">{suggestion.display_name}</span>
                            <span className="text-muted-foreground truncate text-xs">
                              {suggestion.email}
                            </span>
                          </>
                        ) : (
                          <span className="truncate">{suggestion.email}</span>
                        )}
                      </button>
                    ))}
                  </div>
                ) : null}
              </div>
              <Select
                value={shareRole}
                onValueChange={(value) => setShareRole(value as "owner" | "editor" | "viewer")}
              >
                <SelectTrigger className="w-32">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="viewer">Viewer</SelectItem>
                  <SelectItem value="editor">Editor</SelectItem>
                  <SelectItem value="owner">Owner</SelectItem>
                </SelectContent>
              </Select>
              <Button onClick={handleAddShare} disabled={busy || !shareEmail.trim()}>
                Add
              </Button>
            </div>
            <div className="space-y-2">
              {shares.length === 0 ? (
                <p className="text-muted-foreground text-sm">Not shared with anyone yet.</p>
              ) : (
                shares.map((share) => (
                  <div
                    key={share.user_id}
                    className="flex items-center justify-between gap-2 rounded-md border px-3 py-2"
                  >
                    <div className="min-w-0">
                      <p className="truncate text-sm">{share.email}</p>
                      <p className="text-muted-foreground text-xs capitalize">{share.role}</p>
                    </div>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="text-destructive h-8 w-8"
                      aria-label="Remove access"
                      disabled={busy}
                      onClick={() => handleRemoveShare(share.user_id)}
                    >
                      <Trash2 className="h-4 w-4" aria-hidden="true" />
                    </Button>
                  </div>
                ))
              )}
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShareOpen(false)}>
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

export default ProjectPage
