import type { ReactNode } from "react"

import type { ChatMessage } from "@/lib/types"
import { Skeleton } from "@/components/ui/skeleton"

type MessageListProps = {
  messages: ChatMessage[]
  isLoading?: boolean
  emptyLabel: string
  onScroll: () => void
  containerRef: React.RefObject<HTMLDivElement | null>
  endRef: React.RefObject<HTMLDivElement | null>
  renderMessage: (msg: ChatMessage) => ReactNode
  loadingFallback?: ReactNode
}

export const MessageList = ({
  messages,
  isLoading,
  emptyLabel,
  onScroll,
  containerRef,
  endRef,
  renderMessage,
  loadingFallback,
}: MessageListProps) => {
  return (
    <div
      ref={containerRef}
      className="flex-1 space-y-4 p-6 min-h-0 overflow-y-auto"
      onScroll={onScroll}
    >
      {isLoading
        ? loadingFallback ?? (
            <div className="space-y-3">
              <Skeleton className="h-5 w-40" />
              <Skeleton className="h-20 w-3/4" />
              <Skeleton className="h-5 w-32" />
              <Skeleton className="h-16 w-2/3" />
            </div>
          )
        : null}
      {messages.map((msg) => (
        <div key={msg.id} data-message-id={msg.id}>
          {renderMessage(msg)}
        </div>
      ))}
      {!isLoading && messages.length === 0 ? (
        <p className="text-muted-foreground text-sm">{emptyLabel}</p>
      ) : null}
      <div ref={endRef} />
    </div>
  )
}
