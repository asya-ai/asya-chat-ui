import { useRef, type ReactNode } from "react"

import type { ChatMessageAttachmentInput } from "@/lib/types"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { ArrowUp, Plus, Square, X } from "lucide-react"
import { useI18n } from "@/lib/i18n-context"
import { shouldSubmitOnEnter } from "@/lib/chat-input"
import { cn } from "@/lib/utils"

type ChatComposerProps = {
  message: string
  placeholder: string
  loading: boolean
  readOnly?: boolean
  isDragActive: boolean
  pendingAttachments: ChatMessageAttachmentInput[]
  attachmentError?: string | null
  webSearchEnabled: boolean
  codeExecutionEnabled: boolean
  inputRef?: React.RefObject<HTMLTextAreaElement | null>
  modelSelect?: ReactNode
  showModelSelect?: boolean
  onMessageChange: (value: string) => void
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
  onWebSearchEnabledChange: (enabled: boolean) => void
  onCodeExecutionEnabledChange: (enabled: boolean) => void
  sendLabel: string
  stopLabel: string
  welcomeTitle: string
  centered?: boolean
}

export const ChatComposer = ({
  message,
  placeholder,
  loading,
  readOnly = false,
  isDragActive,
  pendingAttachments,
  attachmentError,
  webSearchEnabled,
  codeExecutionEnabled,
  inputRef,
  modelSelect,
  showModelSelect = false,
  onMessageChange,
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
  onWebSearchEnabledChange,
  onCodeExecutionEnabledChange,
  sendLabel,
  stopLabel,
  welcomeTitle,
  centered = false,
}: ChatComposerProps) => {
  const { t } = useI18n()
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  const handlePickFiles = () => {
    fileInputRef.current?.click()
  }

  const handleFilesSelected = (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? [])
    if (files.length === 0) return
    onFilesSelected(files)
    event.target.value = ""
  }

  const canSend = !readOnly && Boolean(message.trim() || pendingAttachments.length > 0)

  return (
    <div
      className={cn(
        "z-10",
        centered
          ? "absolute left-1/2 top-1/2 flex w-(--chat-content-width) -translate-x-1/2 -translate-y-1/2 flex-col items-center gap-8 p-0 max-md:top-auto max-md:bottom-4 max-md:w-[calc(100%-2rem)] max-md:translate-y-0 max-md:gap-6"
          : "mx-auto w-[min(var(--chat-content-width),calc(100%-2rem))] pt-2 pb-[calc(1rem+env(safe-area-inset-bottom))]"
      )}
    >
      {centered ? (
        <h2 className="max-md:hidden w-full font-heading font-normal text-4xl text-center leading-10">
          {welcomeTitle}
        </h2>
      ) : null}
      <div
        className={cn(
          "flex flex-col justify-between bg-card border border-border rounded-2xl w-full",
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
          ref={inputRef}
          value={message}
          onChange={(event) => onMessageChange(event.target.value)}
          onPaste={onPasteAttachments}
          onKeyDown={(event) => {
            if (!shouldSubmitOnEnter(event)) return

            event.preventDefault()
            if (!loading && canSend) {
              onSend()
            }
          }}
          placeholder={placeholder}
          rows={centered ? 1 : 2}
          className={cn(
            "bg-transparent shadow-none border-0 overflow-y-auto text-base resize-none",
            centered
              ? "-mx-px -mt-px h-13 min-h-13 max-h-52 w-[calc(100%+2px)] px-5 py-4 leading-5"
              : "max-h-52 min-h-13 px-1.5 py-1",
            "placeholder:text-muted-foreground",
            "focus-visible:border-0 focus-visible:ring-0 dark:bg-transparent"
          )}
          disabled={loading || readOnly}
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
              return (
                <div key={`${attachment.file_name}-${index}`} className="relative">
                  {isImage ? (
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      className="p-0 rounded-md w-auto h-auto overflow-hidden"
                      onClick={() => onPreviewAttachment(attachment)}
                    >
                      <img
                        src={`data:${attachment.content_type};base64,${attachment.data_base64}`}
                        alt={attachment.file_name}
                        className="rounded-md w-16 h-16 object-cover"
                      />
                    </Button>
                  ) : (
                    <div className="px-3 py-2 border rounded-md text-xs">
                      {attachment.file_name}
                    </div>
                  )}
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
            "flex justify-between gap-2",
            centered ? "h-13 items-center p-2" : "items-end"
          )}
        >
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="hidden"
              onChange={handleFilesSelected}
              disabled={loading || readOnly}
            />
            <Button
              variant="ghost"
              size="icon"
              className="size-9 shrink-0 text-muted-foreground"
              onClick={handlePickFiles}
              disabled={loading || readOnly}
              aria-label={t("chat_add_files")}
            >
              <Plus aria-hidden="true" className="size-4" />
            </Button>
            <label
              className={cn(
                "inline-flex cursor-pointer items-center gap-2 text-sm",
                (loading || readOnly) && "pointer-events-none opacity-50"
              )}
            >
              <Switch
                size="sm"
                checked={webSearchEnabled}
                disabled={loading || readOnly}
                onCheckedChange={onWebSearchEnabledChange}
                aria-label={t("chat_web_search")}
              />
              <span className="whitespace-nowrap text-xs text-foreground/80">
                {t("chat_web_search")}
              </span>
            </label>
            <label
              className={cn(
                "inline-flex cursor-pointer items-center gap-2 text-sm",
                (loading || readOnly) && "pointer-events-none opacity-50"
              )}
            >
              <Switch
                size="sm"
                checked={codeExecutionEnabled}
                disabled={loading || readOnly}
                onCheckedChange={onCodeExecutionEnabledChange}
                aria-label={t("org_code_execution")}
              />
              <span className="whitespace-nowrap text-xs text-foreground/80">
                {t("org_code_execution")}
              </span>
            </label>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            {showModelSelect && modelSelect && !loading ? modelSelect : null}
            {loading ? (
              <Button
                variant="destructive"
                size="icon"
                className="size-9"
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
                className="size-9"
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
                className="size-9"
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
