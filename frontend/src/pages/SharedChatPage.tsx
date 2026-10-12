import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import type { CSSProperties } from "react"
import { Link, useNavigate, useParams } from "@tanstack/react-router"
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism"

import { BrandLogo } from "@/components/BrandLogo"
import { Button } from "@/components/ui/button"
import { chatApi } from "@/lib/api"
import { useAuth } from "@/lib/auth-context"
import { useI18n } from "@/lib/i18n-context"
import { tokenStore } from "@/lib/storage"
import type { ChatMessage } from "@/lib/types"
import { MessageBubble } from "@/pages/chat/MessageBubble"
import { MessageList } from "@/pages/chat/MessageList"

const noop = () => undefined

export const SharedChatPage = () => {
  const { shareToken } = useParams({ strict: false })
  const navigate = useNavigate()
  const { token } = useAuth()
  const { t } = useI18n()
  const [title, setTitle] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [loading, setLoading] = useState(true)
  const [missing, setMissing] = useState(false)
  const messagesContainerRef = useRef<HTMLDivElement | null>(null)
  const messagesEndRef = useRef<HTMLDivElement | null>(null)
  const codeTheme = useMemo<Record<string, CSSProperties>>(() => oneDark, [])

  useEffect(() => {
    if (!shareToken) return
    if (token || tokenStore.get()) {
      let cancelled = false
      chatApi
        .resolveShared(shareToken)
        .then((resolved) => {
          if (cancelled) return
          navigate({ href: `/chat/${resolved.chat_id}`, replace: true })
        })
        .catch(() => {
          if (cancelled) return
          setMissing(true)
          setLoading(false)
        })
      return () => {
        cancelled = true
      }
    }

    let cancelled = false
    setLoading(true)
    setMissing(false)
    Promise.all([chatApi.resolveShared(shareToken), chatApi.sharedMessages(shareToken)])
      .then(([resolved, nextMessages]) => {
        if (cancelled) return
        setTitle(resolved.title ?? null)
        setMessages(
          nextMessages
            .filter((msg) => msg.role === "user" || msg.role === "assistant")
            .map((msg) => {
              if (msg.role !== "assistant") return msg
              return {
                ...msg,
                thinking_steps: null,
                stream_parts: msg.stream_parts?.filter((part) => part.type === "text") ?? null,
              }
            })
        )
        setLoading(false)
      })
      .catch(() => {
        if (cancelled) return
        setMissing(true)
        setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [navigate, shareToken, token])

  const renderMessage = useCallback(
    (msg: ChatMessage) => {
      const isUser = msg.role === "user"
      const isCodeEvent = msg.tool_event?.type === "code_execution"
      const messageIndex = messages.findIndex((item) => item.id === msg.id)
      const exportQuestion =
        !isUser && messageIndex > 0
          ? [...messages.slice(0, messageIndex)]
              .reverse()
              .find((item) => item.role === "user")?.content ?? null
          : null
      return (
        <MessageBubble
          msg={msg}
          isUser={isUser}
          isCodeEvent={isCodeEvent}
          isThinking={false}
          thinkingLabels={[]}
          currentStepLabel={null}
          currentToolLabel={null}
          actionInfoLevel="none"
          actionsEnabled={false}
          isEditing={false}
          isEditDragActive={false}
          editingAttachments={[]}
          codeTheme={codeTheme}
          t={t}
          exportQuestion={exportQuestion}
          onStartEdit={noop}
          onDeleteFromMessage={noop}
          onRetryMessage={noop}
          onSaveEditedMessage={noop}
          onCancelEdit={noop}
          onEditPasteAttachments={noop}
          onEditFilesSelected={noop}
          onEditDragEnter={noop}
          onEditDragOver={noop}
          onEditDragLeave={noop}
          onEditDrop={noop}
          onRemoveEditingAttachment={noop}
          onPreviewAttachment={noop}
        />
      )
    },
    [codeTheme, messages, t]
  )

  if (loading) {
    return (
      <div className="flex justify-center items-center h-svh text-muted-foreground text-sm">
        {t("common_loading")}
      </div>
    )
  }

  if (missing) {
    return (
      <div className="flex flex-col justify-center items-center gap-4 p-6 h-svh text-center">
        <h1 className="font-semibold text-lg">{t("chat_link_not_shared_title")}</h1>
        <p className="max-w-md text-muted-foreground text-sm">{t("chat_link_not_shared_desc")}</p>
        <Button asChild>
          <Link to="/login">{t("auth_sign_in")}</Link>
        </Button>
      </div>
    )
  }

  const displayTitle = title?.trim() || t("chat_untitled")

  return (
    <div className="relative flex h-svh overflow-hidden bg-background">
      <main
        id="main-content"
        className="relative flex flex-col flex-1 m-2 min-w-0 min-h-0 overflow-hidden bg-background md:rounded-card md:border md:border-border"
      >
        <h1 className="sr-only">{displayTitle}</h1>
        <div className="relative z-10 grid items-center grid-cols-[1fr_auto_1fr] gap-2 bg-background px-3 py-2.5 h-15 shrink-0">
          <div className="flex items-center gap-2 min-w-0 justify-self-start">
            <BrandLogo className="h-7" />
          </div>
          <div className="justify-self-center px-2 min-w-0">
            <p className="font-medium text-sm truncate">{displayTitle}</p>
          </div>
          <div className="flex justify-self-end items-center">
            <Button asChild variant="outline" size="sm">
              <Link to="/login">{t("auth_sign_in")}</Link>
            </Button>
          </div>
        </div>
        <MessageList
          messages={messages}
          isLoading={false}
          onScroll={noop}
          containerRef={messagesContainerRef}
          endRef={messagesEndRef}
          renderMessage={renderMessage}
        />
        <div className="flex justify-center items-center px-4 py-3 border-t shrink-0">
          <p className="text-muted-foreground text-xs">{t("chat_share_dialog_desc")}</p>
        </div>
      </main>
    </div>
  )
}
