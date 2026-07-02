import { useState } from "react"
import type { ReactNode } from "react"

import type { Agent, Chat } from "@/lib/types"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { ChevronDown, ChevronRight, FolderOpen, MoreHorizontal } from "lucide-react"

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
}: ChatSidebarProps) => {
  const [projectsCollapsed, setProjectsCollapsed] = useState(false)
  const showProjectPanel = projects.length > 0 && !projectsCollapsed

  return (
    <nav aria-label={title} className="flex flex-col gap-4 h-full min-h-0">
      <div className="flex justify-between items-center">
        <h2 className="font-semibold text-base">{title}</h2>
        <Button size="sm" onClick={onNewChat}>
          {labels.newChat}
        </Button>
      </div>
      <Input
        value={searchQuery}
        onChange={(event) => onSearchChange(event.target.value)}
        placeholder={labels.searchPlaceholder}
      />
      <div className={showProjectPanel ? "grid grid-rows-2 gap-3 flex-1 min-h-0" : "flex flex-col flex-1 min-h-0"}>
        <div className={showProjectPanel ? "min-h-0" : "min-h-0 flex-1"}>
          <ScrollArea className="h-full min-h-0">
            <div className="space-y-3 pr-3">
              {groups.length === 0 ? (
                <p className="text-muted-foreground text-sm">{labels.noResults}</p>
              ) : groups.map((group) => (
                <div key={group.label} className="space-y-2">
                  <p className="text-muted-foreground text-xs uppercase tracking-wide">
                    {group.label}
                  </p>
                  {group.items.map((chat) => (
                    <Card
                      key={chat.id}
                      className={`group relative cursor-pointer px-3 py-2 ${
                        activeChatId === chat.id ? "border-primary" : ""
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
                      <div className="relative flex items-center gap-2 w-full">
                        <p className="flex-1 min-w-0 max-w-48 font-medium truncate">
                          {chat.title || labels.untitled}
                        </p>
                        <div className="flex items-center gap-2 shrink-0">
                          <span className="text-muted-foreground text-xs">
                            {formatRelativeAge(getChatActivityDate(chat))}
                          </span>
                        </div>
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button
                              type="button"
                              variant="ghost"
                              size="icon"
                              aria-label={`${chat.is_shared ? labels.unshare : labels.share} / ${labels.delete}`}
                              className="top-1/2 right-2 absolute opacity-100 md:opacity-0 md:group-hover:opacity-100 md:group-focus-within:opacity-100 transition -translate-y-1/2"
                              onClick={(event) => event.stopPropagation()}
                            >
                              <MoreHorizontal aria-hidden="true" className="w-4 h-4 text-muted-foreground" />
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
                    </Card>
                  ))}
                </div>
              ))}
            </div>
          </ScrollArea>
        </div>
        {projects.length > 0 ? (
          <div className="space-y-2 pt-2 border-t min-h-0">
            <div className="flex items-center justify-between">
              <button
                type="button"
                onClick={onOpenProjects}
                className="text-muted-foreground hover:text-foreground text-xs uppercase tracking-wide transition-colors"
              >
                {labels.projects}
              </button>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="w-6 h-6"
                aria-label={projectsCollapsed ? "Expand projects" : "Collapse projects"}
                onClick={() => setProjectsCollapsed((value) => !value)}
              >
                {projectsCollapsed ? (
                  <ChevronRight className="w-4 h-4" />
                ) : (
                  <ChevronDown className="w-4 h-4" />
                )}
              </Button>
            </div>
            {!projectsCollapsed ? (
              <ScrollArea className="h-full min-h-0">
                <div className="space-y-2 pr-3">
                  {projects.map((project) => (
                    <Card
                      key={project.id}
                      className={`cursor-pointer px-3 py-2 ${
                        activeProjectId === project.id ? "border-primary" : ""
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
                      <div className="flex items-center gap-2">
                        <FolderOpen className="text-muted-foreground h-4 w-4 shrink-0" aria-hidden="true" />
                        <p className="font-medium truncate">{project.name}</p>
                      </div>
                    </Card>
                  ))}
                </div>
              </ScrollArea>
            ) : null}
          </div>
        ) : null}
      </div>
      <div className="pt-3 border-t">
        <div className="flex flex-col gap-2">
          <Button variant="outline" onClick={onOpenProjects}>
            <FolderOpen className="w-4 h-4" aria-hidden="true" />
            {labels.projects}
          </Button>
          <Button variant="outline" onClick={onOpenSettings}>
            {labels.settings}
          </Button>
          {footer}
        </div>
      </div>
    </nav>
  )
}

export default ChatSidebar
