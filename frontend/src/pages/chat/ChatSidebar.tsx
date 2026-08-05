import { useEffect, useMemo, useState, type ReactNode } from "react"
import { useNavigate, useParams } from "react-router"
import { useQueryClient } from "@tanstack/react-query"
import { ChevronDown, ChevronRight, Plus, Search, X } from "lucide-react"

import { agentApi, chatApi } from "@/lib/api"
import { useI18n } from "@/lib/i18n-context"
import { orgStore, sidebarSectionsStore } from "@/lib/storage"
import type { Agent, Chat } from "@/lib/types"
import {
  useChats,
  useChatSearch,
  useDeleteChat,
  useOrgsMine,
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
import { Input } from "@/components/ui/input"
import { ScrollArea } from "@/components/ui/scroll-area"
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
  footer,
  onRequestClose,
}: ChatSidebarProps) => {
  const navigate = useNavigate()
  const { chatId: routeChatId } = useParams()
  const queryClient = useQueryClient()
  const { locale, t } = useI18n()
  const { data: orgs = [] } = useOrgsMine()
  const orgId = orgStore.get() ?? orgs[0]?.id ?? null
  const { data: chats = [] } = useChats(orgId)
  const deleteChatMutation = useDeleteChat(orgId)
  const renameChatMutation = useRenameChat(orgId)
  const [agents, setAgents] = useState<Agent[]>([])
  const [sectionsOpen, setSectionsOpen] = useState(() => sidebarSectionsStore.get())
  const [sessionQuery, setSessionQuery] = useState("")
  const [sessionQueryDebounced, setSessionQueryDebounced] = useState("")
  const [renameChat, setRenameChat] = useState<Chat | null>(null)
  const [renameValue, setRenameValue] = useState("")
  const [deleteConfirmChat, setDeleteConfirmChat] = useState<Chat | null>(null)
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

  const sessionGroups = useMemo(() => {
    const currentOrgChatIds = new Set(chats.map((chat) => chat.id))
    const sourceChats = sessionQueryDebounced
      ? searchedChats.filter((chat) => currentOrgChatIds.has(chat.id))
      : chats
    const sorted = [...sourceChats].sort(
      (a, b) =>
        parseChatDate(b.last_activity_at || b.created_at).getTime() -
        parseChatDate(a.last_activity_at || a.created_at).getTime()
    )
    const startOfToday = new Date()
    startOfToday.setHours(0, 0, 0, 0)
    const startOfYesterday = new Date(startOfToday)
    startOfYesterday.setDate(startOfYesterday.getDate() - 1)

    const buckets = new Map<string, Chat[]>()
    const order: string[] = []

    for (const chat of sorted) {
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

    return order.map((label) => ({
      label,
      chats: buckets.get(label) ?? [],
    }))
  }, [chats, locale, searchedChats, sessionQueryDebounced, t])

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

  const toggleShare = async (chat: Chat) => {
    if (chat.is_shared) {
      await chatApi.unshare(chat.id)
      if (orgId) {
        queryClient.setQueryData<Chat[]>(["chats", orgId], (prev) =>
          prev
            ? prev.map((item) =>
                item.id === chat.id ? { ...item, is_shared: false } : item
              )
            : prev
        )
      }
      return
    }
    await chatApi.share(chat.id)
    if (orgId) {
      queryClient.setQueryData<Chat[]>(["chats", orgId], (prev) =>
        prev
          ? prev.map((item) =>
              item.id === chat.id ? { ...item, is_shared: true } : item
            )
          : prev
      )
    }
  }

  return (
    <nav aria-label={title} className="flex h-full min-h-0 flex-col">
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

      <div className="mt-3 flex min-h-0 flex-1 flex-col gap-3">
        <Button className="w-full shrink-0" onClick={onNewChat}>
          <Plus aria-hidden="true" />
          {labels.newChat}
        </Button>

        <div className="h-0 shrink-0 border-t border-border" />

        <ScrollArea className="min-h-0 flex-1">
          <div className="flex flex-col gap-0.5 pb-2">
            <div>
              <Button
                type="button"
                variant="ghost"
                className="h-9 w-full justify-start gap-0.5 p-0 data-[active=true]:bg-sidebar-accent"
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
                {sectionsOpen.spaces ? (
                  <ChevronDown
                    aria-hidden="true"
                    className="mr-2 size-4 shrink-0 text-muted-foreground"
                  />
                ) : (
                  <ChevronRight
                    aria-hidden="true"
                    className="mr-2 size-4 shrink-0 text-muted-foreground"
                  />
                )}
              </Button>
              {sectionsOpen.spaces ? (
                <div className="mt-0.5 flex flex-col gap-0.5">
                  {agents.length === 0 ? (
                    <p className="px-3 py-2 text-xs text-muted-foreground">
                      {t("project_empty_title")}
                    </p>
                  ) : (
                    agents.map((agent) => (
                      <Button
                        key={agent.id}
                        type="button"
                        variant="ghost"
                        className={cn(
                          "h-8 w-full justify-start px-3 text-left text-sm font-normal",
                          activeAgentId === agent.id && "bg-sidebar-accent"
                        )}
                        onClick={() => selectAgent(agent)}
                      >
                        <span className="truncate">{agent.name}</span>
                      </Button>
                    ))
                  )}
                </div>
              ) : null}
            </div>

            <div>
              <Button
                type="button"
                variant="ghost"
                className="h-9 w-full justify-start gap-0.5 p-0 data-[active=true]:bg-sidebar-accent"
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
                {sectionsOpen.sessions ? (
                  <ChevronDown
                    aria-hidden="true"
                    className="mr-2 size-4 shrink-0 text-muted-foreground"
                  />
                ) : (
                  <ChevronRight
                    aria-hidden="true"
                    className="mr-2 size-4 shrink-0 text-muted-foreground"
                  />
                )}
              </Button>
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
                        <p className="px-3 py-1 text-xs font-medium text-muted-foreground">
                          {group.label}
                        </p>
                        {group.chats.map((chat) => (
                          <ContextMenu key={chat.id}>
                            <ContextMenuTrigger asChild>
                              <Button
                                type="button"
                                variant="ghost"
                                className={cn(
                                  "h-8 w-full justify-start px-3 text-left text-sm font-normal",
                                  currentChatId === chat.id && "bg-sidebar-accent"
                                )}
                                onClick={() => selectChat(chat)}
                              >
                                <span className="truncate">
                                  {chat.title || t("chat_untitled")}
                                </span>
                              </Button>
                            </ContextMenuTrigger>
                            <ContextMenuContent>
                              <ContextMenuItem onClick={() => openRename(chat)}>
                                {t("chat_rename")}
                              </ContextMenuItem>
                              <ContextMenuItem onClick={() => void toggleShare(chat)}>
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
