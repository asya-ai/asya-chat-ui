import { useEffect, useMemo, useRef, useState } from "react"
import { useNavigate, useParams } from "react-router"
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
import { useI18n, type TranslationKey } from "@/lib/i18n-context"
import type {
  Agent,
  AgentShare,
  AgentShareSuggestion,
  AgentSource,
  AgentSourceStatus,
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
import { AppShell } from "@/components/AppShell"

type Tab = "chats" | "files" | "instructions"

const SOURCE_ACCEPT =
  ".doc,.docx,.xls,.xlsx,.md,.txt,.pdf,.ppt,.pptx,.csv,.json,.xml,.html,.htm,.yaml,.yml,.odt,.ods,.odp,.odg,.odf"

const formatDate = (value: string) => {
  try {
    return new Date(value).toLocaleString()
  } catch {
    return value
  }
}

const SOURCE_STATUS_KEYS: Record<AgentSourceStatus, TranslationKey> = {
  queued: "project_status_queued",
  indexing: "project_status_indexing",
  ready: "project_status_ready",
  failed: "project_status_failed",
}

const ROLE_KEYS = {
  viewer: "project_role_viewer",
  editor: "project_role_editor",
  owner: "project_role_owner",
} as const satisfies Record<"viewer" | "editor" | "owner", TranslationKey>

export const ProjectPage = () => {
  const navigate = useNavigate()
  const { projectId } = useParams<{ projectId: string }>()
  const { t, tCount } = useI18n()
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

  const instrNameRef = useRef<HTMLInputElement | null>(null)
  const instrDescriptionRef = useRef<HTMLInputElement | null>(null)
  const instrPromptRef = useRef<HTMLTextAreaElement | null>(null)
  const [instrModelId, setInstrModelId] = useState("")

  const [sourceFiles, setSourceFiles] = useState<File[]>([])
  const [sourceFileKey, setSourceFileKey] = useState(0)
  const [sourceUrl, setSourceUrl] = useState("")
  const [sourceUrlTitle, setSourceUrlTitle] = useState("")

  const [renameOpen, setRenameOpen] = useState(false)
  const [renameValue, setRenameValue] = useState("")
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [shareOpen, setShareOpen] = useState(false)
  const [shareQuery, setShareQuery] = useState("")
  const [shareSelected, setShareSelected] = useState<AgentShareSuggestion | null>(null)
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
      if (instrNameRef.current) instrNameRef.current.value = found.name
      if (instrDescriptionRef.current) {
        instrDescriptionRef.current.value = found.description ?? ""
      }
      if (instrPromptRef.current) instrPromptRef.current.value = found.master_prompt ?? ""
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
          setError(t("project_not_found_access"))
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
        setError(err instanceof Error ? err.message : t("project_load_one_failed"))
      } finally {
        setLoading(false)
      }
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, t])

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
        .shareSuggestions(project.id, shareQuery)
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
  }, [shareOpen, shareQuery, project, shares])

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
      setError(err instanceof Error ? err.message : t("common_error"))
    } finally {
      setBusy(false)
    }
  }

  const handleSaveInstructions = () =>
    withBusy(async () => {
      if (!project) return
      if (!(instrNameRef.current?.value.trim() ?? "")) {
        setError(t("project_name_required"))
        return
      }
      await agentApi.update(project.id, {
        name: instrNameRef.current?.value.trim() ?? "",
        description: instrDescriptionRef.current?.value.trim() || null,
        master_prompt: instrPromptRef.current?.value.trim() || null,
        preferred_model_id: instrModelId || null,
      })
      await loadProject()
      notify(t("project_instructions_saved"))
    })

  const handleUploadFiles = () =>
    withBusy(async () => {
      if (!project || sourceFiles.length === 0) return
      const attachments = await readFilesAsAttachments(sourceFiles)
      if (attachments.length === 0) throw new Error(t("project_files_read_failed"))
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
      notify(
        tCount("project_file_uploaded", "project_files_uploaded", attachments.length)
      )
    })

  const handleAddUrl = () =>
    withBusy(async () => {
      if (!project) return
      const url = sourceUrl.trim()
      if (!url) {
        setError(t("project_url_required"))
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
      notify(t("project_url_added"))
    })

  const handleReindex = (sourceId: string) =>
    withBusy(async () => {
      if (!project) return
      await agentApi.reindexSource(project.id, sourceId)
      await loadSources()
      notify(t("project_reindex_started"))
    })

  const handleDeleteSource = (sourceId: string) =>
    withBusy(async () => {
      if (!project) return
      await agentApi.removeSource(project.id, sourceId)
      await loadSources()
      notify(t("project_file_removed"))
    })

  const handleRename = () =>
    withBusy(async () => {
      if (!project) return
      const name = renameValue.trim()
      if (!name) {
        setError(t("project_name_required"))
        return
      }
      await agentApi.update(project.id, { name })
      setRenameOpen(false)
      await loadProject()
      notify(t("project_renamed"))
    })

  const handleDeleteProject = () =>
    withBusy(async () => {
      if (!project) return
      await agentApi.remove(project.id)
      navigate("/projects")
    })

  const openShare = () => {
    setShareOpen(true)
    setShareQuery("")
    setShareSelected(null)
    setSuggestionsOpen(false)
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
      if (!shareSelected) {
        setError(t("project_member_required"))
        return
      }
      await agentApi.share(project.id, { user_id: shareSelected.user_id, role: shareRole })
      setShareQuery("")
      setShareSelected(null)
      setSuggestionsOpen(false)
      setShares(await agentApi.listShares(project.id))
      notify(t("project_access_granted"))
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
      <AppShell activeSection="projects">
        {(sidebarControls) => (
          <div className="flex h-full flex-col">
            <div className="flex h-15 shrink-0 items-center px-3">{sidebarControls}</div>
            <div className="flex flex-1 items-center justify-center px-4">
              <p className="text-muted-foreground text-sm">{t("project_loading_one")}</p>
            </div>
          </div>
        )}
      </AppShell>
    )
  }

  if (!project) {
    return (
      <AppShell activeSection="projects">
        {(sidebarControls) => (
          <div className="flex h-full flex-col">
            <div className="flex h-15 shrink-0 items-center px-3">{sidebarControls}</div>
            <div className="flex flex-1 flex-col items-center justify-center gap-4 px-4 text-center">
              <p className="text-muted-foreground">{error ?? t("project_not_found")}</p>
              <Button onClick={() => navigate("/projects")}>{t("project_back_to_projects")}</Button>
            </div>
          </div>
        )}
      </AppShell>
    )
  }

  const tabs: { id: Tab; label: string; count?: number }[] = [
    { id: "chats", label: t("project_tab_chats"), count: chats.length },
    { id: "files", label: t("project_tab_files"), count: sources.length },
    { id: "instructions", label: t("project_tab_instructions") },
  ]

  return (
    <AppShell activeSection="projects">
      {(sidebarControls) => (
    <div className="mx-auto flex h-full w-full max-w-5xl flex-col gap-6 overflow-y-auto p-4 sm:p-6">
      <div>
        <div className="-ml-2 mb-2 flex items-center gap-1">
          {sidebarControls}
          <Button
            variant="ghost"
            size="sm"
            className="text-muted-foreground"
            onClick={() => navigate("/projects")}
          >
            <ArrowLeft className="h-4 w-4" aria-hidden="true" />
            {t("project_title")}
          </Button>
        </div>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h1 className="truncate font-heading text-4xl font-normal leading-10">{project.name}</h1>
            {project.description ? (
              <p className="text-muted-foreground mt-1 text-sm">{project.description}</p>
            ) : null}
          </div>
          <div className="flex items-center gap-2">
            <Button onClick={startChat}>
              <MessageSquarePlus className="h-4 w-4" aria-hidden="true" />
              {t("chat_new_title")}
            </Button>
            {canEdit ? (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="outline" size="icon" aria-label={t("project_actions_aria")}>
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
                    {t("project_rename")}
                  </DropdownMenuItem>
                  {isOwner ? (
                    <DropdownMenuItem onClick={openShare}>{t("project_share")}</DropdownMenuItem>
                  ) : null}
                  {isOwner ? (
                    <>
                      <DropdownMenuSeparator />
                      <DropdownMenuItem variant="destructive" onClick={() => setDeleteOpen(true)}>
                        {t("project_delete")}
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
          <div className="flex flex-col items-center justify-center gap-3 rounded-[var(--radius-card)] border border-dashed bg-card py-16 text-center">
            <MessageSquarePlus className="text-muted-foreground h-6 w-6" aria-hidden="true" />
            <p className="text-muted-foreground text-sm">{t("project_no_chats")}</p>
            <Button onClick={startChat}>{t("project_start_chat")}</Button>
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
                  {chat.title || t("project_untitled_chat")}
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
                  {t("project_upload_files")}
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
                    {t("project_upload")}
                  </Button>
                  {sourceFiles.length > 0 ? (
                    <span className="text-muted-foreground text-xs">
                      {t("project_files_selected", { count: sourceFiles.length })}
                    </span>
                  ) : null}
                </div>
              </div>
              <div className="space-y-2 border-t pt-4">
                <p className="flex items-center gap-2 text-sm font-medium">
                  <Link2 className="h-4 w-4" aria-hidden="true" />
                  {t("project_add_url")}
                </p>
                <Input
                  placeholder={t("project_url_placeholder")}
                  value={sourceUrl}
                  onChange={(event) => setSourceUrl(event.target.value)}
                />
                <Input
                  placeholder={t("project_url_title_placeholder")}
                  value={sourceUrlTitle}
                  onChange={(event) => setSourceUrlTitle(event.target.value)}
                />
                <Button onClick={handleAddUrl} disabled={!sourceUrl.trim() || busy} size="sm">
                  {t("project_add_url_action")}
                </Button>
              </div>
            </Card>
          ) : null}

          <div className="space-y-2">
            {sources.length === 0 ? (
              <p className="text-muted-foreground text-sm">{t("project_no_files")}</p>
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
                        {t(SOURCE_STATUS_KEYS[source.status])}
                      </Badge>
                      {canEdit ? (
                        <>
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-8 w-8"
                            aria-label={t("project_reindex_aria")}
                            disabled={busy}
                            onClick={() => handleReindex(source.id)}
                          >
                            <RefreshCw className="h-4 w-4" aria-hidden="true" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon"
                            className="text-destructive h-8 w-8"
                            aria-label={t("project_delete_file_aria")}
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

      <div className={tab === "instructions" ? "max-w-2xl space-y-4" : "hidden"}>
          <div className="space-y-2">
            <label className="text-sm font-medium">{t("project_name_label")}</label>
            <Input
              ref={instrNameRef}
              defaultValue={project.name}
              disabled={!canEdit}
            />
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium">{t("project_description_label")}</label>
            <Input
              ref={instrDescriptionRef}
              defaultValue={project.description ?? ""}
              placeholder={t("project_description_placeholder")}
              disabled={!canEdit}
            />
          </div>
          <div className="space-y-2">
            <label className="flex items-center gap-2 text-sm font-medium">
              <Settings2 className="h-4 w-4" aria-hidden="true" />
              {t("project_custom_instructions")}
            </label>
            <p className="text-muted-foreground text-xs">{t("project_custom_instructions_help")}</p>
            <Textarea
              ref={instrPromptRef}
              rows={8}
              defaultValue={project.master_prompt ?? ""}
              placeholder={t("project_custom_instructions_placeholder")}
              disabled={!canEdit}
            />
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium">{t("project_preferred_model")}</label>
            <Select
              value={instrModelId || "__none__"}
              onValueChange={(value) => setInstrModelId(value === "__none__" ? "" : value)}
              disabled={!canEdit}
            >
              <SelectTrigger className="w-full">
                <SelectValue placeholder={t("project_no_preferred_model")} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__none__">{t("project_no_preferred_model")}</SelectItem>
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
              {t("project_save_changes")}
            </Button>
          ) : (
            <p className="text-muted-foreground text-sm">{t("project_view_only")}</p>
          )}
        </div>

      <Dialog open={renameOpen} onOpenChange={setRenameOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("project_rename_title")}</DialogTitle>
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
              {t("common_cancel")}
            </Button>
            <Button onClick={handleRename} disabled={busy || !renameValue.trim()}>
              {t("common_save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("project_delete_title")}</DialogTitle>
          </DialogHeader>
          <p className="text-muted-foreground text-sm">{t("project_delete_desc")}</p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteOpen(false)}>
              {t("common_cancel")}
            </Button>
            <Button variant="destructive" onClick={handleDeleteProject} disabled={busy}>
              {t("common_delete")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={shareOpen} onOpenChange={setShareOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("project_share_title")}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="flex items-start gap-2">
              <div className="relative flex-1">
                <Input
                  placeholder={t("project_share_search_placeholder")}
                  value={shareQuery}
                  onChange={(event) => {
                    setShareQuery(event.target.value)
                    setShareSelected(null)
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
                          setShareSelected(suggestion)
                          setShareQuery(
                            suggestion.display_name
                              ? `${suggestion.display_name} (${suggestion.email})`
                              : suggestion.email
                          )
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
                  <SelectItem value="viewer">{t("project_role_viewer")}</SelectItem>
                  <SelectItem value="editor">{t("project_role_editor")}</SelectItem>
                  <SelectItem value="owner">{t("project_role_owner")}</SelectItem>
                </SelectContent>
              </Select>
              <Button onClick={handleAddShare} disabled={busy || !shareSelected}>
                {t("project_share_add")}
              </Button>
            </div>
            <p className="text-muted-foreground text-xs">{t("project_share_org_only_hint")}</p>
            <div className="space-y-2">
              {shares.length === 0 ? (
                <p className="text-muted-foreground text-sm">{t("project_share_empty")}</p>
              ) : (
                shares.map((share) => (
                  <div
                    key={share.user_id}
                    className="flex items-center justify-between gap-2 rounded-md border px-3 py-2"
                  >
                    <div className="min-w-0">
                      <p className="truncate text-sm">{share.email}</p>
                      <p className="text-muted-foreground text-xs">
                        {t(ROLE_KEYS[share.role])}
                      </p>
                    </div>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="text-destructive h-8 w-8"
                      aria-label={t("project_remove_access_aria")}
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
              {t("common_close")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
      )}
    </AppShell>
  )
}

export default ProjectPage
