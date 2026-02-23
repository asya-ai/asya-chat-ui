import type { ReactNode } from "react"

import type { Chat } from "@/lib/types"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { MoreHorizontal } from "lucide-react"

type ChatGroup = { label: string; items: Chat[] }

type ChatSidebarProps = {
  title: string
  labels: {
    newChat: string
    untitled: string
    settings: string
    delete: string
  }
  groups: ChatGroup[]
  activeChatId?: string | null
  onNewChat: () => void
  onSelectChat: (chat: Chat) => void
  onDeleteChat: (chat: Chat) => void
  onOpenSettings: () => void
  formatRelativeAge: (dateString: string) => string
  getChatActivityDate: (chat: Chat) => string
  footer?: ReactNode
}

export const ChatSidebar = ({
  title,
  labels,
  groups,
  activeChatId,
  onNewChat,
  onSelectChat,
  onDeleteChat,
  onOpenSettings,
  formatRelativeAge,
  getChatActivityDate,
  footer,
}: ChatSidebarProps) => {
  return (
    <div className="flex flex-col gap-4 h-full min-h-0">
      <div className="flex justify-between items-center">
        <h2 className="font-semibold text-base">{title}</h2>
        <Button size="sm" onClick={onNewChat}>
          {labels.newChat}
        </Button>
      </div>
      <ScrollArea className="flex-1 pr-1 min-h-0">
        <div className="space-y-3">
          {groups.map((group) => (
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
                >
                  <div className="relative flex justify-between items-center gap-2 w-full">
                    <p className="max-w-50 font-medium text-sm truncate">
                      {chat.title || labels.untitled}
                    </p>
                    <div className="flex items-center gap-2">
                      <span className="text-muted-foreground text-xs">
                        {formatRelativeAge(getChatActivityDate(chat))}
                      </span>
                    </div>
                    <DropdownMenu >
                      <DropdownMenuTrigger asChild>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="top-1/2 right-2 absolute opacity-0 group-hover:opacity-100 transition -translate-y-1/2"
                          onClick={(event) => event.stopPropagation()}
                        >
                          <MoreHorizontal className="w-4 h-4 text-muted-foreground" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
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
      <div className="pt-3 border-t">
        <div className="flex flex-col gap-2">
          <Button variant="outline" onClick={onOpenSettings}>
            {labels.settings}
          </Button>
          {footer}
        </div>
      </div>
    </div>
  )
}

export default ChatSidebar
