import { useRef } from "react"

import type { ChatMessageAttachmentInput } from "@/lib/types"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Paperclip, X, Brain, Globe, SquareTerminal } from "lucide-react"
import { useI18n } from "@/lib/i18n-context"
import { shouldSubmitOnEnter } from "@/lib/chat-input"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { cn } from "@/lib/utils"

type ChatComposerProps = {
  message: string
  placeholder: string
  loading: boolean
  readOnly?: boolean
  isDragActive: boolean
  pendingAttachments: ChatMessageAttachmentInput[]
  attachmentError?: string | null
  reasoningEffort: string | null
  webSearchEnabled: boolean
  codeExecutionEnabled: boolean
  inputRef?: React.RefObject<HTMLTextAreaElement | null>
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
  onReasoningEffortChange: (effort: string | null) => void
  onWebSearchEnabledChange: (enabled: boolean) => void
  onCodeExecutionEnabledChange: (enabled: boolean) => void
  sendLabel: string
  stopLabel: string
}

const toolToggleClass = (active: boolean) =>
  cn(
    "gap-1.5 h-7 rounded-lg px-2 text-muted-foreground hover:text-foreground",
    active && "bg-secondary text-foreground"
  )

export const ChatComposer = ({
  message,
  placeholder,
  loading,
  readOnly = false,
  isDragActive,
  pendingAttachments,
  attachmentError,
  reasoningEffort,
  webSearchEnabled,
  codeExecutionEnabled,
  inputRef,
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
  onReasoningEffortChange,
  onWebSearchEnabledChange,
  onCodeExecutionEnabledChange,
  sendLabel,
  stopLabel,
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

  const reasoningLevels = [
    { value: null, label: t("chat_reasoning_default") },
    { value: "low", label: t("chat_reasoning_low") },
    { value: "medium", label: t("chat_reasoning_medium") },
    { value: "high", label: t("chat_reasoning_high") },
  ]

  const currentReasoningLabel =
    reasoningLevels.find((l) => l.value === reasoningEffort)?.label ??
    t("chat_reasoning_default")

  return (
    <div className="px-4 pt-2 pb-[calc(1rem+env(safe-area-inset-bottom))]">
      <div
        className={cn(
          "flex flex-col justify-between gap-2 rounded-lg border border-border bg-card p-2",
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
          rows={2}
          className={cn(
            "max-h-48 min-h-[52px] resize-none overflow-y-auto",
            "border-0 bg-transparent px-1.5 py-1 text-sm shadow-none",
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
        <div className="flex items-end justify-between gap-2">
          <div className="flex min-w-0 flex-wrap items-center gap-0.5">
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
              className="size-7 text-muted-foreground"
              onClick={handlePickFiles}
              disabled={loading || readOnly}
              aria-label={t("chat_add_files")}
            >
              <Paperclip aria-hidden="true" className="size-4" />
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="ghost"
                  size="sm"
                  className={toolToggleClass(Boolean(reasoningEffort))}
                  title={t("chat_reasoning_effort")}
                  disabled={loading || readOnly}
                >
                  <Brain aria-hidden="true" className="size-3.5" />
                  <span className="text-xs">{currentReasoningLabel}</span>
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start">
                {reasoningLevels.map((level) => (
                  <DropdownMenuItem
                    key={level.value ?? "default"}
                    onClick={() => onReasoningEffortChange(level.value)}
                    className={reasoningEffort === level.value ? "bg-accent" : ""}
                  >
                    {level.label}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
            <Button
              variant="ghost"
              size="sm"
              className={toolToggleClass(webSearchEnabled)}
              title={
                webSearchEnabled ? t("chat_web_search_on") : t("chat_web_search_off")
              }
              disabled={loading || readOnly}
              onClick={() => onWebSearchEnabledChange(!webSearchEnabled)}
            >
              <Globe aria-hidden="true" className="size-3.5" />
              <span className="text-xs">{t("chat_web_search")}</span>
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className={toolToggleClass(codeExecutionEnabled)}
              title={
                codeExecutionEnabled
                  ? t("chat_code_execution_on")
                  : t("chat_code_execution_off")
              }
              disabled={loading || readOnly}
              onClick={() => onCodeExecutionEnabledChange(!codeExecutionEnabled)}
            >
              <SquareTerminal aria-hidden="true" className="size-3.5" />
              <span className="text-xs">{t("org_code_execution")}</span>
            </Button>
          </div>
          <div className="shrink-0">
            {loading ? (
              <Button variant="destructive" size="sm" className="h-9" onClick={onStop}>
                {stopLabel}
              </Button>
            ) : (
              <Button
                size="sm"
                className="h-9"
                variant={canSend ? "default" : "secondary"}
                onClick={onSend}
                disabled={!canSend}
              >
                {sendLabel}
              </Button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
