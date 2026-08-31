import {
  useCallback,
  useImperativeHandle,
  useRef,
  useState,
  type ReactNode,
  type Ref,
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
import { cn } from "@/lib/utils"

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
  const [hasText, setHasText] = useState(false)
  const onDraftChangeRef = useRef(onDraftChange)
  onDraftChangeRef.current = onDraftChange

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
          : "mx-auto w-[min(var(--chat-content-width),calc(100%-2rem))] pb-[calc(1rem+env(safe-area-inset-bottom))]"
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
            // Grow with content via field-sizing-content up to 75vh, then scroll.
            // Cap harder on phones so the field doesn't fight the virtual keyboard.
            "min-h-13 max-h-[75vh] max-md:max-h-[36dvh] bg-transparent shadow-none border-0 overflow-y-auto text-base resize-none",
            centered
              ? "-mx-px -mt-px w-[calc(100%+2px)] px-5 py-4 leading-5"
              : "px-1.5 py-1",
            "placeholder:text-muted-foreground",
            "focus-visible:border-0 focus-visible:ring-0 dark:bg-transparent"
          )}
          disabled={readOnly}
        />
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
