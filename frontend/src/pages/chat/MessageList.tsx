import { forwardRef, memo, useCallback, useImperativeHandle, useRef } from "react"
import type { ComponentPropsWithRef, ReactNode } from "react"
import { Virtuoso, type VirtuosoHandle } from "react-virtuoso"

import type { ChatMessage } from "@/lib/types"
import { useI18n } from "@/lib/i18n-context"
import { Skeleton } from "@/components/ui/skeleton"

export type MessageListHandle = {
  scrollToBottom: (behavior?: ScrollBehavior) => void
  scrollToIndex: (
    index: number,
    options?: { align?: "start" | "center" | "end"; behavior?: ScrollBehavior }
  ) => void
}

type MessageListProps = {
  messages: ChatMessage[]
  welcomeTitle: string
  isLoading?: boolean
  followOutput?: boolean
  onAtBottomChange: (atBottom: boolean) => void
  renderMessage: (msg: ChatMessage) => ReactNode
  loadingFallback?: ReactNode
}

const VirtuosoScroller = forwardRef<HTMLDivElement, ComponentPropsWithRef<"div">>(
  function VirtuosoScroller({ style, ...props }, ref) {
    return (
      <div
        {...props}
        ref={ref}
        style={{
          ...style,
          overflowX: "hidden",
        }}
      />
    )
  }
)

const MessageListComponent = forwardRef<MessageListHandle, MessageListProps>(
  function MessageListComponent(
    {
      messages,
      welcomeTitle,
      isLoading,
      followOutput = false,
      onAtBottomChange,
      renderMessage,
      loadingFallback,
    },
    ref
  ) {
    const { t } = useI18n()
    const virtuosoRef = useRef<VirtuosoHandle | null>(null)

    useImperativeHandle(
      ref,
      () => ({
        scrollToBottom: (behavior: ScrollBehavior = "smooth") => {
          if (messages.length === 0) return
          virtuosoRef.current?.scrollToIndex({
            index: messages.length - 1,
            align: "end",
            behavior: behavior === "smooth" ? "smooth" : "auto",
          })
        },
        scrollToIndex: (index, options) => {
          const behavior = options?.behavior ?? "smooth"
          virtuosoRef.current?.scrollToIndex({
            index,
            align: options?.align ?? "start",
            behavior: behavior === "smooth" ? "smooth" : "auto",
          })
        },
      }),
      [messages.length]
    )

    const itemContent = useCallback(
      (_index: number, msg: ChatMessage) => (
        // flow-root + padding (not margin) so ResizeObserver heights stay stable —
        // collapsing margins on markdown children cause Virtuoso scroll jumps.
        <div
          className="flow-root min-w-0 max-w-full overflow-x-hidden px-4 pb-6 md:px-8"
          data-message-id={msg.id}
        >
          {renderMessage(msg)}
        </div>
      ),
      [renderMessage]
    )

    const computeItemKey = useCallback((_index: number, msg: ChatMessage) => msg.id, [])

    if (isLoading) {
      return (
        <div className="relative flex min-h-0 flex-1 flex-col gap-6 overflow-x-hidden overflow-y-auto px-4 py-6 md:px-8">
          {loadingFallback ?? (
            <div className="flex flex-col gap-3">
              <Skeleton className="h-5 w-40" />
              <Skeleton className="h-20 w-3/4" />
              <Skeleton className="h-5 w-32" />
              <Skeleton className="h-16 w-2/3" />
            </div>
          )}
        </div>
      )
    }

    if (messages.length === 0) {
      return (
        <div className="relative flex min-h-0 flex-1 flex-col gap-6 overflow-x-hidden overflow-y-auto px-4 py-6 md:px-8">
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
        </div>
      )
    }

    return (
      <div className="relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden py-6">
        <Virtuoso
          ref={virtuosoRef}
          className="h-full min-h-0 w-full min-w-0"
          data={messages}
          computeItemKey={computeItemKey}
          itemContent={itemContent}
          components={{ Scroller: VirtuosoScroller }}
          initialTopMostItemIndex={{
            index: messages.length - 1,
            align: "start",
          }}
          followOutput={followOutput ? "smooth" : false}
          atBottomStateChange={onAtBottomChange}
          atBottomThreshold={80}
          defaultItemHeight={280}
          increaseViewportBy={{ top: 600, bottom: 600 }}
          aria-live="polite"
          aria-relevant="additions text"
        />
      </div>
    )
  }
)

export const MessageList = memo(
  MessageListComponent,
  (prev, next) =>
    prev.messages === next.messages &&
    prev.welcomeTitle === next.welcomeTitle &&
    prev.isLoading === next.isLoading &&
    prev.followOutput === next.followOutput &&
    prev.onAtBottomChange === next.onAtBottomChange &&
    prev.renderMessage === next.renderMessage &&
    prev.loadingFallback === next.loadingFallback
)
