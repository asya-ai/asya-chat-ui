import { memo } from "react"
import type { ReactNode } from "react"

import type { ChatMessage } from "@/lib/types"
import { useI18n } from "@/lib/i18n-context"
import { Skeleton } from "@/components/ui/skeleton"

type MessageListProps = {
  messages: ChatMessage[]
  isLoading?: boolean
  onScroll: () => void
  containerRef: React.RefObject<HTMLDivElement | null>
  endRef: React.RefObject<HTMLDivElement | null>
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
}: MessageListProps) => {
  const { t } = useI18n()
  return (
    <div
      ref={containerRef}
      className="flex-1 space-y-4 p-6 min-h-0 overflow-y-auto"
      onScroll={onScroll}
      aria-live="polite"
      aria-relevant="additions text"
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
        <div className="flex flex-col justify-center items-center min-h-full text-center">
          <img
            src="/logo_asya.png"
            alt=""
            className="mb-4 size-14 rounded-xl object-contain dark:hidden"
          />
          <img
            src="/logo_asya_light.png"
            alt=""
            className="mb-4 size-14 rounded-xl object-contain hidden dark:block"
          />
          <h2 className="font-heading text-3xl font-normal tracking-tight">
            {t("chat_welcome_title")}
          </h2>
          <p className="mt-2 text-muted-foreground text-sm">
            {t("chat_welcome_created_by")}{" "}
            <a
              href="https://asya.ai"
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary underline underline-offset-2"
            >
              asya.ai
            </a>
          </p>
        </div>
      ) : null}
      <div ref={endRef} />
    </div>
  )
}

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
