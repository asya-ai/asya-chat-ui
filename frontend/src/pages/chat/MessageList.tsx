import { memo, type CSSProperties, type ReactNode, type RefObject } from "react"

import type { ChatMessage } from "@/lib/types"
import { cn } from "@/lib/utils"
import { Skeleton } from "@/components/ui/skeleton"

const REVEAL_TAIL = 2

type MessageListProps = {
  messages: ChatMessage[]
  isLoading?: boolean
  onScroll: () => void
  containerRef: RefObject<HTMLDivElement | null>
  endRef: RefObject<HTMLDivElement | null>
  renderMessage: (msg: ChatMessage) => ReactNode
  loadingFallback?: ReactNode
}

const MessageListComponent = ({
  messages,
  isLoading,
  onScroll,
  containerRef,
  endRef,
  renderMessage,
  loadingFallback,
}: MessageListProps) => (
  <div
    ref={containerRef}
    className="relative flex flex-col flex-1 gap-6 px-4 md:px-8 pt-6 pb-0 min-h-0 overflow-x-hidden overflow-y-auto"
    onScroll={onScroll}
    aria-live="polite"
    aria-relevant="additions text"
  >
    {isLoading
      ? loadingFallback ?? (
          <div className="mx-auto flex w-full min-w-0 max-w-(--chat-content-width) flex-col gap-6">
            <div className="flex flex-col items-end gap-2">
              <Skeleton className="h-5 w-28" />
              <Skeleton className="h-16 w-[min(85%,20rem)]" />
            </div>
            <div className="flex flex-col items-start gap-2">
              <Skeleton className="h-5 w-24" />
              <Skeleton className="h-24 w-full max-w-md" />
              <Skeleton className="h-16 w-[min(100%,18rem)]" />
            </div>
          </div>
        )
      : null}
    {messages.map((msg, index) => {
      const revealIndex = index - (messages.length - REVEAL_TAIL)
      const reveal = !isLoading && revealIndex >= 0
      return (
        <div
          key={msg.id}
          className={cn("min-w-0 max-w-full shrink-0", reveal && "chat-message-reveal")}
          style={
            reveal
              ? ({ "--reveal": revealIndex } as CSSProperties)
              : undefined
          }
          data-message-id={msg.id}
        >
          {renderMessage(msg)}
        </div>
      )
    })}
    <div ref={endRef} />
  </div>
)

export const MessageList = memo(
  MessageListComponent,
  (prev, next) =>
    prev.messages === next.messages &&
    prev.isLoading === next.isLoading &&
    prev.onScroll === next.onScroll &&
    prev.containerRef === next.containerRef &&
    prev.endRef === next.endRef &&
    prev.renderMessage === next.renderMessage &&
    prev.loadingFallback === next.loadingFallback
)
