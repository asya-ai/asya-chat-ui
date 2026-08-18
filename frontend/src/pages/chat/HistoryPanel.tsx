import { useEffect, useState, type ReactNode } from "react"

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

type HistoryRow = {
  chat: Chat
  modelLabel?: string | null
  spaceLabel?: string | null
}

type HistoryGroup = {
  label: string
  rows: HistoryRow[]
}

type HistoryPanelProps = {
  groups: HistoryGroup[]
  leadingAction: ReactNode
  trailingAction?: ReactNode
  labels: {
    title: string
    search: string
    empty: string
    emptySearch: string
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
}

export const HistoryPanel = ({
  groups,
  leadingAction,
  trailingAction,
  labels,
  onQueryChange,
  onSelectChat,
  onDeleteChat,
  onToggleShareChat,
}: HistoryPanelProps) => {
  const [query, setQuery] = useState("")
  const hasRows = groups.some((group) => group.rows.length > 0)

  useEffect(() => {
    const timerId = window.setTimeout(() => {
      onQueryChange(query.trim())
    }, 250)
    return () => window.clearTimeout(timerId)
  }, [query, onQueryChange])

  return (
    <>
      <div className="flex h-15 shrink-0 items-center justify-between gap-3 px-3 py-2.5">
        <div className="flex min-w-0 items-center gap-1.5">
          {leadingAction}
          <h1 className="truncate font-heading text-4xl leading-10">{labels.title}</h1>
        </div>
        {trailingAction ? (
          <div className="flex shrink-0 items-center gap-2">{trailingAction}</div>
        ) : null}
      </div>

      <div className="mx-auto flex min-h-0 w-full max-w-4xl flex-1 flex-col gap-5 px-3 pb-3 pt-2 sm:px-6">
        <div className="relative mx-auto w-full max-w-md shrink-0">
          <Search
            aria-hidden="true"
            className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
          />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={labels.search}
            className="pl-9"
          />
        </div>

        <ScrollArea className="min-h-0 flex-1">
          {hasRows ? (
            <div className="flex flex-col gap-6 pb-3">
              {groups.map((group) =>
                group.rows.length === 0 ? null : (
                  <section key={group.label} className="flex flex-col gap-1">
                    <h2 className="px-2 text-xs font-medium leading-4 text-muted-foreground">
                      {group.label}
                    </h2>
                    <div className="flex flex-col">
                      {group.rows.map(({ chat, modelLabel, spaceLabel }) => (
                        <div
                          key={chat.id}
                          role="link"
                          tabIndex={0}
                          className="group relative flex cursor-pointer items-center gap-3 rounded-lg p-2 pr-11 outline-none transition-colors hover:bg-secondary focus-visible:bg-secondary focus-visible:ring-2 focus-visible:ring-ring"
                          onClick={() => onSelectChat(chat)}
                          onKeyDown={(event) => {
                            if (event.key === "Enter" || event.key === " ") {
                              event.preventDefault()
                              onSelectChat(chat)
                            }
                          }}
                        >
                          <div className="min-w-0 flex-1">
                            <p className="truncate text-sm font-medium leading-4">
                              {chat.title || labels.untitled}
                            </p>
                            {spaceLabel ? (
                              <span className="mt-1.5 inline-flex max-w-full items-center gap-1 rounded-full bg-secondary px-2 py-0.5 text-xs leading-4 text-muted-foreground">
                                <span
                                  aria-hidden="true"
                                  className="figma-icon size-3.5 shrink-0"
                                  style={{ maskImage: "url('/icon-spaces.svg')" }}
                                />
                                <span className="truncate">{spaceLabel}</span>
                              </span>
                            ) : null}
                          </div>
                          {modelLabel ? (
                            <span className="hidden shrink-0 items-center gap-1.5 text-xs leading-4 text-muted-foreground sm:inline-flex">
                              {modelLabel}
                            </span>
                          ) : null}
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
                  </section>
                )
              )}
            </div>
          ) : (
            <p className="p-8 text-center text-sm text-muted-foreground">
              {query.trim() ? labels.emptySearch : labels.empty}
            </p>
          )}
        </ScrollArea>
      </div>
    </>
  )
}

export default HistoryPanel
