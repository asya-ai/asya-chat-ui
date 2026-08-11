import { useEffect, useMemo, useState, type ReactNode } from "react"
import { useNavigate, useParams } from "react-router"
import { ChevronDown, ChevronRight, MoreVertical, Pin, Plus, Search, X } from "lucide-react"

import { agentApi } from "@/lib/api"
import { useI18n } from "@/lib/i18n-context"
import { orgStore, sidebarSectionsStore } from "@/lib/storage"
import type { Agent, Chat } from "@/lib/types"
import {
  useChats,
  useChatSearch,
  useDeleteChat,
  useOrgsMine,
  usePinChat,
  useRenameChat,
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
import { Textarea } from "@/components/ui/textarea"
import { cn } from "@/lib/utils"

type ChatSidebarProps = {
  title: string
  labels: {
    newChat: string
    history: string
    projects: string
    close?: string
  }
  activeSection?: "history" | "projects" | null
  activeChatId?: string | null
  activeAgentId?: string | null
  onNewChat: () => void
  onOpenHistory: () => void
  onOpenProjects: () => void
  onToggleShareChat?: (chat: Chat) => void
  footer?: ReactNode
  onRequestClose?: () => void
}

const parseChatDate = (value: string) => {
  const hasTimezone = /[zZ]|[+-]\d{2}:?\d{2}$/.test(value)
  return new Date(hasTimezone ? value : `${value}Z`)
}

export const ChatSidebar = ({
  title,
  labels,
  activeSection,
  activeChatId = null,
  activeAgentId = null,
  onNewChat,
  onOpenHistory,
  onOpenProjects,
  onToggleShareChat,
  footer,
  onRequestClose,
}: ChatSidebarProps) => {
  const navigate = useNavigate()
  const { chatId: routeChatId } = useParams()
  const { locale, t } = useI18n()
  const { data: orgs = [] } = useOrgsMine()
  const orgId = orgStore.get() ?? orgs[0]?.id ?? null
  const { data: chats = [] } = useChats(orgId)
  const deleteChatMutation = useDeleteChat(orgId)
  const renameChatMutation = useRenameChat(orgId)
  const pinChatMutation = usePinChat(orgId)
  const [agents, setAgents] = useState<Agent[]>([])
  const [sectionsOpen, setSectionsOpen] = useState(() => sidebarSectionsStore.get())
  const [sessionQuery, setSessionQuery] = useState("")
  const [sessionQueryDebounced, setSessionQueryDebounced] = useState("")
  const [renameChat, setRenameChat] = useState<Chat | null>(null)
  const [renameValue, setRenameValue] = useState("")
  const [deleteConfirmChat, setDeleteConfirmChat] = useState<Chat | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState("")
  const [newInstructions, setNewInstructions] = useState("")
  const [createError, setCreateError] = useState<string | null>(null)
  const { data: searchedChats = [] } = useChatSearch(orgId, sessionQueryDebounced)
  const currentChatId = activeChatId ?? routeChatId ?? null

  useEffect(() => {
    const timerId = window.setTimeout(() => {
      setSessionQueryDebounced(sessionQuery.trim())
    }, 250)
    return () => window.clearTimeout(timerId)
  }, [sessionQuery])

  useEffect(() => {
    let cancelled = false
    agentApi
      .list()
      .then((items) => {
        if (!cancelled) setAgents(items)
      })
      .catch(() => {
        if (!cancelled) setAgents([])
      })
    return () => {
      cancelled = true
    }
  }, [orgId])

  const setSectionOpen = (key: keyof typeof sectionsOpen, open: boolean) => {
    setSectionsOpen((prev) => {
      const next = { ...prev, [key]: open }
      sidebarSectionsStore.set(next)
      return next
    })
  }

  const { pinnedChats, sessionGroups } = useMemo(() => {
    const currentOrgChatIds = new Set(chats.map((chat) => chat.id))
    const sourceChats = sessionQueryDebounced
      ? searchedChats.filter((chat) => currentOrgChatIds.has(chat.id))
      : chats
    const sorted = [...sourceChats].sort(
      (a, b) =>
        parseChatDate(b.last_activity_at || b.created_at).getTime() -
        parseChatDate(a.last_activity_at || a.created_at).getTime()
    )
    const pinned = sorted.filter((chat) => chat.is_pinned)
    const unpinned = sorted.filter((chat) => !chat.is_pinned)
    const startOfToday = new Date()
    startOfToday.setHours(0, 0, 0, 0)
    const startOfYesterday = new Date(startOfToday)
    startOfYesterday.setDate(startOfYesterday.getDate() - 1)

    const buckets = new Map<string, Chat[]>()
    const order: string[] = []

    for (const chat of unpinned) {
      const date = parseChatDate(chat.last_activity_at || chat.created_at)
      const day = new Date(date)
      day.setHours(0, 0, 0, 0)
      let label: string
      if (day.getTime() === startOfToday.getTime()) {
        label = t("chat_group_today")
      } else if (day.getTime() === startOfYesterday.getTime()) {
        label = t("chat_group_yesterday")
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

    return {
      pinnedChats: pinned,
      sessionGroups: order.map((label) => ({
        label,
        chats: buckets.get(label) ?? [],
      })),
    }
  }, [chats, locale, searchedChats, sessionQueryDebounced, t])

  const togglePin = (chat: Chat) => {
    const nextPinned = !chat.is_pinned
    if (nextPinned && !sectionsOpen.pinned) {
      setSectionOpen("pinned", true)
    }
    void pinChatMutation.mutateAsync({
      chatId: chat.id,
      is_pinned: nextPinned,
    })
  }

  const selectChat = (chat: Chat) => {
    onRequestClose?.()
    navigate(
      chat.agent_id
        ? `/chat/${chat.id}?agent=${encodeURIComponent(chat.agent_id)}`
        : `/chat/${chat.id}`
    )
  }

  const selectAgent = (agent: Agent) => {
    onRequestClose?.()
    navigate(`/projects/${encodeURIComponent(agent.id)}`)
  }

  const openCreateSpace = () => {
    setCreateError(null)
    setCreateOpen(true)
    if (!sectionsOpen.spaces) setSectionOpen("spaces", true)
  }

  const handleCreateSpace = async () => {
    const name = newName.trim()
    if (!name) {
      setCreateError(t("project_name_required"))
      return
    }
    try {
      setCreating(true)
      setCreateError(null)
      const created = await agentApi.create({
        name,
        master_prompt: newInstructions.trim() || null,
      })
      setAgents((prev) => [created, ...prev])
      setCreateOpen(false)
      setNewName("")
      setNewInstructions("")
      onRequestClose?.()
      navigate(`/projects/${created.id}`)
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
    const title = renameValue.trim()
    if (!title) return
    await renameChatMutation.mutateAsync({ chatId: renameChat.id, title })
    setRenameChat(null)
  }

  const confirmDelete = async () => {
    if (!deleteConfirmChat) return
    const chatIdToDelete = deleteConfirmChat.id
    await deleteChatMutation.mutateAsync(chatIdToDelete)
    setDeleteConfirmChat(null)
    if (currentChatId === chatIdToDelete) {
      navigate("/chat", { replace: true })
    }
  }

  return (
    <nav aria-label={title} className="flex h-full min-h-0 min-w-0 flex-col">
      <div className="flex h-15 shrink-0 items-center justify-between gap-2 px-1.5 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className="flex size-11 shrink-0 items-center justify-center">
            <img
              src="/favicon.svg"
              alt="Eldigen"
              className="h-11 w-[39.286px] object-contain"
            />
          </span>
          <p className="truncate font-heading text-4xl leading-9">{title}</p>
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

      <div className="mt-3 flex min-h-0 min-w-0 flex-1 flex-col gap-3">
        <Button className="w-full shrink-0" onClick={onNewChat}>
          <Plus aria-hidden="true" />
          {labels.newChat}
        </Button>

        <div className="h-0 shrink-0 border-t border-border" />

        {/* Radix wraps viewport content in a display:table div, which grows to
            max-content and drags every w-full row wide on long chat titles. */}
        <ScrollArea className="min-h-0 min-w-0 flex-1 [&>[data-slot=scroll-area-viewport]>div]:block!">
          <div className="flex min-w-0 flex-col gap-0.5 px-1 pb-2">
            {pinnedChats.length > 0 ? (
              <div>
                <div className="flex h-9 w-full min-w-0 items-center">
                  <Button
                    type="button"
                    variant="ghost"
                    className="h-9 min-w-0 flex-1 justify-start gap-0.5 p-0"
                    aria-expanded={sectionsOpen.pinned}
                    onClick={() => setSectionOpen("pinned", !sectionsOpen.pinned)}
                  >
                    <span className="flex size-9 shrink-0 items-center justify-center">
                      <Pin aria-hidden="true" className="size-5 text-muted-foreground" />
                    </span>
                    <span className="min-w-0 flex-1 truncate text-left text-sm font-semibold">
                      {t("chat_pinned")}
                    </span>
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    className="mr-2 shrink-0 text-muted-foreground"
                    aria-label={
                      sectionsOpen.pinned
                        ? t("chat_collapse_pinned")
                        : t("chat_expand_pinned")
                    }
                    onClick={() => setSectionOpen("pinned", !sectionsOpen.pinned)}
                  >
                    {sectionsOpen.pinned ? (
                      <ChevronDown aria-hidden="true" className="size-4" />
                    ) : (
                      <ChevronRight aria-hidden="true" className="size-4" />
                    )}
                  </Button>
                </div>
                {sectionsOpen.pinned ? (
                  <div className="mt-0.5 flex flex-col gap-0.5">
                    {pinnedChats.map((chat) => (
                      <ContextMenu key={chat.id}>
                        <ContextMenuTrigger asChild>
                          <div
                            className={cn(
                              "group/chat-item relative flex h-8 w-full min-w-0 items-center rounded-md",
                              currentChatId === chat.id && "bg-sidebar-accent"
                            )}
                          >
                            <Button
                              type="button"
                              variant="ghost"
                              className="h-8 w-full min-w-0 justify-start px-3 text-left text-sm font-normal"
                              onClick={() => selectChat(chat)}
                            >
                              <span className="min-w-0 flex-1 truncate">
                                {chat.title || t("chat_untitled")}
                              </span>
                            </Button>
                            <DropdownMenu>
                              <DropdownMenuTrigger asChild>
                                <Button
                                  type="button"
                                  variant="ghost"
                                  className="absolute right-1 hidden size-6 rounded-md bg-sidebar-accent p-0 group-hover/chat-item:flex data-[state=open]:flex"
                                  onClick={(e) => e.stopPropagation()}
                                >
                                  <MoreVertical className="size-4" />
                                </Button>
                              </DropdownMenuTrigger>
                              <DropdownMenuContent align="start" side="right">
                                <DropdownMenuItem onClick={() => togglePin(chat)}>
                                  {t("chat_unpin")}
                                </DropdownMenuItem>
                                <DropdownMenuItem onClick={() => openRename(chat)}>
                                  {t("chat_rename")}
                                </DropdownMenuItem>
                                <DropdownMenuItem
                                  onClick={() => {
                                    if (onToggleShareChat) onToggleShareChat(chat)
                                  }}
                                >
                                  {chat.is_shared ? t("chat_unshare") : t("chat_share")}
                                </DropdownMenuItem>
                                <DropdownMenuSeparator />
                                <DropdownMenuItem
                                  variant="destructive"
                                  onClick={() => setDeleteConfirmChat(chat)}
                                >
                                  {t("chat_delete")}
                                </DropdownMenuItem>
                              </DropdownMenuContent>
                            </DropdownMenu>
                          </div>
                        </ContextMenuTrigger>
                        <ContextMenuContent>
                          <ContextMenuItem onClick={() => togglePin(chat)}>
                            {t("chat_unpin")}
                          </ContextMenuItem>
                          <ContextMenuItem onClick={() => openRename(chat)}>
                            {t("chat_rename")}
                          </ContextMenuItem>
                          <ContextMenuItem
                            onClick={() => {
                              if (onToggleShareChat) onToggleShareChat(chat)
                            }}
                          >
                            {chat.is_shared ? t("chat_unshare") : t("chat_share")}
                          </ContextMenuItem>
                          <ContextMenuSeparator />
                          <ContextMenuItem
                            variant="destructive"
                            onClick={() => setDeleteConfirmChat(chat)}
                          >
                            {t("chat_delete")}
                          </ContextMenuItem>
                        </ContextMenuContent>
                      </ContextMenu>
                    ))}
                  </div>
                ) : null}
              </div>
            ) : null}

            <div>
              <div className="flex h-9 w-full min-w-0 items-center">
                <Button
                  type="button"
                  variant="ghost"
                  className="h-9 min-w-0 flex-1 justify-start gap-0.5 p-0 data-[active=true]:bg-sidebar-accent"
                  data-active={activeSection === "projects"}
                  aria-expanded={sectionsOpen.spaces}
                  onClick={() => setSectionOpen("spaces", !sectionsOpen.spaces)}
                  onDoubleClick={(event) => {
                    event.preventDefault()
                    onOpenProjects()
                  }}
                >
                  <span className="flex size-9 shrink-0 items-center justify-center">
                    <span
                      aria-hidden="true"
                      className="figma-icon size-5"
                      style={{ maskImage: "url('/icon-spaces.svg')" }}
                    />
                  </span>
                  <span className="min-w-0 flex-1 truncate text-left text-sm font-semibold">
                    {labels.projects}
                  </span>
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  className="shrink-0 text-muted-foreground"
                  aria-label={t("project_new")}
                  onClick={openCreateSpace}
                >
                  <Plus aria-hidden="true" className="size-4" />
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  className="mr-2 shrink-0 text-muted-foreground"
                  aria-label={
                    sectionsOpen.spaces
                      ? t("chat_collapse_spaces")
                      : t("chat_expand_spaces")
                  }
                  onClick={() => setSectionOpen("spaces", !sectionsOpen.spaces)}
                >
                  {sectionsOpen.spaces ? (
                    <ChevronDown aria-hidden="true" className="size-4" />
                  ) : (
                    <ChevronRight aria-hidden="true" className="size-4" />
                  )}
                </Button>
              </div>
              {sectionsOpen.spaces ? (
                <div className="mt-0.5 flex flex-col gap-0.5">
                  {agents.map((agent) => (
                    <Button
                      key={agent.id}
                      type="button"
                      variant="ghost"
                      className={cn(
                        "h-8 w-full min-w-0 justify-start px-3 text-left text-sm font-normal",
                        activeAgentId === agent.id && "bg-sidebar-accent"
                      )}
                      onClick={() => selectAgent(agent)}
                    >
                      <span className="min-w-0 flex-1 truncate">{agent.name}</span>
                    </Button>
                  ))}
                </div>
              ) : null}
            </div>

            <div>
              <div className="flex h-9 w-full min-w-0 items-center">
                <Button
                  type="button"
                  variant="ghost"
                  className="h-9 min-w-0 flex-1 justify-start gap-0.5 p-0 data-[active=true]:bg-sidebar-accent"
                  data-active={activeSection === "history"}
                  aria-expanded={sectionsOpen.sessions}
                  onClick={() => setSectionOpen("sessions", !sectionsOpen.sessions)}
                  onDoubleClick={(event) => {
                    event.preventDefault()
                    onOpenHistory()
                  }}
                >
                  <span className="flex size-9 shrink-0 items-center justify-center">
                    <span
                      aria-hidden="true"
                      className="figma-icon size-5"
                      style={{ maskImage: "url('/icon-history.svg')" }}
                    />
                  </span>
                  <span className="min-w-0 flex-1 truncate text-left text-sm font-semibold">
                    {labels.history}
                  </span>
                </Button>
                <span className="size-7 shrink-0" aria-hidden="true" />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  className="mr-2 shrink-0 text-muted-foreground"
                  aria-label={
                    sectionsOpen.sessions
                      ? t("chat_collapse_sessions")
                      : t("chat_expand_sessions")
                  }
                  onClick={() => setSectionOpen("sessions", !sectionsOpen.sessions)}
                >
                  {sectionsOpen.sessions ? (
                    <ChevronDown aria-hidden="true" className="size-4" />
                  ) : (
                    <ChevronRight aria-hidden="true" className="size-4" />
                  )}
                </Button>
              </div>
              {sectionsOpen.sessions ? (
                <div className="mt-0.5 flex flex-col gap-2">
                  <div className="relative px-1.5">
                    <Search
                      aria-hidden="true"
                      className="pointer-events-none absolute left-4 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground"
                    />
                    <Input
                      value={sessionQuery}
                      onChange={(event) => setSessionQuery(event.target.value)}
                      placeholder={t("chat_search_placeholder")}
                      aria-label={t("chat_search_placeholder")}
                      className="h-8 bg-background pl-8 text-sm"
                      onClick={(event) => event.stopPropagation()}
                    />
                  </div>
                  {sessionGroups.length === 0 ? (
                    <p className="px-3 py-2 text-xs text-muted-foreground">
                      {sessionQueryDebounced
                        ? t("chat_search_no_results")
                        : t("chat_history_empty")}
                    </p>
                  ) : (
                    sessionGroups.map((group) => (
                      <div key={group.label} className="flex flex-col gap-0.5">
                        <div className="mx-3 mt-1 border-t border-border pt-2">
                          <p className="text-xs font-medium text-muted-foreground">
                            {group.label}
                          </p>
                        </div>
                        {group.chats.map((chat) => (
                          <ContextMenu key={chat.id}>
                            <ContextMenuTrigger asChild>
                              <div
                                className={cn(
                                  "group/chat-item relative flex h-8 w-full min-w-0 items-center rounded-md",
                                  currentChatId === chat.id && "bg-sidebar-accent"
                                )}
                              >
                                <Button
                                  type="button"
                                  variant="ghost"
                                  className="h-8 w-full min-w-0 justify-start px-3 text-left text-sm font-normal"
                                  onClick={() => selectChat(chat)}
                                >
                                  <span className="min-w-0 flex-1 truncate">
                                    {chat.title || t("chat_untitled")}
                                  </span>
                                </Button>
                                <DropdownMenu>
                                  <DropdownMenuTrigger asChild>
                                <Button
                                  type="button"
                                  variant="ghost"
                                  className="absolute right-1 hidden size-6 rounded-md bg-sidebar-accent p-0 group-hover/chat-item:flex data-[state=open]:flex"
                                  onClick={(e) => e.stopPropagation()}
                                >
                                  <MoreVertical className="size-4" />
                                </Button>
                              </DropdownMenuTrigger>
                              <DropdownMenuContent align="start" side="right">
                                <DropdownMenuItem onClick={() => togglePin(chat)}>
                                  {t("chat_pin")}
                                </DropdownMenuItem>
                                <DropdownMenuItem onClick={() => openRename(chat)}>
                                  {t("chat_rename")}
                                </DropdownMenuItem>
                                <DropdownMenuItem
                                  onClick={() => {
                                    if (onToggleShareChat) onToggleShareChat(chat)
                                  }}
                                >
                                  {chat.is_shared ? t("chat_unshare") : t("chat_share")}
                                </DropdownMenuItem>
                                <DropdownMenuSeparator />
                                <DropdownMenuItem
                                  variant="destructive"
                                  onClick={() => setDeleteConfirmChat(chat)}
                                >
                                  {t("chat_delete")}
                                </DropdownMenuItem>
                              </DropdownMenuContent>
                                </DropdownMenu>
                              </div>
                            </ContextMenuTrigger>
                            <ContextMenuContent>
                              <ContextMenuItem onClick={() => togglePin(chat)}>
                                {t("chat_pin")}
                              </ContextMenuItem>
                              <ContextMenuItem onClick={() => openRename(chat)}>
                                {t("chat_rename")}
                              </ContextMenuItem>
                              <ContextMenuItem
                                onClick={() => {
                                  if (onToggleShareChat) onToggleShareChat(chat)
                                }}
                              >
                                {chat.is_shared ? t("chat_unshare") : t("chat_share")}
                              </ContextMenuItem>
                              <ContextMenuSeparator />
                              <ContextMenuItem
                                variant="destructive"
                                onClick={() => setDeleteConfirmChat(chat)}
                              >
                                {t("chat_delete")}
                              </ContextMenuItem>
                            </ContextMenuContent>
                          </ContextMenu>
                        ))}
                      </div>
                    ))
                  )}
                </div>
              ) : null}
            </div>
          </div>
        </ScrollArea>
      </div>

      {footer ? <div className="flex shrink-0 flex-col gap-0.5">{footer}</div> : null}

      <Dialog
        open={createOpen}
        onOpenChange={(open) => {
          setCreateOpen(open)
          if (!open) {
            setCreateError(null)
            setNewName("")
            setNewInstructions("")
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("project_create_title")}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <Input
              autoFocus
              placeholder={t("project_name_placeholder")}
              value={newName}
              onChange={(event) => setNewName(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault()
                  void handleCreateSpace()
                }
              }}
            />
            <Textarea
              rows={4}
              placeholder={t("project_instructions_placeholder")}
              value={newInstructions}
              onChange={(event) => setNewInstructions(event.target.value)}
            />
            {createError ? (
              <p className="text-destructive text-sm">{createError}</p>
            ) : null}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateOpen(false)}>
              {t("common_cancel")}
            </Button>
            <Button
              onClick={() => void handleCreateSpace()}
              disabled={creating || !newName.trim()}
            >
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
    </nav>
  )
}

export default ChatSidebar
