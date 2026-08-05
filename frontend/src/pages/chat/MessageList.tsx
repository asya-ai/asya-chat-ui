import { memo } from "react"
import type { ReactNode, RefObject } from "react"

import type { ChatMessage } from "@/lib/types"
import { useI18n } from "@/lib/i18n-context"
import { Skeleton } from "@/components/ui/skeleton"

type MessageListProps = {
  messages: ChatMessage[]
  welcomeTitle: string
  isLoading?: boolean
  onScroll: () => void
  containerRef: RefObject<HTMLDivElement | null>
  endRef: RefObject<HTMLDivElement | null>
  renderMessage: (msg: ChatMessage) => ReactNode
  loadingFallback?: ReactNode
}

const MessageListComponent = ({
  messages,
  welcomeTitle,
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
      className="relative flex min-h-0 flex-1 flex-col gap-6 overflow-x-hidden overflow-y-auto px-4 py-6 md:px-8"
      onScroll={onScroll}
      aria-live="polite"
      aria-relevant="additions text"
    >
      {isLoading
        ? loadingFallback ?? (
            <div className="flex flex-col gap-3">
              <Skeleton className="h-5 w-40" />
              <Skeleton className="h-20 w-3/4" />
              <Skeleton className="h-5 w-32" />
              <Skeleton className="h-16 w-2/3" />
            </div>
          )
        : null}
      {messages.map((msg) => (
        <div key={msg.id} className="min-w-0 max-w-full shrink-0" data-message-id={msg.id}>
          {renderMessage(msg)}
        </div>
      ))}
      {!isLoading && messages.length === 0 ? (
        <div className="flex min-h-full flex-col items-center text-center md:block max-md:pt-24">
          <h2 className="font-heading text-4xl font-normal leading-10 md:hidden">
            {welcomeTitle}
          </h2>
          <div className="absolute -bottom-px left-1/2 flex w-(--chat-content-width) -translate-x-1/2 items-start gap-2 pb-8 text-left text-xs leading-4 text-(--content-secondary) max-md:hidden">
            <span
              aria-hidden="true"
              className="figma-icon size-4 shrink-0"
              style={{ maskImage: "url('/icon-privacy.svg')" }}
            />
            <p>{t("chat_privacy_message")}</p>
          </div>
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
    prev.welcomeTitle === next.welcomeTitle &&
    prev.isLoading === next.isLoading &&
    prev.onScroll === next.onScroll &&
    prev.containerRef === next.containerRef &&
    prev.endRef === next.endRef &&
    prev.renderMessage === next.renderMessage &&
    prev.loadingFallback === next.loadingFallback
)
