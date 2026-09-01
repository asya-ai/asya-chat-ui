import {
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type ReactNode,
  type Ref,
  type PointerEvent as ReactPointerEvent,
} from "react"

import type { ChatMessageAttachmentInput } from "@/lib/types"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Textarea } from "@/components/ui/textarea"
import { ArrowUp, Plus, Square, X } from "lucide-react"
import { useI18n } from "@/lib/i18n-context"
import { shouldSubmitOnEnter } from "@/lib/chat-input"
import { composerTextareaHeightStore } from "@/lib/storage"
import { cn } from "@/lib/utils"

const getMaxComposerTextareaHeight = () => {
  if (typeof window === "undefined") {
    return Math.round(800 * composerTextareaHeightStore.maxHeightVh)
  }
  return Math.round(window.innerHeight * composerTextareaHeightStore.maxHeightVh)
}

const clampComposerTextareaHeight = (px: number) => {
  const max = getMaxComposerTextareaHeight()
  return Math.min(max, Math.max(composerTextareaHeightStore.min, Math.round(px)))
}

export type ChatComposerHandle = {
  getValue: () => string
  setValue: (next: string) => void
}

type ChatComposerProps = {
  placeholder: string
  loading: boolean
  readOnly?: boolean
  isDragActive: boolean
  pendingAttachments: ChatMessageAttachmentInput[]
  attachmentError?: string | null
  ref?: Ref<ChatComposerHandle>
  inputRef?: React.RefObject<HTMLTextAreaElement | null>
  modelSelect?: ReactNode
  showModelSelect?: boolean
  hasPrompts?: boolean
  onSend: () => void
  onStop: () => void
  onFilesSelected: (files: File[]) => void
  onRemoveAttachment: (index: number) => void
  onPreviewAttachment: (attachment: ChatMessageAttachmentInput) => void
  onPasteAttachments: (event: React.ClipboardEvent<HTMLTextAreaElement>) => void
  onDragEnter: (event: React.DragEvent<HTMLDivElement>) => void
  onDragOver: (event: React.DragEvent<HTMLDivElement>) => void
  onDragLeave: (event: React.DragEvent<HTMLDivElement>) => void
  onDrop: (event: React.DragEvent<HTMLDivElement>) => void
  onInsertPromptRequest?: () => void
  onDraftChange?: (value: string) => void
  sendLabel: string
  stopLabel: string
  welcomeTitle: string
  centered?: boolean
}

export const ChatComposer = ({
  placeholder,
  loading,
  readOnly = false,
  isDragActive,
  pendingAttachments,
  attachmentError,
  ref,
  inputRef,
  modelSelect,
  showModelSelect = false,
  hasPrompts = false,
  onSend,
  onStop,
  onFilesSelected,
  onRemoveAttachment,
  onPreviewAttachment,
  onPasteAttachments,
  onDragEnter,
  onDragOver,
  onDragLeave,
  onDrop,
  onInsertPromptRequest,
  onDraftChange,
  sendLabel,
  stopLabel,
  welcomeTitle,
  centered = false,
}: ChatComposerProps) => {
  const { t } = useI18n()
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)
  const heightPxRef = useRef(clampComposerTextareaHeight(composerTextareaHeightStore.get()))
  const resizeStartYRef = useRef(0)
  const resizeStartHeightRef = useRef(0)
  const [hasText, setHasText] = useState(false)
  const [textareaHeightPx, setTextareaHeightPx] = useState(() =>
    clampComposerTextareaHeight(composerTextareaHeightStore.get())
  )
  const [isResizing, setIsResizing] = useState(false)
  const onDraftChangeRef = useRef(onDraftChange)
  onDraftChangeRef.current = onDraftChange

  useEffect(() => {
    heightPxRef.current = textareaHeightPx
  }, [textareaHeightPx])

  useEffect(() => {
    if (!isResizing) return
    const previousCursor = globalThis.document.body.style.cursor
    const previousUserSelect = globalThis.document.body.style.userSelect
    globalThis.document.body.style.cursor = "row-resize"
    globalThis.document.body.style.userSelect = "none"
    return () => {
      globalThis.document.body.style.cursor = previousCursor
      globalThis.document.body.style.userSelect = previousUserSelect
    }
  }, [isResizing])

  const stopResizing = () => {
    setIsResizing(false)
    composerTextareaHeightStore.set(heightPxRef.current)
  }

  const handleResizePointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (centered || event.button !== 0) return
    event.preventDefault()
    event.currentTarget.setPointerCapture(event.pointerId)
    resizeStartYRef.current = event.clientY
    resizeStartHeightRef.current = heightPxRef.current
    setIsResizing(true)
  }

  const handleResizePointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!isResizing) return
    const delta = resizeStartYRef.current - event.clientY
    const next = clampComposerTextareaHeight(resizeStartHeightRef.current + delta)
    heightPxRef.current = next
    setTextareaHeightPx(next)
  }

  const handleResizePointerUp = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!isResizing) return
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
    stopResizing()
  }

  const handleResizeDoubleClick = () => {
    if (centered) return
    const next = composerTextareaHeightStore.default
    heightPxRef.current = next
    setTextareaHeightPx(next)
    composerTextareaHeightStore.set(next)
  }

  const assignTextareaRef = useCallback(
    (el: HTMLTextAreaElement | null) => {
      textareaRef.current = el
      if (inputRef) inputRef.current = el
    },
    [inputRef]
  )

  const syncDraft = (next: string) => {
    const nextHasText = next.trim().length > 0
    setHasText((prev) => (prev === nextHasText ? prev : nextHasText))
    onDraftChangeRef.current?.(next)
  }

  useImperativeHandle(ref, () => ({
    getValue: () => textareaRef.current?.value ?? "",
    setValue: (next: string) => {
      const el = textareaRef.current
      if (el) el.value = next
      syncDraft(next)
    },
  }))

  const handlePickFiles = () => {
    fileInputRef.current?.click()
  }

  const handleFilesSelected = (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? [])
    if (files.length === 0) return
    onFilesSelected(files)
    event.target.value = ""
  }

  const canSend = !readOnly && (hasText || pendingAttachments.length > 0)

  return (
    <div
      className={cn(
        "z-10",
        centered
          ? "absolute inset-x-0 top-15 bottom-[calc(1rem+env(safe-area-inset-bottom))] flex flex-col items-center justify-center gap-8 px-4 max-md:justify-end max-md:gap-6"
          : "mx-auto w-[min(var(--chat-content-width),calc(100%-2rem))] shrink-0 pb-[calc(1rem+env(safe-area-inset-bottom))]"
      )}
    >
      {centered ? (
        <h2 className="max-md:hidden w-full max-w-(--chat-content-width) shrink-0 font-heading font-normal text-4xl text-center leading-10">
          {welcomeTitle}
        </h2>
      ) : null}
      <div
        className={cn(
          "@container/composer flex w-full max-w-(--chat-content-width) flex-col justify-between bg-card border border-border rounded-2xl",
          centered ? "min-h-26 gap-0 p-0" : "gap-2 p-2",
          "shadow-none transition-[box-shadow,border-color]",
          "focus-within:border-primary/40 focus-within:shadow-[0_0_0_3px] focus-within:shadow-primary/30",
          isDragActive && "border-primary/50 shadow-[0_0_0_3px] shadow-primary/30"
        )}
        onDragEnter={onDragEnter}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
      >
        {!centered ? (
          <div
            role="separator"
            aria-orientation="horizontal"
            aria-label="Resize message input"
            aria-valuemin={composerTextareaHeightStore.min}
            aria-valuemax={getMaxComposerTextareaHeight()}
            aria-valuenow={textareaHeightPx}
            title="Drag to resize · double-click to reset"
            className={cn(
              "flex h-2 w-full shrink-0 cursor-row-resize touch-none items-center justify-center",
              "before:h-1 before:w-10 before:rounded-full before:bg-border before:transition-colors",
              "hover:before:bg-muted-foreground/40",
              isResizing && "before:bg-primary"
            )}
            onPointerDown={handleResizePointerDown}
            onPointerMove={handleResizePointerMove}
            onPointerUp={handleResizePointerUp}
            onPointerCancel={handleResizePointerUp}
            onDoubleClick={handleResizeDoubleClick}
          />
        ) : null}
        <div
          style={centered ? undefined : { minHeight: textareaHeightPx }}
          className={cn(!centered && "shrink-0")}
        >
          <Textarea
            ref={assignTextareaRef}
            defaultValue=""
            onChange={(event) => syncDraft(event.target.value)}
            onPaste={onPasteAttachments}
            onKeyDown={(event) => {
              if (!shouldSubmitOnEnter(event)) return

              event.preventDefault()
              if (canSend) {
                onSend()
              }
            }}
            placeholder={placeholder}
            rows={centered ? 1 : 2}
            className={cn(
              "bg-transparent shadow-none border-0 text-base resize-none",
              centered
                ? "min-h-13 max-h-[50vh] max-md:max-h-[50dvh] overflow-y-auto -mx-px -mt-px w-[calc(100%+2px)] px-5 py-4 leading-5"
                : "field-sizing-content min-h-13 max-h-[50vh] max-md:max-h-[50dvh] overflow-y-auto px-1.5 py-1",
              "placeholder:text-muted-foreground",
              "focus-visible:border-0 focus-visible:ring-0 dark:bg-transparent"
            )}
            disabled={readOnly}
          />
        </div>
        {attachmentError ? (
          <p className="px-1.5 text-destructive text-sm" role="alert">
            {attachmentError}
          </p>
        ) : null}
        {pendingAttachments.length > 0 ? (
          <div className="flex flex-wrap gap-2 px-1">
            {pendingAttachments.map((attachment, index) => {
              const isImage = attachment.content_type.startsWith("image/")
              const previewSrc =
                attachment.preview_url ||
                (attachment.data_base64
                  ? `data:${attachment.content_type};base64,${attachment.data_base64}`
                  : attachment.content_url || "")
              const progress = Math.max(0, Math.min(1, attachment.upload_progress ?? 0))
              const uploading =
                attachment.upload_status === "uploading" ||
                attachment.upload_status === "pending"
              const failed = attachment.upload_status === "error"
              const radius = 14
              const circumference = 2 * Math.PI * radius
              const dashOffset = circumference * (1 - progress)
              return (
                <div
                  key={attachment.local_id || `${attachment.file_name}-${index}`}
                  className="relative"
                >
                  {isImage ? (
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      className="p-0 rounded-md w-auto h-auto overflow-hidden"
                      onClick={() => onPreviewAttachment(attachment)}
                    >
                      <img
                        src={previewSrc}
                        alt={attachment.file_name}
                        className={cn(
                          "rounded-md w-16 h-16 object-cover",
                          uploading || failed ? "opacity-50" : null
                        )}
                      />
                    </Button>
                  ) : (
                    <div
                      className={cn(
                        "px-3 py-2 border rounded-md text-xs",
                        uploading || failed ? "opacity-50" : null
                      )}
                    >
                      {attachment.file_name}
                    </div>
                  )}
                  {uploading ? (
                    <div
                      className="absolute inset-0 flex items-center justify-center pointer-events-none"
                      aria-hidden="true"
                    >
                      <svg width="36" height="36" viewBox="0 0 36 36" className="-rotate-90">
                        <circle
                          cx="18"
                          cy="18"
                          r={radius}
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="3"
                          className="text-background/70"
                        />
                        <circle
                          cx="18"
                          cy="18"
                          r={radius}
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="3"
                          strokeLinecap="round"
                          strokeDasharray={circumference}
                          strokeDashoffset={dashOffset}
                          className="text-primary"
                        />
                      </svg>
                    </div>
                  ) : null}
                  {failed ? (
                    <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                      <span className="rounded bg-destructive/90 px-1 text-[10px] text-destructive-foreground">
                        !
                      </span>
                    </div>
                  ) : null}
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="-top-2 -right-2 absolute bg-card shadow rounded-full w-8 h-8"
                    onClick={() => onRemoveAttachment(index)}
                    aria-label={t("common_delete")}
                  >
                    <X aria-hidden="true" className="w-3 h-3" />
                  </Button>
                </div>
              )
            })}
          </div>
        ) : null}
        <div
          className={cn(
            "flex min-w-0 justify-between gap-2",
            centered ? "h-13 items-center p-2" : "items-end"
          )}
        >
          <div className="flex min-w-0 items-center gap-1">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="hidden"
              onChange={handleFilesSelected}
              disabled={readOnly}
            />
            {hasPrompts && onInsertPromptRequest ? (
              <DropdownMenu modal={false}>
                <DropdownMenuTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-9 shrink-0 text-muted-foreground"
                    disabled={readOnly}
                    aria-label={t("chat_add_files")}
                  >
                    <Plus aria-hidden="true" className="size-4" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="start" className="w-52">
                  <DropdownMenuItem
                    className="cursor-pointer"
                    onClick={handlePickFiles}
                  >
                    {t("chat_add_files")}
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    className="cursor-pointer"
                    onClick={onInsertPromptRequest}
                  >
                    {t("prompt_insert")}
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            ) : (
              <Button
                variant="ghost"
                size="icon"
                className="size-9 shrink-0 text-muted-foreground"
                onClick={handlePickFiles}
                disabled={readOnly}
                aria-label={t("chat_add_files")}
              >
                <Plus aria-hidden="true" className="size-4" />
              </Button>
            )}
          </div>
          <div className="flex min-w-0 shrink items-center gap-1">
            {showModelSelect && modelSelect && !(loading && !canSend) ? (
              <div className="min-w-0 max-w-54 overflow-hidden">
                {modelSelect}
              </div>
            ) : null}
            {loading && !canSend ? (
              <Button
                variant="destructive"
                size="icon"
                className="size-9 shrink-0"
                onClick={onStop}
                aria-label={stopLabel}
              >
                <Square aria-hidden="true" className="size-3.5 fill-current" />
              </Button>
            ) : centered && !canSend ? (
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="size-9 shrink-0"
                onClick={() => inputRef?.current?.focus()}
                aria-label={t("chat_voice_input")}
              >
                <span
                  aria-hidden="true"
                  className="size-4 figma-icon"
                  style={{ maskImage: "url('/icon-microphone.svg')" }}
                />
              </Button>
            ) : (
              <Button
                size="icon"
                className="size-9 shrink-0"
                variant={canSend ? "default" : "secondary"}
                onClick={onSend}
                disabled={!canSend}
                aria-label={sendLabel}
              >
                <ArrowUp aria-hidden="true" />
              </Button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
