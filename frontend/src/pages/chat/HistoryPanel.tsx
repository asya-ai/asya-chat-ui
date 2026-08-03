import type { ReactNode } from "react"

import type { Chat } from "@/lib/types"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import { ScrollArea } from "@/components/ui/scroll-area"
import { MoreHorizontal, Search } from "lucide-react"

type HistoryPanelProps = {
  chats: Chat[]
  query: string
  leadingAction: ReactNode
  labels: {
    title: string
    search: string
    empty: string
    untitled: string
    delete: string
    share: string
    unshare: string
    actions: string
  }
  onQueryChange: (value: string) => void
  onSelectChat: (chat: Chat) => void
  onDeleteChat: (chat: Chat) => void
  onToggleShareChat: (chat: Chat) => void
  formatDate: (value: string) => string
}

export const HistoryPanel = ({
  chats,
  query,
  leadingAction,
  labels,
  onQueryChange,
  onSelectChat,
  onDeleteChat,
  onToggleShareChat,
  formatDate,
}: HistoryPanelProps) => (
  <>
    <div className="flex h-15 shrink-0 items-center gap-3 px-3 py-2.5">
      <div className="flex min-w-0 items-center gap-1.5">
        {leadingAction}
        <h1 className="truncate font-heading text-4xl leading-10">{labels.title}</h1>
      </div>
    </div>

    <div className="mx-auto flex min-h-0 w-full max-w-143.75 flex-1 flex-col gap-5 px-3 pb-3 pt-2 sm:px-0">
      <div className="relative shrink-0">
        <Search
          aria-hidden="true"
          className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
        />
        <Input
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          placeholder={labels.search}
          className="pl-9"
        />
      </div>

      <ScrollArea className="min-h-0 flex-1">
        {chats.length > 0 ? (
          <div className="flex flex-col pb-3">
            {chats.map((chat) => (
              <div
                key={chat.id}
                role="link"
                tabIndex={0}
                className="group relative flex cursor-pointer flex-col gap-1.5 rounded-lg p-2 pr-11 outline-none transition-colors hover:bg-secondary focus-visible:bg-secondary focus-visible:ring-2 focus-visible:ring-ring"
                onClick={() => onSelectChat(chat)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault()
                    onSelectChat(chat)
                  }
                }}
              >
                <p className="text-sm font-medium leading-4">
                  {chat.title || labels.untitled}
                </p>
                <p className="text-xs leading-4 text-(--content-secondary)">
                  {formatDate(chat.last_activity_at || chat.created_at)}
                </p>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      className="absolute right-2 top-1/2 -translate-y-1/2 opacity-100 sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100"
                      aria-label={labels.actions}
                      onClick={(event) => event.stopPropagation()}
                    >
                      <MoreHorizontal aria-hidden="true" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    <DropdownMenuGroup>
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
                    </DropdownMenuGroup>
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
            ))}
          </div>
        ) : (
          <p className="p-8 text-center text-sm text-muted-foreground">{labels.empty}</p>
        )}
      </ScrollArea>
    </div>
  </>
)

export default HistoryPanel
