import { useState } from "react"
import type { ReactNode } from "react"

import type { Agent, Chat } from "@/lib/types"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { ChevronDown, ChevronRight, FolderOpen, MoreHorizontal, Plus, X } from "lucide-react"

type ChatGroup = { label: string; items: Chat[] }

type ChatSidebarProps = {
  title: string
  labels: {
    newChat: string
    projects: string
    untitled: string
    settings: string
    delete: string
    share: string
    unshare: string
    searchPlaceholder: string
    noResults: string
    close?: string
    expandProjects: string
    collapseProjects: string
  }
  groups: ChatGroup[]
  projects: Agent[]
  searchQuery: string
  activeChatId?: string | null
  activeProjectId?: string | null
  onSearchChange: (value: string) => void
  onNewChat: () => void
  onSelectChat: (chat: Chat) => void
  onOpenProjects: () => void
  onSelectProject: (project: Agent) => void
  onDeleteChat: (chat: Chat) => void
  onToggleShareChat: (chat: Chat) => void
  onOpenSettings: () => void
  formatRelativeAge: (dateString: string) => string
  getChatActivityDate: (chat: Chat) => string
  footer?: ReactNode
  onRequestClose?: () => void
}

export const ChatSidebar = ({
  title,
  labels,
  groups,
  projects,
  searchQuery,
  activeChatId,
  activeProjectId,
  onSearchChange,
  onNewChat,
  onSelectChat,
  onOpenProjects,
  onSelectProject,
  onDeleteChat,
  onToggleShareChat,
  onOpenSettings,
  formatRelativeAge,
  getChatActivityDate,
  footer,
  onRequestClose,
}: ChatSidebarProps) => {
  const [projectsCollapsed, setProjectsCollapsed] = useState(false)
  const showProjectPanel = projects.length > 0 && !projectsCollapsed

  return (
    <nav aria-label={title} className="flex flex-col gap-4 h-full min-h-0">
      <div className="flex items-start justify-between gap-2">
        <p className="min-w-0 truncate font-heading text-[28px] leading-9 tracking-tight">
          {title}
        </p>
        <div className="flex shrink-0 items-center gap-1">
          <Button size="icon-sm" onClick={onNewChat} aria-label={labels.newChat}>
            <Plus className="size-4" aria-hidden="true" />
          </Button>
          {onRequestClose ? (
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              onClick={onRequestClose}
              aria-label={labels.close}
            >
              <X className="size-4" aria-hidden="true" />
            </Button>
          ) : null}
        </div>
      </div>

      <Input
        value={searchQuery}
        onChange={(event) => onSearchChange(event.target.value)}
        placeholder={labels.searchPlaceholder}
        className="bg-background/60"
      />

      <div
        className={
          showProjectPanel
            ? "grid grid-rows-2 gap-3 flex-1 min-h-0"
            : "flex flex-col flex-1 min-h-0"
        }
      >
        <div className={showProjectPanel ? "min-h-0" : "min-h-0 flex-1"}>
          <ScrollArea className="h-full min-h-0">
            <div className="space-y-3 pr-2">
              {groups.length === 0 ? (
                <p className="text-muted-foreground text-sm">{labels.noResults}</p>
              ) : (
                groups.map((group) => (
                  <div key={group.label} className="space-y-1">
                    <p className="px-2 text-muted-foreground text-xs font-medium">
                      {group.label}
                    </p>
                    {group.items.map((chat) => {
                      const active = activeChatId === chat.id
                      return (
                        <div
                          key={chat.id}
                          className={`group relative flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-2 transition-colors ${
                            active
                              ? "bg-sidebar-accent text-sidebar-accent-foreground"
                              : "hover:bg-sidebar-accent/70"
                          }`}
                          onClick={() => onSelectChat(chat)}
                          role="button"
                          tabIndex={0}
                          onKeyDown={(event) => {
                            if (event.key === "Enter" || event.key === " ") {
                              event.preventDefault()
                              onSelectChat(chat)
                            }
                          }}
                        >
                          <p className="flex-1 min-w-0 font-medium text-sm truncate">
                            {chat.title || labels.untitled}
                          </p>
                          <span className="shrink-0 text-muted-foreground text-[11px] md:group-hover:invisible md:group-focus-within:invisible">
                            {formatRelativeAge(getChatActivityDate(chat))}
                          </span>
                          <DropdownMenu>
                            <DropdownMenuTrigger asChild>
                              <Button
                                type="button"
                                variant="ghost"
                                size="icon"
                                aria-label={`${chat.is_shared ? labels.unshare : labels.share} / ${labels.delete}`}
                                className="absolute right-1 top-1/2 size-7 -translate-y-1/2 opacity-100 md:opacity-0 md:group-hover:opacity-100 md:group-focus-within:opacity-100"
                                onClick={(event) => event.stopPropagation()}
                              >
                                <MoreHorizontal
                                  aria-hidden="true"
                                  className="size-4 text-muted-foreground"
                                />
                              </Button>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent align="end">
                              <DropdownMenuItem
                                onClick={(event) => {
                                  event.stopPropagation()
                                  onToggleShareChat(chat)
                                }}
                              >
                                {chat.is_shared ? labels.unshare : labels.share}
                              </DropdownMenuItem>
                              <DropdownMenuItem
                                variant="destructive"
                                onClick={(event) => {
                                  event.stopPropagation()
                                  onDeleteChat(chat)
                                }}
                              >
                                {labels.delete}
                              </DropdownMenuItem>
                            </DropdownMenuContent>
                          </DropdownMenu>
                        </div>
                      )
                    })}
                  </div>
                ))
              )}
            </div>
          </ScrollArea>
        </div>

        {projects.length > 0 ? (
          <div className="space-y-1 border-t pt-2 min-h-0">
            <div className="flex items-center justify-between px-1">
              <button
                type="button"
                onClick={onOpenProjects}
                className="px-1 text-muted-foreground hover:text-foreground text-xs font-medium transition-colors"
              >
                {labels.projects}
              </button>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="size-6"
                aria-label={projectsCollapsed ? labels.expandProjects : labels.collapseProjects}
                onClick={() => setProjectsCollapsed((value) => !value)}
              >
                {projectsCollapsed ? (
                  <ChevronRight className="size-4" />
                ) : (
                  <ChevronDown className="size-4" />
                )}
              </Button>
            </div>
            {!projectsCollapsed ? (
              <ScrollArea className="h-full min-h-0">
                <div className="space-y-0.5 pr-2">
                  {projects.map((project) => {
                    const active = activeProjectId === project.id
                    return (
                      <div
                        key={project.id}
                        className={`flex cursor-pointer items-center gap-2 rounded-lg px-2 py-2 transition-colors ${
                          active
                            ? "bg-sidebar-accent text-sidebar-accent-foreground"
                            : "hover:bg-sidebar-accent/70"
                        }`}
                        onClick={() => onSelectProject(project)}
                        role="button"
                        tabIndex={0}
                        onKeyDown={(event) => {
                          if (event.key === "Enter" || event.key === " ") {
                            event.preventDefault()
                            onSelectProject(project)
                          }
                        }}
                      >
                        <FolderOpen
                          className="text-muted-foreground size-4 shrink-0"
                          aria-hidden="true"
                        />
                        <p className="font-medium text-sm truncate">{project.name}</p>
                      </div>
                    )
                  })}
                </div>
              </ScrollArea>
            ) : null}
          </div>
        ) : null}
      </div>

      <div className="space-y-1 border-t pt-3">
        <Button
          variant="ghost"
          className="w-full justify-start gap-2"
          onClick={onOpenProjects}
        >
          <FolderOpen className="size-4" aria-hidden="true" />
          {labels.projects}
        </Button>
        <Button
          variant="ghost"
          className="w-full justify-start"
          onClick={onOpenSettings}
        >
          {labels.settings}
        </Button>
        {footer}
      </div>
    </nav>
  )
}

export default ChatSidebar
