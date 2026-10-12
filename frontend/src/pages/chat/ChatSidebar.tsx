import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type HTMLAttributes,
  type MouseEvent,
  type ReactNode,
  type SyntheticEvent,
} from "react"
import { Link, useParams } from "@tanstack/react-router"
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
import {
  ArrowLeft,
  GripVertical,
  Link2,
  Loader2,
  Lock,
  MessageSquareDot,
  MoreVertical,
  Pencil,
  Pin,
  Plus,
  RefreshCw,
  Search,
  SquareArrowOutUpRight,
  Star,
  Trash2,
  Upload,
  X,
} from "lucide-react"
import { toast } from "sonner"

import { agentApi } from "@/lib/api"
import {
  chatGenerationIndicatorsStore,
  useChatGenerationIndicators,
} from "@/lib/chat-generation-indicators"
import {
  draftTitleFromText,
  composerDraftStore,
  useComposerDrafts,
} from "@/lib/composer-drafts"
import { readFilesAsAttachments } from "@/lib/file-utils"
import { useI18n } from "@/lib/i18n-context"
import { orgStore, showProjectChatIndicationStore } from "@/lib/storage"
import type {
  Agent,
  AgentShare,
  AgentShareSuggestion,
  AgentSource,
  Chat,
  Prompt,
} from "@/lib/types"
import { BrandLogo } from "@/components/BrandLogo"
import { PromptFormDialog } from "@/components/PromptFormDialog"
import {
  useAgents,
  useChats,
  useChatSearch,
  useCreateAgent,
  useDeleteAgent,
  useDeleteChat,
  useDeletePrompt,
  useOrgsMine,
  usePinChat,
  usePrompts,
  useRenameChat,
  useUpdateAgent,
  useUpdateAgentOrder,
} from "@/hooks/use-chat-query"
import { Button } from "@/components/ui/button"
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from "@/components/ui/context-menu"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { cn } from "@/lib/utils"

type SidebarTab = "history" | "projects" | "prompts"
type ProjectSubTab = "chats" | "files" | "instructions"

type SortableProjectRowProps = {
  setNodeRef: (node: HTMLElement | null) => void
  style: CSSProperties
  attributes: HTMLAttributes<HTMLElement>
  listeners: Record<string, (event: SyntheticEvent) => void> | undefined
}

const SortableProjectRow = ({
  id,
  children,
}: {
  id: string
  children: (props: SortableProjectRowProps) => ReactNode
}) => {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id,
  })
  const style: CSSProperties = {
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
        attributes: attributes as HTMLAttributes<HTMLElement>,
        listeners: listeners as SortableProjectRowProps["listeners"],
      })}
    </>
  )
}

type ChatSidebarProps = {
  title: string
  labels: {
    newChat: string
    history: string
    projects: string
    prompts?: string
    close?: string
  }
  activeSection?: "history" | "projects" | null
  activeChatId?: string | null
  activeAgentId?: string | null
  onNewChat: (options?: { agentId?: string | null }) => void
  onOpenHistory: () => void
  onOpenProjects: () => void
  onSelectChat?: (chat: Chat) => void
  onToggleShareChat?: (chat: Chat) => void
  onInsertPrompt?: (body: string) => void
  onPromptCountChange?: (count: number) => void
  footer?: ReactNode
  onRequestClose?: () => void
  hideBrandHeader?: boolean
}

const parseChatDate = (value: string) => {
  const hasTimezone = /[zZ]|[+-]\d{2}:?\d{2}$/.test(value)
  return new Date(hasTimezone ? value : `${value}Z`)
}

const groupChatsByDate = (
  chats: Chat[],
  locale: string,
  labels: { today: string; yesterday: string }
) => {
  const startOfToday = new Date()
  startOfToday.setHours(0, 0, 0, 0)
  const startOfYesterday = new Date(startOfToday)
  startOfYesterday.setDate(startOfYesterday.getDate() - 1)

  const buckets = new Map<string, Chat[]>()
  const order: string[] = []

  for (const chat of chats) {
    const date = parseChatDate(chat.last_activity_at || chat.created_at)
    const day = new Date(date)
    day.setHours(0, 0, 0, 0)
    let label: string
    if (day.getTime() === startOfToday.getTime()) {
      label = labels.today
    } else if (day.getTime() === startOfYesterday.getTime()) {
      label = labels.yesterday
    } else {
      label = new Intl.DateTimeFormat(locale, {
        month: "short",
        day: "numeric",
        year: date.getFullYear() === startOfToday.getFullYear() ? undefined : "numeric",
      }).format(date)
    }
    if (!buckets.has(label)) {
      buckets.set(label, [])
      order.push(label)
    }
    buckets.get(label)!.push(chat)
  }

  return order.map((label) => ({
    label,
    chats: buckets.get(label) ?? [],
  }))
}

const staggerStyle = (stagger: number): CSSProperties =>
  ({ "--stagger": stagger } as CSSProperties)

const SessionDateHeading = ({
  label,
  stagger = 0,
}: {
  label: string
  stagger?: number
}) => (
  <div
    className="sidebar-stagger-item flex items-center pt-1.5 pr-0 pb-1 pl-2.5 border-border border-b w-full"
    style={staggerStyle(stagger)}
  >
    <p className="font-semibold text-badge-fg text-xs leading-4">{label}</p>
  </div>
)

const SessionSearchInput = ({
  placeholder,
  onDebouncedChange,
}: {
  placeholder: string
  onDebouncedChange: (value: string) => void
}) => {
  const [query, setQuery] = useState("")

  useEffect(() => {
    const timerId = window.setTimeout(() => onDebouncedChange(query.trim()), 250)
    return () => window.clearTimeout(timerId)
  }, [query, onDebouncedChange])

  return (
    <div className="relative w-full">
      <Search
        aria-hidden="true"
        className="top-1/2 left-3 absolute size-4 text-muted-foreground -translate-y-1/2 pointer-events-none"
      />
      <Input
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        placeholder={placeholder}
        aria-label={placeholder}
        className="bg-transparent pl-9 border-border rounded-lg h-9"
        onClick={(event) => event.stopPropagation()}
      />
    </div>
  )
}

const FigmaIcon = ({ src, className }: { src: string; className?: string }) => (
  <span
    aria-hidden="true"
    className={cn("size-4 figma-icon shrink-0", className)}
    style={{ maskImage: `url('${src}')` }}
  />
)

const sourceStatusLabel = (
  status: AgentSource["status"],
  t: (key: "project_status_queued" | "project_status_indexing" | "project_status_ready" | "project_status_failed") => string
) => {
  if (status === "queued") return t("project_status_queued")
  if (status === "indexing") return t("project_status_indexing")
  if (status === "failed") return t("project_status_failed")
  return t("project_status_ready")
}

export const ChatSidebar = ({
  title,
  labels,
  activeSection,
  activeChatId = null,
  activeAgentId = null,
  onNewChat,
  onOpenHistory: _onOpenHistory,
  onOpenProjects: _onOpenProjects,
  onSelectChat,
  onToggleShareChat,
  onInsertPrompt,
  onPromptCountChange,
  footer,
  onRequestClose,
  hideBrandHeader = false,
}: ChatSidebarProps) => {
  const { chatId: routeChatId } = useParams({ strict: false })
  const { locale, t } = useI18n()
  const { data: orgs = [] } = useOrgsMine()
  const orgId = orgStore.get() ?? orgs[0]?.id ?? null
  const { data: chats = [] } = useChats(orgId)
  const deleteChatMutation = useDeleteChat(orgId)
  const renameChatMutation = useRenameChat(orgId)
  const pinChatMutation = usePinChat(orgId)
  const createAgentMutation = useCreateAgent(orgId)
  const updateAgentMutation = useUpdateAgent(orgId)
  const deleteAgentMutation = useDeleteAgent(orgId)
  const updateAgentOrderMutation = useUpdateAgentOrder(orgId)
  const deletePromptMutation = useDeletePrompt()
  const { data: agents = [] } = useAgents(orgId)
  const projectSensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
  )

  const handleProjectDragEnd = (event: DragEndEvent) => {
    const { active, over } = event
    if (!over || active.id === over.id) return
    const oldIndex = agents.findIndex((agent) => agent.id === active.id)
    const newIndex = agents.findIndex((agent) => agent.id === over.id)
    if (oldIndex < 0 || newIndex < 0) return
    void updateAgentOrderMutation.mutateAsync(arrayMove(agents, oldIndex, newIndex))
  }
  const { data: prompts = [] } = usePrompts(activeAgentId ?? null)
  const [tab, setTab] = useState<SidebarTab>(() =>
    activeSection === "projects" ? "projects" : "history"
  )
  const [focusedAgentId, setFocusedAgentId] = useState<string | null>(
    () => (activeSection === "projects" ? activeAgentId : null)
  )
  const [projectSubTab, setProjectSubTab] = useState<ProjectSubTab>("chats")
  const [sources, setSources] = useState<AgentSource[]>([])
  const [sourcesLoading, setSourcesLoading] = useState(false)
  const [urlOpen, setUrlOpen] = useState(false)
  const [sourceUrl, setSourceUrl] = useState("")
  const [sourceUrlTitle, setSourceUrlTitle] = useState("")
  const [sourceBusy, setSourceBusy] = useState(false)
  const [deletingSourceId, setDeletingSourceId] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const instrNameRef = useRef<HTMLInputElement | null>(null)
  const instrDescriptionRef = useRef<HTMLInputElement | null>(null)
  const instrPromptRef = useRef<HTMLTextAreaElement | null>(null)
  const [sessionQueryDebounced, setSessionQueryDebounced] = useState("")
  const [renameChat, setRenameChat] = useState<Chat | null>(null)
  const [renameValue, setRenameValue] = useState("")
  const [deleteConfirmChat, setDeleteConfirmChat] = useState<Chat | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [creating, setCreating] = useState(false)
  const [hasNewName, setHasNewName] = useState(false)
  const newNameRef = useRef<HTMLInputElement | null>(null)
  const newInstructionsRef = useRef<HTMLTextAreaElement | null>(null)
  const [promptFormOpen, setPromptFormOpen] = useState(false)
  const [editingPrompt, setEditingPrompt] = useState<Prompt | null>(null)
  const [deletePromptTarget, setDeletePromptTarget] = useState<Prompt | null>(null)
  const [deletingPrompt, setDeletingPrompt] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)
  const [deleteProjectOpen, setDeleteProjectOpen] = useState(false)
  const [deletingProject, setDeletingProject] = useState(false)
  const [projectActionError, setProjectActionError] = useState<string | null>(null)
  const [shareOpen, setShareOpen] = useState(false)
  const [shareBusy, setShareBusy] = useState(false)
  const [shareQuery, setShareQuery] = useState("")
  const [shareSelected, setShareSelected] = useState<AgentShareSuggestion | null>(null)
  const [shareRole, setShareRole] = useState<"owner" | "editor" | "viewer">("viewer")
  const [shareSuggestions, setShareSuggestions] = useState<AgentShareSuggestion[]>([])
  const [suggestionsOpen, setSuggestionsOpen] = useState(false)
  const [shares, setShares] = useState<AgentShare[]>([])
  const { data: searchedChats = [] } = useChatSearch(orgId, sessionQueryDebounced)
  const currentChatId = activeChatId ?? routeChatId ?? null
  const generationIndicators = useChatGenerationIndicators()
  const composerDrafts = useComposerDrafts()

  const focusedAgent =
    agents.find((agent) => agent.id === focusedAgentId) ??
    (focusedAgentId && activeAgentId === focusedAgentId
      ? agents.find((agent) => agent.id === activeAgentId) ?? null
      : null)

  useEffect(() => {
    if (activeSection === "projects") setTab("projects")
    else if (activeSection === "history") setTab("history")
  }, [activeSection])

  const prevActiveAgentIdRef = useRef(activeAgentId)
  useEffect(() => {
    const prev = prevActiveAgentIdRef.current
    prevActiveAgentIdRef.current = activeAgentId
    if (activeAgentId && activeAgentId !== prev) {
      setFocusedAgentId(activeAgentId)
      return
    }
    if (!activeAgentId) setFocusedAgentId(null)
  }, [activeAgentId])

  useEffect(() => {
    onPromptCountChange?.(prompts.length)
  }, [prompts.length, onPromptCountChange])

  const loadSources = useCallback(async (agentId: string) => {
    setSourcesLoading(true)
    try {
      setSources(await agentApi.listSources(agentId))
    } catch {
      setSources([])
    } finally {
      setSourcesLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!focusedAgentId) {
      setSources([])
      return
    }
    void loadSources(focusedAgentId)
  }, [focusedAgentId, loadSources])

  useEffect(() => {
    if (!focusedAgent || projectSubTab !== "instructions") return
    if (instrNameRef.current) instrNameRef.current.value = focusedAgent.name
    if (instrDescriptionRef.current) {
      instrDescriptionRef.current.value = focusedAgent.description ?? ""
    }
    if (instrPromptRef.current) {
      instrPromptRef.current.value = focusedAgent.master_prompt ?? ""
    }
  }, [focusedAgent, projectSubTab])

  const chatDisplayTitle = (chat: Chat) => {
    const serverTitle = (chat.title || "").trim()
    if (serverTitle) return serverTitle
    const draftTitle = draftTitleFromText(composerDrafts[chat.id] ?? "")
    if (draftTitle) return draftTitle
    return t("chat_new_title")
  }

  const chatSessionStatus = (chatId: string) => {
    const status = generationIndicators[chatId] ?? null
    if (currentChatId === chatId && status === "ready") return null
    return status
  }

  const handleSessionQueryChange = useCallback((value: string) => {
    setSessionQueryDebounced(value)
  }, [])

  const historySourceChats = useMemo(() => {
    const currentOrgChatIds = new Set(chats.map((chat) => chat.id))
    const sourceChats = sessionQueryDebounced
      ? searchedChats.filter((chat) => currentOrgChatIds.has(chat.id))
      : chats
    return [...sourceChats].sort(
      (a, b) =>
        parseChatDate(b.last_activity_at || b.created_at).getTime() -
        parseChatDate(a.last_activity_at || a.created_at).getTime()
    )
  }, [chats, searchedChats, sessionQueryDebounced])

  // Global history pins only non-project chats. Project pins stay project-scoped.
  const pinnedChats = useMemo(
    () => historySourceChats.filter((chat) => chat.is_pinned && !chat.agent_id),
    [historySourceChats]
  )

  const sessionGroups = useMemo(() => {
    const forDateGroups = historySourceChats.filter(
      (chat) => Boolean(chat.agent_id) || !chat.is_pinned
    )
    return groupChatsByDate(forDateGroups, locale, {
      today: t("chat_group_today"),
      yesterday: t("chat_group_yesterday"),
    })
  }, [historySourceChats, locale, t])

  const projectChats = useMemo(
    () =>
      chats
        .filter((chat) => chat.agent_id === focusedAgentId)
        .sort(
          (a, b) =>
            parseChatDate(b.last_activity_at || b.created_at).getTime() -
            parseChatDate(a.last_activity_at || a.created_at).getTime()
        ),
    [chats, focusedAgentId]
  )

  const projectPinnedChats = useMemo(
    () => projectChats.filter((chat) => chat.is_pinned),
    [projectChats]
  )

  const projectSessionGroups = useMemo(() => {
    const unpinned = projectChats.filter((chat) => !chat.is_pinned)
    return groupChatsByDate(unpinned, locale, {
      today: t("chat_group_today"),
      yesterday: t("chat_group_yesterday"),
    })
  }, [projectChats, locale, t])

  const togglePin = (chat: Chat) => {
    void pinChatMutation.mutateAsync({
      chatId: chat.id,
      is_pinned: !chat.is_pinned,
    })
  }

  const chatHref = (chat: Chat) =>
    chat.agent_id
      ? `/chat/${chat.id}?agent=${encodeURIComponent(chat.agent_id)}`
      : `/chat/${chat.id}`

  const openChatInNewTab = (chat: Chat) => {
    window.open(chatHref(chat), "_blank", "noopener,noreferrer")
  }

  const copyLinkWithToast = async (pathOrUrl: string) => {
    const fullUrl = pathOrUrl.startsWith("http")
      ? pathOrUrl
      : `${window.location.origin}${pathOrUrl}`
    try {
      await navigator.clipboard.writeText(fullUrl)
      toast.success(t("chat_share_dialog_title"), {
        description: t("chat_share_dialog_copied"),
      })
    } catch {
      toast.message(t("chat_share_dialog_title"), {
        description: fullUrl,
      })
    }
  }

  const shareInternalLink = (chat: Chat) => {
    void copyLinkWithToast(chatHref(chat))
  }

  const togglePublicShare = (chat: Chat) => {
    if (chat.is_incognito) {
      toast.error(t("chat_share_incognito_blocked"))
      return
    }
    onToggleShareChat?.(chat)
  }

  const markChatUnread = (chat: Chat) => {
    if (generationIndicators[chat.id] === "generating") return
    chatGenerationIndicatorsStore.finish(chat.id, true)
  }

  const onChatLinkClick = (event: MouseEvent, chat: Chat) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) {
      return
    }
    if (onSelectChat) {
      event.preventDefault()
      onSelectChat(chat)
    }
    onRequestClose?.()
  }

  const openProject = (agent: Agent) => {
    setFocusedAgentId(agent.id)
    setProjectSubTab("chats")
    setUrlOpen(false)
    if (onSelectChat) {
      // embed: stay in sheet / open project route
      window.open(`/projects/${encodeURIComponent(agent.id)}`, "_blank", "noopener,noreferrer")
      return
    }
    onNewChat({ agentId: agent.id })
  }

  const openCreateProject = () => {
    setCreateError(null)
    setCreateOpen(true)
  }

  const openCreatePrompt = () => {
    setEditingPrompt(null)
    setPromptFormOpen(true)
  }

  const handleCreateProject = async () => {
    const name = newNameRef.current?.value.trim() ?? ""
    if (!name) {
      setCreateError(t("project_name_required"))
      return
    }
    try {
      setCreating(true)
      setCreateError(null)
      const created = await createAgentMutation.mutateAsync({
        name,
        master_prompt: newInstructionsRef.current?.value.trim() || null,
      })
      setCreateOpen(false)
      setHasNewName(false)
      openProject(created)
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : t("project_create_failed"))
    } finally {
      setCreating(false)
    }
  }

  const openRename = (chat: Chat) => {
    setRenameChat(chat)
    setRenameValue(chat.title || "")
  }

  const saveRename = async () => {
    if (!renameChat) return
    const nextTitle = renameValue.trim()
    if (!nextTitle) return
    await renameChatMutation.mutateAsync({ chatId: renameChat.id, title: nextTitle })
    setRenameChat(null)
  }

  const confirmDelete = async () => {
    if (!deleteConfirmChat) return
    const chatIdToDelete = deleteConfirmChat.id
    const agentIdToKeep = deleteConfirmChat.agent_id
    await deleteChatMutation.mutateAsync(chatIdToDelete)
    composerDraftStore.clear(chatIdToDelete)
    setDeleteConfirmChat(null)
    if (currentChatId === chatIdToDelete) {
      if (onSelectChat) onNewChat()
      else onNewChat({ agentId: agentIdToKeep ?? null })
    }
  }

  const handleDeletePrompt = async () => {
    if (!deletePromptTarget) return
    try {
      setDeletingPrompt(true)
      await deletePromptMutation.mutateAsync(deletePromptTarget.id)
      setDeletePromptTarget(null)
    } catch {
      // keep open
    } finally {
      setDeletingPrompt(false)
    }
  }

  const handleUploadFiles = async (files: FileList | null) => {
    if (!focusedAgentId || !files?.length) return
    try {
      setSourceBusy(true)
      const list = Array.from(files)
      const attachments = await readFilesAsAttachments(list)
      await Promise.all(
        attachments.map((attachment, index) =>
          agentApi.createSource(focusedAgentId, {
            kind: "file",
            title: list[index]?.name || attachment.file_name,
            file_name: attachment.file_name,
            content_type: attachment.content_type,
            data_base64: attachment.data_base64,
          })
        )
      )
      await loadSources(focusedAgentId)
    } finally {
      setSourceBusy(false)
      if (fileInputRef.current) fileInputRef.current.value = ""
    }
  }

  const handleAddUrl = async () => {
    if (!focusedAgentId) return
    const url = sourceUrl.trim()
    if (!url) return
    try {
      setSourceBusy(true)
      await agentApi.createSource(focusedAgentId, {
        kind: "url",
        title: sourceUrlTitle.trim() || null,
        url,
      })
      setSourceUrl("")
      setSourceUrlTitle("")
      setUrlOpen(false)
      await loadSources(focusedAgentId)
    } finally {
      setSourceBusy(false)
    }
  }

  const handleSaveInstructions = async () => {
    if (!focusedAgentId) return
    await updateAgentMutation.mutateAsync({
      agentId: focusedAgentId,
      payload: {
        name: instrNameRef.current?.value.trim() ?? "",
        description: instrDescriptionRef.current?.value.trim() || null,
        master_prompt: instrPromptRef.current?.value.trim() || null,
      },
    })
  }

  const openShare = async (agent: Agent) => {
    setFocusedAgentId(agent.id)
    setProjectActionError(null)
    setShareQuery("")
    setShareSelected(null)
    setShareRole("viewer")
    setSuggestionsOpen(false)
    setShareOpen(true)
    try {
      setShares(await agentApi.listShares(agent.id))
    } catch {
      setShares([])
    }
  }

  const handleAddShare = async () => {
    if (!focusedAgentId || !shareSelected) {
      setProjectActionError(t("project_share_target_required"))
      return
    }
    try {
      setShareBusy(true)
      setProjectActionError(null)
      if (shareSelected.kind === "org") {
        await agentApi.share(focusedAgentId, { org: true, role: shareRole })
      } else if (shareSelected.kind === "team") {
        if (!shareSelected.team_id) {
          setProjectActionError(t("project_share_target_required"))
          return
        }
        await agentApi.share(focusedAgentId, {
          team_id: shareSelected.team_id,
          role: shareRole,
        })
      } else {
        if (!shareSelected.user_id) {
          setProjectActionError(t("project_share_target_required"))
          return
        }
        await agentApi.share(focusedAgentId, {
          user_id: shareSelected.user_id,
          role: shareRole,
        })
      }
      setShareQuery("")
      setShareSelected(null)
      setSuggestionsOpen(false)
      setShares(await agentApi.listShares(focusedAgentId))
    } catch (err) {
      setProjectActionError(err instanceof Error ? err.message : t("common_error"))
    } finally {
      setShareBusy(false)
    }
  }

  const handleRemoveShare = async (share: AgentShare) => {
    if (!focusedAgentId) return
    try {
      setShareBusy(true)
      setProjectActionError(null)
      if (share.kind === "org") {
        await agentApi.unshareOrg(focusedAgentId)
      } else if (share.kind === "team") {
        if (!share.team_id) return
        await agentApi.unshareTeam(focusedAgentId, share.team_id)
      } else {
        if (!share.user_id) return
        await agentApi.unshare(focusedAgentId, share.user_id)
      }
      setShares(await agentApi.listShares(focusedAgentId))
    } catch (err) {
      setProjectActionError(err instanceof Error ? err.message : t("common_error"))
    } finally {
      setShareBusy(false)
    }
  }

  const handleDeleteProject = async () => {
    if (!focusedAgent || deletingProject) return
    try {
      setDeletingProject(true)
      setProjectActionError(null)
      const deletedId = focusedAgent.id
      await deleteAgentMutation.mutateAsync(deletedId)
      setDeleteProjectOpen(false)
      setFocusedAgentId(null)
      if (activeAgentId === deletedId) {
        onNewChat({ agentId: null })
      }
    } catch (err) {
      setProjectActionError(err instanceof Error ? err.message : t("common_error"))
    } finally {
      setDeletingProject(false)
    }
  }

  useEffect(() => {
    if (!shareOpen || !focusedAgentId) {
      setShareSuggestions([])
      return
    }
    let cancelled = false
    const handle = window.setTimeout(() => {
      void agentApi
        .shareSuggestions(focusedAgentId, shareQuery)
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
  }, [shareOpen, shareQuery, focusedAgentId, shares])

  const tabs: { id: SidebarTab; label: string; icon: string }[] = [
    { id: "history", label: labels.history, icon: "/icon-history.svg" },
    { id: "projects", label: labels.projects, icon: "/icon-projects.svg" },
    {
      id: "prompts",
      label: labels.prompts ?? t("prompt_library"),
      icon: "/icon-prompts.svg",
    },
  ]

  const renderChatMenus = (
    chat: Chat,
    Item: typeof DropdownMenuItem | typeof ContextMenuItem,
    Separator: typeof DropdownMenuSeparator | typeof ContextMenuSeparator
  ) => {
    const canMarkUnread = generationIndicators[chat.id] !== "generating"
    return (
      <>
        <Item onClick={() => openChatInNewTab(chat)}>
          <SquareArrowOutUpRight />
          {t("chat_open_in_new_tab")}
        </Item>
        <Item onClick={() => togglePin(chat)}>
          <Pin />
          {chat.is_pinned ? t("chat_unpin") : t("chat_pin")}
        </Item>
        <Item onClick={() => openRename(chat)}>
          <Pencil />
          {t("chat_rename")}
        </Item>
        {canMarkUnread ? (
          <Item onClick={() => markChatUnread(chat)}>
            <MessageSquareDot />
            {t("chat_mark_unread")}
          </Item>
        ) : null}
        <Separator />
        {onToggleShareChat ? (
          chat.is_shared ? (
            <>
              {chat.share_token ? (
                <Item
                  onClick={() => void copyLinkWithToast(`/shared/${chat.share_token}`)}
                >
                  <Link2 />
                  {t("chat_share_public")}
                </Item>
              ) : null}
              <Item onClick={() => togglePublicShare(chat)}>
                <X />
                {t("chat_unshare_public")}
              </Item>
            </>
          ) : (
            <Item onClick={() => togglePublicShare(chat)}>
              <Link2 />
              {t("chat_share_public")}
            </Item>
          )
        ) : null}
        <Item onClick={() => shareInternalLink(chat)}>
          <Lock />
          {t("chat_share_internal")}
        </Item>
        <Separator />
        <Item variant="destructive" onClick={() => setDeleteConfirmChat(chat)}>
          <Trash2 />
          {t("chat_delete")}
        </Item>
      </>
    )
  }

  const renderChatRow = (chat: Chat, stagger = 0) => {
    const status = chatSessionStatus(chat.id)
    const isActive = currentChatId === chat.id
    const showStatusPill = status === "generating" || status === "ready"
    const showPinnedIcon = Boolean(chat.is_pinned) && !showStatusPill
    const showShareIcon = Boolean(chat.is_shared) && !showStatusPill
    const trailingIconCount = Number(showPinnedIcon) + Number(showShareIcon)
    const projectName =
      showProjectChatIndicationStore.get() && tab === "history" && chat.agent_id
        ? agents.find((agent) => agent.id === chat.agent_id)?.name ?? null
        : null
    return (
      <ContextMenu key={chat.id}>
        <ContextMenuTrigger asChild>
          <div
            className={cn(
              "sidebar-stagger-item group/chat-item relative flex items-start has-data-[state=open]:bg-accent hover:bg-accent p-1.5 rounded-lg w-full min-w-0",
              isActive && "bg-accent",
              status === "generating" && "bg-[rgba(14,97,69,0.1)]"
            )}
            style={staggerStyle(stagger)}
          >
            <Button
              variant="ghost"
              asChild
              className={cn(
                "justify-start hover:bg-transparent px-0 py-0 w-full min-w-0 h-auto min-h-4 font-normal text-sm text-left",
                showStatusPill ? "pr-24" : trailingIconCount > 1 ? "pr-16" : "pr-9"
              )}
            >
              <Link to={chatHref(chat)} onClick={(event) => onChatLinkClick(event, chat)}>
                <span className="flex flex-col items-start gap-1 w-full min-w-0">
                  <span className="block w-full min-w-0 text-sidebar-foreground text-sm truncate leading-4">
                    {chatDisplayTitle(chat)}
                  </span>
                  {projectName ? (
                    <span className="inline-flex max-w-full items-center gap-1 rounded-full bg-secondary px-2 py-0.5 text-xs leading-4 text-muted-foreground">
                      <span
                        aria-hidden="true"
                        className="figma-icon size-3.5 shrink-0"
                        style={{ maskImage: "url('/icon-projects.svg')" }}
                      />
                      <span className="truncate">{projectName}</span>
                    </span>
                  ) : null}
                </span>
              </Link>
            </Button>

            {status === "generating" ? (
              <span className="group-has-data-[state=open]/chat-item:invisible group-hover/chat-item:invisible inline-flex top-1 right-1.5 absolute items-center gap-0.5 bg-[#177c5a] px-1.5 py-0.5 rounded-full font-medium text-white text-xs pointer-events-none">
                <Loader2 aria-hidden="true" className="size-3.5 animate-spin" />
                {t("chat_status_in_progress")}
              </span>
            ) : null}
            {status === "ready" ? (
              <span className="group-has-data-[state=open]/chat-item:invisible group-hover/chat-item:invisible inline-flex top-1 right-1.5 absolute items-center bg-primary px-1.5 py-0.5 rounded-full font-medium text-primary-foreground text-xs pointer-events-none">
                {t("chat_status_unread")}
              </span>
            ) : null}

            {trailingIconCount > 0 ? (
              <span
                aria-hidden="true"
                className="top-0.5 right-0.5 absolute flex items-center text-muted-foreground pointer-events-none group-has-data-[state=open]/chat-item:invisible group-hover/chat-item:invisible"
              >
                {showShareIcon ? (
                  <span className="flex justify-center items-center size-7">
                    <Link2 className="size-4" />
                  </span>
                ) : null}
                {showPinnedIcon ? (
                  <span className="flex justify-center items-center size-7">
                    <Pin className="size-4 fill-current" />
                  </span>
                ) : null}
              </span>
            ) : null}

            <div
              className={cn(
                "top-0.5 right-0.5 absolute",
                "opacity-0 group-has-data-[state=open]/chat-item:opacity-100 group-hover/chat-item:opacity-100"
              )}
            >
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    className="size-7"
                    aria-label={t("chat_history_actions")}
                  >
                    <MoreVertical aria-hidden="true" className="size-4" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  {renderChatMenus(chat, DropdownMenuItem, DropdownMenuSeparator)}
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          </div>
        </ContextMenuTrigger>
        <ContextMenuContent>
          {renderChatMenus(chat, ContextMenuItem, ContextMenuSeparator)}
        </ContextMenuContent>
      </ContextMenu>
    )
  }

  const showProjectDetail = tab === "projects" && Boolean(focusedAgentId && focusedAgent)

  return (
    <nav aria-label={title} className="relative flex flex-col min-w-0 h-full min-h-0">
      {hideBrandHeader ? null : (
        <div className="flex justify-between items-center gap-2 px-2 pt-2 h-15 shrink-0">
          <div className="flex items-center gap-2 min-w-0">
            <span className="flex justify-center items-center size-11 shrink-0">
              <BrandLogo className="w-[39.54px] h-11" />
            </span>
            <p className="font-heading text-4xl truncate leading-9">{title}</p>
          </div>
          {onRequestClose ? (
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              onClick={onRequestClose}
              aria-label={labels.close}
            >
              <X aria-hidden="true" />
            </Button>
          ) : null}
        </div>
      )}

      <div className="flex flex-col flex-1 min-w-0 min-h-0">
        <div className="flex flex-col gap-1.5 px-2 pt-2 shrink-0">
          {hideBrandHeader && onRequestClose ? (
            <div className="flex justify-end">
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                onClick={onRequestClose}
                aria-label={labels.close}
              >
                <X aria-hidden="true" />
              </Button>
            </div>
          ) : null}

          <div
            role="tablist"
            aria-label={title}
            className="flex gap-1 bg-sidebar p-1 border border-sidebar-border rounded-xl"
          >
            {tabs.map((item) => {
              const active = tab === item.id
              return (
                <button
                  key={item.id}
                  type="button"
                  role="tab"
                  aria-selected={active}
                  className={cn(
                    "flex flex-1 justify-center items-center gap-1 px-3 rounded-lg min-w-0 h-8 font-medium text-sm transition-colors",
                    active
                      ? "bg-black/6 text-foreground dark:bg-white/10"
                      : "text-foreground/80 hover:bg-black/4 dark:hover:bg-white/5"
                  )}
                  onClick={() => {
                    setTab(item.id)
                    if (item.id !== "projects") setFocusedAgentId(null)
                    else if (activeAgentId) setFocusedAgentId(activeAgentId)
                  }}
                >
                  <FigmaIcon src={item.icon} />
                  <span className="truncate">{item.label}</span>
                </button>
              )
            })}
          </div>

          {tab === "history" ? (
            <SessionSearchInput
              placeholder={t("chat_search_placeholder")}
              onDebouncedChange={handleSessionQueryChange}
            />
          ) : null}
        </div>

        <div className="relative flex-1 mt-0 min-w-0 min-h-0">
          <ScrollArea className="min-w-0 h-full min-h-0 [&>[data-slot=scroll-area-viewport]>div]:block!">
            <div
              key={`${tab}-${focusedAgentId ?? ""}-${showProjectDetail ? projectSubTab : "root"}`}
              className="flex flex-col p-2 pb-20 min-w-0"
            >
              {tab === "history" ? (
                <div className="flex flex-col gap-0.5">
                  {pinnedChats.length === 0 && sessionGroups.length === 0 ? (
                    <p className="sidebar-stagger-item px-3 py-6 text-muted-foreground text-sm text-center">
                      {sessionQueryDebounced
                        ? t("chat_search_no_results")
                        : t("chat_history_empty")}
                    </p>
                  ) : (
                    (() => {
                      let stagger = 0
                      const next = () => stagger++
                      return (
                        <>
                          {pinnedChats.length > 0 ? (
                            <div className="flex flex-col gap-0.5">
                              <SessionDateHeading
                                label={`${t("chat_pinned")} (${pinnedChats.length})`}
                                stagger={next()}
                              />
                              {pinnedChats.map((chat) => renderChatRow(chat, next()))}
                            </div>
                          ) : null}
                          {sessionGroups.map((group) => (
                            <div
                              key={group.label}
                              className="flex flex-col gap-0.5 mt-2 first:mt-0"
                            >
                              <SessionDateHeading label={group.label} stagger={next()} />
                              {group.chats.map((chat) => renderChatRow(chat, next()))}
                            </div>
                          ))}
                        </>
                      )
                    })()
                  )}
                </div>
              ) : null}

              {tab === "projects" && !showProjectDetail ? (
                agents.length === 0 ? (
                  <div className="sidebar-stagger-item flex flex-col items-center gap-3 px-3 py-6">
                    <p className="text-muted-foreground text-sm text-center">
                      {t("project_empty_desc")}
                    </p>
                    <Button type="button" variant="outline" onClick={openCreateProject}>
                      <Plus aria-hidden="true" />
                      {t("project_new")}
                    </Button>
                  </div>
                ) : (
                  <div className="flex flex-col gap-0.5">
                    <DndContext
                      sensors={projectSensors}
                      collisionDetection={closestCenter}
                      onDragEnd={handleProjectDragEnd}
                    >
                      <SortableContext
                        items={agents.map((agent) => agent.id)}
                        strategy={verticalListSortingStrategy}
                      >
                        {agents.map((agent, agentIndex) => {
                          const isOwner = agent.is_owner || agent.role === "owner"
                          return (
                            <SortableProjectRow key={agent.id} id={agent.id}>
                              {({ setNodeRef, style, attributes, listeners }) => (
                                <div
                                  ref={setNodeRef}
                                  style={{ ...style, ...staggerStyle(agentIndex) }}
                                  className={cn(
                                    "sidebar-stagger-item group/project-item relative flex items-center gap-0.5 has-data-[state=open]:bg-accent hover:bg-accent rounded-lg w-full",
                                    activeAgentId === agent.id && "bg-accent"
                                  )}
                                  {...attributes}
                                >
                                  <button
                                    type="button"
                                    className="touch-none cursor-grab active:cursor-grabbing text-muted-foreground hover:text-foreground shrink-0 p-1.5 ml-1"
                                    aria-label={t("project_reorder")}
                                    {...listeners}
                                  >
                                    <GripVertical aria-hidden="true" className="size-3.5" />
                                  </button>
                                  <button
                                    type="button"
                                    className="flex flex-1 items-center gap-2 py-3 pr-3 min-w-0 text-left"
                                    onClick={() => openProject(agent)}
                                  >
                                    <span
                                      aria-hidden="true"
                                      className="size-4 figma-icon shrink-0"
                                      style={{ maskImage: "url('/icon-projects.svg')" }}
                                    />
                                    <span className="flex-1 min-w-0 text-sidebar-foreground text-sm truncate leading-5">
                                      {agent.name}
                                    </span>
                                  </button>
                                  {isOwner ? (
                                    <div className="top-1.5 right-1.5 absolute opacity-0 group-has-data-[state=open]/project-item:opacity-100 group-hover/project-item:opacity-100">
                                      <DropdownMenu>
                                        <DropdownMenuTrigger asChild>
                                          <Button
                                            type="button"
                                            variant="ghost"
                                            size="icon-sm"
                                            className="size-7"
                                            aria-label={t("project_actions_aria")}
                                          >
                                            <MoreVertical aria-hidden="true" className="size-4" />
                                          </Button>
                                        </DropdownMenuTrigger>
                                        <DropdownMenuContent align="end">
                                          <DropdownMenuItem
                                            onClick={() => {
                                              void openShare(agent)
                                            }}
                                          >
                                            {t("project_share")}
                                          </DropdownMenuItem>
                                          <DropdownMenuSeparator />
                                          <DropdownMenuItem
                                            variant="destructive"
                                            onClick={() => {
                                              setFocusedAgentId(agent.id)
                                              setProjectActionError(null)
                                              setDeleteProjectOpen(true)
                                            }}
                                          >
                                            {t("project_delete")}
                                          </DropdownMenuItem>
                                        </DropdownMenuContent>
                                      </DropdownMenu>
                                    </div>
                                  ) : null}
                                </div>
                              )}
                            </SortableProjectRow>
                          )
                        })}
                      </SortableContext>
                    </DndContext>
                    <Button
                      type="button"
                      variant="ghost"
                      className="justify-start"
                      onClick={openCreateProject}
                    >
                      <Plus aria-hidden="true" />
                      {t("project_new")}
                    </Button>
                  </div>
                )
              ) : null}

              {showProjectDetail && focusedAgent ? (
                <div className="flex flex-col gap-1.5 bg-card p-3 rounded-lg">
                  <div className="flex items-center gap-2.5 py-1">
                    <Button
                      type="button"
                      variant="secondary"
                      size="icon"
                      className="size-9 shrink-0"
                      aria-label={t("project_back_to_projects")}
                      onClick={() => setFocusedAgentId(null)}
                    >
                      <ArrowLeft aria-hidden="true" className="size-4" />
                    </Button>
                    <p className="flex-1 min-w-0 font-medium text-xl truncate leading-6">
                      {focusedAgent.name}
                    </p>
                    {focusedAgent.is_owner || focusedAgent.role === "owner" ? (
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon-sm"
                            className="size-8 shrink-0"
                            aria-label={t("project_actions_aria")}
                          >
                            <MoreVertical aria-hidden="true" className="size-4" />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuItem
                            onClick={() => {
                              void openShare(focusedAgent)
                            }}
                          >
                            {t("project_share")}
                          </DropdownMenuItem>
                          <DropdownMenuSeparator />
                          <DropdownMenuItem
                            variant="destructive"
                            onClick={() => {
                              setProjectActionError(null)
                              setDeleteProjectOpen(true)
                            }}
                          >
                            {t("project_delete")}
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    ) : null}
                  </div>

                  <div className="flex w-full">
                    {(
                      [
                        {
                          id: "chats" as const,
                          label: `${t("project_tab_chats")} ${projectChats.length}`,
                        },
                        {
                          id: "files" as const,
                          label: `${t("project_tab_files")} ${sources.length}`,
                        },
                        {
                          id: "instructions" as const,
                          label: t("project_tab_instructions"),
                        },
                      ] as const
                    ).map((item) => {
                      const active = projectSubTab === item.id
                      return (
                        <button
                          key={item.id}
                          type="button"
                          className={cn(
                            "flex flex-1 justify-center items-center px-3 border-b min-w-0 h-8 font-medium text-sm",
                            active
                              ? "border-b-2 border-foreground text-foreground"
                              : "border-border text-foreground/50"
                          )}
                          onClick={() => {
                            setProjectSubTab(item.id)
                            setUrlOpen(false)
                          }}
                        >
                          <span className="truncate">{item.label}</span>
                        </button>
                      )
                    })}
                  </div>

                  <div className="flex flex-col gap-4 pt-4">
                    {projectSubTab === "chats" ? (
                      <div className="flex flex-col gap-0.5">
                        {projectChats.length === 0 ? (
                          <p className="sidebar-stagger-item py-4 text-muted-foreground text-sm text-center">
                            {t("project_no_chats")}
                          </p>
                        ) : (
                          (() => {
                            let stagger = 0
                            const next = () => stagger++
                            return (
                          <>
                            {projectPinnedChats.length > 0 ? (
                              <div className="flex flex-col gap-0.5">
                                <SessionDateHeading
                                  label={`${t("chat_pinned")} (${projectPinnedChats.length})`}
                                  stagger={next()}
                                />
                                {projectPinnedChats.map((chat) => renderChatRow(chat, next()))}
                              </div>
                            ) : null}
                            {projectSessionGroups.map((group) => (
                              <div
                                key={group.label}
                                className="flex flex-col gap-0.5 mt-2 first:mt-0"
                              >
                                <SessionDateHeading label={group.label} stagger={next()} />
                                {group.chats.map((chat) => renderChatRow(chat, next()))}
                              </div>
                            ))}
                          </>
                            )
                          })()
                        )}
                      </div>
                    ) : null}

                    {projectSubTab === "files" ? (
                      <>
                        <div className="flex gap-2">
                          <Button
                            type="button"
                            variant="ghost"
                            className="flex-1 h-9"
                            disabled={sourceBusy}
                            onClick={() => fileInputRef.current?.click()}
                          >
                            <Upload aria-hidden="true" />
                            {t("project_upload_files")}
                          </Button>
                          <Button
                            type="button"
                            variant="ghost"
                            className="flex-1 h-9"
                            onClick={() => setUrlOpen((open) => !open)}
                          >
                            <Link2 aria-hidden="true" />
                            {t("project_add_url_action")}
                          </Button>
                          <input
                            ref={fileInputRef}
                            type="file"
                            multiple
                            className="hidden"
                            onChange={(event) => void handleUploadFiles(event.target.files)}
                          />
                        </div>

                        {urlOpen ? (
                          <div className="flex flex-col gap-2 bg-sidebar p-3 rounded-lg">
                            <p className="font-semibold text-muted-foreground text-xs">
                              {t("project_add_url_action")}
                            </p>
                            <Input
                              value={sourceUrl}
                              onChange={(event) => setSourceUrl(event.target.value)}
                              placeholder={t("project_url_placeholder")}
                              className="h-9"
                            />
                            <Input
                              value={sourceUrlTitle}
                              onChange={(event) => setSourceUrlTitle(event.target.value)}
                              placeholder={t("project_url_title_placeholder")}
                              className="h-9"
                            />
                            <Button
                              type="button"
                              variant="secondary"
                              className="w-full"
                              disabled={sourceBusy || !sourceUrl.trim()}
                              onClick={() => void handleAddUrl()}
                            >
                              {t("project_add_url_action")}
                            </Button>
                          </div>
                        ) : null}

                        {sourcesLoading ? (
                          <p className="text-muted-foreground text-sm">{t("project_loading_one")}</p>
                        ) : sources.length === 0 ? (
                          <p className="py-4 text-muted-foreground text-sm text-center">
                            {t("project_no_files")}
                          </p>
                        ) : (
                          <div className="flex flex-col gap-2">
                            {sources.map((source, sourceIndex) => (
                              <div
                                key={source.id}
                                className="sidebar-stagger-item flex flex-col gap-1 px-2 py-1 border border-border rounded-lg"
                                style={staggerStyle(sourceIndex)}
                              >
                                <div className="flex justify-between items-center gap-2">
                                  <p className="min-w-0 font-medium text-sm truncate">
                                    {source.title || source.file_name || source.url || "—"}
                                  </p>
                                  <div className="flex items-center gap-1 shrink-0">
                                    <span className="bg-secondary px-1.5 py-0.5 rounded-full font-medium text-xs">
                                      {sourceStatusLabel(source.status, t)}
                                    </span>
                                    <Button
                                      type="button"
                                      variant="ghost"
                                      size="icon-sm"
                                      className="size-7"
                                      aria-label={t("project_reindex_aria")}
                                      onClick={() => {
                                        void agentApi
                                          .reindexSource(focusedAgent.id, source.id)
                                          .then(() => loadSources(focusedAgent.id))
                                      }}
                                    >
                                      <RefreshCw aria-hidden="true" className="size-4" />
                                    </Button>
                                    <Button
                                      type="button"
                                      variant="ghost"
                                      size="icon-sm"
                                      className="size-7"
                                      disabled={deletingSourceId === source.id}
                                      aria-label={t("common_delete")}
                                      onClick={() => {
                                        void (async () => {
                                          setDeletingSourceId(source.id)
                                          try {
                                            await agentApi.removeSource(focusedAgent.id, source.id)
                                            await loadSources(focusedAgent.id)
                                          } finally {
                                            setDeletingSourceId(null)
                                          }
                                        })()
                                      }}
                                    >
                                      <Trash2 aria-hidden="true" className="size-4" />
                                    </Button>
                                  </div>
                                </div>
                                <p className="text-muted-foreground text-xs line-clamp-2">
                                  {source.summary || source.url || source.error_message || ""}
                                </p>
                              </div>
                            ))}
                          </div>
                        )}
                      </>
                    ) : null}

                    {projectSubTab === "instructions" ? (
                      <div className="flex flex-col gap-4">
                        <div className="flex flex-col gap-0">
                          <label className="px-1 py-1 font-semibold text-muted-foreground text-xs">
                            {t("prompt_name")}
                          </label>
                          <Input
                            ref={instrNameRef}
                            key={`name-${focusedAgent.id}`}
                            defaultValue={focusedAgent.name}
                            className="h-9"
                          />
                        </div>
                        <div className="flex flex-col gap-0">
                          <label className="px-1 py-1 font-semibold text-muted-foreground text-xs">
                            {t("project_description_label")}
                          </label>
                          <Input
                            ref={instrDescriptionRef}
                            key={`desc-${focusedAgent.id}`}
                            defaultValue={focusedAgent.description ?? ""}
                            placeholder={t("project_description_placeholder")}
                            className="h-9"
                          />
                        </div>
                        <div className="flex flex-col gap-0">
                          <label className="px-1 py-1 font-semibold text-muted-foreground text-xs">
                            {t("project_tab_instructions")}
                          </label>
                          <Textarea
                            ref={instrPromptRef}
                            key={`prompt-${focusedAgent.id}`}
                            defaultValue={focusedAgent.master_prompt ?? ""}
                            rows={4}
                            placeholder={t("project_instructions_placeholder")}
                          />
                        </div>
                        <Button
                          type="button"
                          variant="secondary"
                          className="w-fit"
                          disabled={updateAgentMutation.isPending}
                          onClick={() => void handleSaveInstructions()}
                        >
                          {t("project_save_changes")}
                        </Button>
                      </div>
                    ) : null}
                  </div>
                </div>
              ) : null}

              {tab === "prompts" ? (
                prompts.length === 0 ? (
                  <div className="sidebar-stagger-item flex flex-col items-center gap-3 px-3 py-6">
                    <p className="text-muted-foreground text-sm text-center">
                      {t("prompt_no_results")}
                    </p>
                    <Button type="button" variant="outline" onClick={openCreatePrompt}>
                      <Plus aria-hidden="true" />
                      {t("prompt_new")}
                    </Button>
                  </div>
                ) : (
                  <div className="flex flex-col gap-2">
                    {prompts.map((prompt, promptIndex) => (
                      <div
                        key={prompt.id}
                        className="sidebar-stagger-item group/prompt relative flex flex-col gap-1.5 bg-card p-3 rounded-lg"
                        style={staggerStyle(promptIndex)}
                      >
                        <p className="text-muted-foreground text-xs">{t("prompt_card_label")}</p>
                        <button
                          type="button"
                          className="flex items-center gap-2.5 w-full text-left"
                          onClick={() => {
                            if (onInsertPrompt) {
                              onInsertPrompt(prompt.body)
                              onRequestClose?.()
                              return
                            }
                            setEditingPrompt(prompt)
                            setPromptFormOpen(true)
                          }}
                        >
                          <Star
                            aria-hidden="true"
                            className="size-4.5 text-muted-foreground shrink-0"
                          />
                          <span className="flex-1 min-w-0 font-medium text-lg truncate leading-5">
                            {prompt.name}
                          </span>
                        </button>
                        <div className="top-2 right-2 absolute opacity-0 group-has-data-[state=open]/prompt:opacity-100 group-hover/prompt:opacity-100">
                          <DropdownMenu>
                            <DropdownMenuTrigger asChild>
                              <Button
                                type="button"
                                variant="ghost"
                                size="icon-sm"
                                className="size-7"
                                aria-label={t("prompt_library")}
                              >
                                <MoreVertical aria-hidden="true" className="size-4" />
                              </Button>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent align="end">
                              {onInsertPrompt ? (
                                <DropdownMenuItem
                                  onClick={() => {
                                    onInsertPrompt(prompt.body)
                                    onRequestClose?.()
                                  }}
                                >
                                  {t("prompt_insert")}
                                </DropdownMenuItem>
                              ) : null}
                              <DropdownMenuItem
                                onClick={() => {
                                  setEditingPrompt(prompt)
                                  setPromptFormOpen(true)
                                }}
                              >
                                {t("prompt_edit")}
                              </DropdownMenuItem>
                              <DropdownMenuSeparator />
                              <DropdownMenuItem
                                variant="destructive"
                                onClick={() => setDeletePromptTarget(prompt)}
                              >
                                {t("common_delete")}
                              </DropdownMenuItem>
                            </DropdownMenuContent>
                          </DropdownMenu>
                        </div>
                      </div>
                    ))}
                  </div>
                )
              ) : null}
            </div>
          </ScrollArea>

          <div className="bottom-0 z-10 absolute inset-x-0 flex justify-end bg-linear-to-t from-sidebar via-sidebar/90 to-transparent pt-6 pr-3 pb-3 pointer-events-none">
            <Button
              type="button"
              className="shadow-xs px-4 rounded-lg h-9 pointer-events-auto"
              onClick={() => {
                if (tab === "prompts") {
                  openCreatePrompt()
                  return
                }
                if (tab === "projects" && focusedAgentId) {
                  onNewChat({ agentId: focusedAgentId })
                  return
                }
                onNewChat()
              }}
            >
              <Plus aria-hidden="true" />
              {tab === "prompts" ? t("prompt_new") : labels.newChat}
            </Button>
          </div>
        </div>
      </div>

      {footer ? <div className="flex flex-col gap-0.5 px-2 pb-2 shrink-0">{footer}</div> : null}

      <Dialog
        open={createOpen}
        onOpenChange={(open) => {
          setCreateOpen(open)
          if (!open) {
            setCreateError(null)
            setHasNewName(false)
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("project_create_title")}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <Input
              key={String(createOpen)}
              ref={newNameRef}
              autoFocus
              placeholder={t("project_name_placeholder")}
              defaultValue=""
              onChange={(event) => {
                const next = event.target.value.trim().length > 0
                setHasNewName((prev) => (prev === next ? prev : next))
              }}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault()
                  void handleCreateProject()
                }
              }}
            />
            <Textarea
              key={`instr-${String(createOpen)}`}
              ref={newInstructionsRef}
              rows={4}
              placeholder={t("project_instructions_placeholder")}
              defaultValue=""
            />
            {createError ? <p className="text-destructive text-sm">{createError}</p> : null}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateOpen(false)}>
              {t("common_cancel")}
            </Button>
            <Button onClick={() => void handleCreateProject()} disabled={creating || !hasNewName}>
              {t("project_create_action")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={Boolean(renameChat)}
        onOpenChange={(open) => {
          if (!open) setRenameChat(null)
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("chat_rename_title")}</DialogTitle>
            <DialogDescription>{t("chat_rename_desc")}</DialogDescription>
          </DialogHeader>
          <Input
            value={renameValue}
            onChange={(event) => setRenameValue(event.target.value)}
            autoFocus
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault()
                void saveRename()
              }
            }}
          />
          <DialogFooter>
            <Button variant="outline" onClick={() => setRenameChat(null)}>
              {t("chat_cancel")}
            </Button>
            <Button
              onClick={() => void saveRename()}
              disabled={!renameValue.trim() || renameChatMutation.isPending}
            >
              {t("chat_rename_save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={Boolean(deleteConfirmChat)}
        onOpenChange={(open) => {
          if (!open) setDeleteConfirmChat(null)
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("chat_delete_confirm_title")}</DialogTitle>
            <DialogDescription>
              {t("chat_delete_confirm_desc", {
                title: deleteConfirmChat?.title || t("chat_untitled"),
              })}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteConfirmChat(null)}>
              {t("chat_cancel")}
            </Button>
            <Button
              variant="destructive"
              onClick={() => void confirmDelete()}
              disabled={deleteChatMutation.isPending}
            >
              {t("chat_delete")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <PromptFormDialog
        open={promptFormOpen}
        onOpenChange={(open) => {
          setPromptFormOpen(open)
          if (!open) setEditingPrompt(null)
        }}
        orgId={orgId}
        projects={agents}
        initial={
          editingPrompt
            ? {
                id: editingPrompt.id,
                name: editingPrompt.name,
                description: editingPrompt.description ?? "",
                body: editingPrompt.body,
                visibility: editingPrompt.visibility,
                team_ids: editingPrompt.team_ids,
                user_ids: editingPrompt.user_ids,
                users: editingPrompt.users,
                agent_id: editingPrompt.agent_id ?? null,
              }
            : {
                agent_id:
                  activeAgentId &&
                  agents.some(
                    (agent) =>
                      agent.id === activeAgentId &&
                      (agent.role === "owner" || agent.role === "editor")
                  )
                    ? activeAgentId
                    : null,
              }
        }
        title={editingPrompt ? t("prompt_edit") : t("prompt_create_title")}
        description={t("prompt_form_description")}
        contextAgentId={activeAgentId ?? null}
      />

      <Dialog
        open={Boolean(deletePromptTarget)}
        onOpenChange={(open) => {
          if (!open) setDeletePromptTarget(null)
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("prompt_delete_confirm_title")}</DialogTitle>
            <DialogDescription>
              {t("prompt_delete_confirm_desc", {
                name: deletePromptTarget?.name ?? "",
              })}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeletePromptTarget(null)}>
              {t("common_cancel")}
            </Button>
            <Button
              variant="destructive"
              onClick={() => void handleDeletePrompt()}
              disabled={deletingPrompt}
            >
              {t("common_delete")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={deleteProjectOpen}
        onOpenChange={(open) => {
          setDeleteProjectOpen(open)
          if (!open) setProjectActionError(null)
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("project_delete_title")}</DialogTitle>
          </DialogHeader>
          <p className="text-muted-foreground text-sm">{t("project_delete_desc")}</p>
          {projectActionError ? (
            <p className="text-destructive text-sm">{projectActionError}</p>
          ) : null}
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteProjectOpen(false)}>
              {t("common_cancel")}
            </Button>
            <Button
              variant="destructive"
              onClick={() => void handleDeleteProject()}
              disabled={deletingProject}
            >
              {t("common_delete")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={shareOpen}
        onOpenChange={(open) => {
          setShareOpen(open)
          if (!open) {
            setProjectActionError(null)
            setShareQuery("")
            setShareSelected(null)
            setSuggestionsOpen(false)
          }
        }}
      >
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
                  <div className="z-50 absolute bg-popover shadow-md mt-1 p-1 border rounded-md w-full max-h-56 overflow-y-auto">
                    {shareSuggestions.map((suggestion) => {
                      const key =
                        suggestion.kind === "org"
                          ? "org"
                          : suggestion.kind === "team"
                            ? `team-${suggestion.team_id}`
                            : `user-${suggestion.user_id}`
                      const title =
                        suggestion.kind === "org"
                          ? t("project_share_org")
                          : suggestion.kind === "team"
                            ? (suggestion.team_name ?? t("project_share_team_hint"))
                            : suggestion.display_name || suggestion.email || ""
                      const subtitle =
                        suggestion.kind === "org"
                          ? t("project_share_org_hint")
                          : suggestion.kind === "team"
                            ? t("project_share_team_hint")
                            : suggestion.display_name
                              ? suggestion.email
                              : null
                      return (
                        <button
                          key={key}
                          type="button"
                          className="flex flex-col items-start hover:bg-accent px-2 py-1.5 rounded-sm w-full text-sm text-left"
                          onMouseDown={(event) => {
                            event.preventDefault()
                            setShareSelected(suggestion)
                            setShareQuery(title)
                            setSuggestionsOpen(false)
                          }}
                        >
                          <span className="truncate">{title}</span>
                          {subtitle ? (
                            <span className="text-muted-foreground text-xs truncate">
                              {subtitle}
                            </span>
                          ) : null}
                        </button>
                      )
                    })}
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
              <Button onClick={() => void handleAddShare()} disabled={shareBusy || !shareSelected}>
                {t("project_share_add")}
              </Button>
            </div>
            <p className="text-muted-foreground text-xs">{t("project_share_org_only_hint")}</p>
            {projectActionError ? (
              <p className="text-destructive text-sm">{projectActionError}</p>
            ) : null}
            <div className="space-y-2">
              {shares.length === 0 ? (
                <p className="text-muted-foreground text-sm">{t("project_share_empty")}</p>
              ) : (
                shares.map((share) => {
                  const key =
                    share.kind === "org"
                      ? "org"
                      : share.kind === "team"
                        ? `team-${share.team_id}`
                        : `user-${share.user_id}`
                  const title =
                    share.kind === "org"
                      ? t("project_share_org")
                      : share.kind === "team"
                        ? (share.team_name ?? t("project_share_team_hint"))
                        : share.display_name || share.email || ""
                  const subtitle =
                    share.kind === "org"
                      ? t("project_share_org_hint")
                      : share.kind === "team"
                        ? t("project_share_team_hint")
                        : share.display_name
                          ? share.email
                          : null
                  const roleLabel =
                    share.role === "owner"
                      ? t("project_role_owner")
                      : share.role === "editor"
                        ? t("project_role_editor")
                        : t("project_role_viewer")
                  return (
                    <div
                      key={key}
                      className="flex justify-between items-center gap-2 px-3 py-2 border rounded-md"
                    >
                      <div className="min-w-0">
                        <p className="text-sm truncate">{title}</p>
                        <p className="text-muted-foreground text-xs">
                          {subtitle ? `${subtitle} · ` : ""}
                          {roleLabel}
                        </p>
                      </div>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="w-8 h-8 text-destructive"
                        aria-label={t("project_remove_access_aria")}
                        disabled={shareBusy}
                        onClick={() => void handleRemoveShare(share)}
                      >
                        <Trash2 className="w-4 h-4" aria-hidden="true" />
                      </Button>
                    </div>
                  )
                })
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
    </nav>
  )
}

export default ChatSidebar
