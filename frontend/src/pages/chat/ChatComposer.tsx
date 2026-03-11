import { useRef } from "react"

import type { ChatMessageAttachmentInput } from "@/lib/types"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Plus, X, Brain, Globe, SquareTerminal } from "lucide-react"
import { useI18n } from "@/lib/i18n-context"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"

type ChatComposerProps = {
  message: string
  placeholder: string
  loading: boolean
  isDragActive: boolean
  pendingAttachments: ChatMessageAttachmentInput[]
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

export const ChatComposer = ({
  message,
  placeholder,
  loading,
  isDragActive,
  pendingAttachments,
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

  const canSend = Boolean(message.trim() || pendingAttachments.length > 0)

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
    <div className="p-4 border-t">
      <div
        className={`space-y-3 rounded-md ${isDragActive ? "ring-2 ring-primary/40 ring-offset-2 ring-offset-background" : ""}`}
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
            if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
              event.preventDefault()
              if (!loading && message.trim()) {
                onSend()
              }
            }
          }}
          placeholder={placeholder}
          rows={3}
          className="max-h-48 overflow-y-auto"
          disabled={loading}
        />
        {pendingAttachments.length > 0 ? (
          <div className="flex flex-wrap gap-2">
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
                    className="-top-2 -right-2 absolute bg-background shadow rounded-full w-6 h-6"
                    onClick={() => onRemoveAttachment(index)}
                  >
                    <X className="w-3 h-3" />
                  </Button>
                </div>
              )
            })}
          </div>
        ) : null}
        <div className="flex justify-between items-center">
          <div className="flex items-center gap-2">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="hidden"
              onChange={handleFilesSelected}
              disabled={loading}
            />
            <Button
              variant="ghost"
              size="icon"
              onClick={handlePickFiles}
              disabled={loading}
              title={t("chat_add_files")}
            >
              <Plus className="w-5 h-5" />
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="ghost"
                  size="sm"
                  className={`gap-2 ${reasoningEffort ? "text-primary bg-primary/10" : "text-muted-foreground"}`}
                  title={t("chat_reasoning_effort")}
                  disabled={loading}
                >
                  <Brain className="w-4 h-4" />
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
              className={`gap-2 ${
                webSearchEnabled ? "text-primary bg-primary/10" : "text-muted-foreground"
              }`}
              title={
                webSearchEnabled ? t("chat_web_search_on") : t("chat_web_search_off")
              }
              disabled={loading}
              onClick={() => onWebSearchEnabledChange(!webSearchEnabled)}
            >
              <Globe className="w-4 h-4" />
              <span className="text-xs">{t("chat_web_search")}</span>
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className={`gap-2 ${
                codeExecutionEnabled ? "text-primary bg-primary/10" : "text-muted-foreground"
              }`}
              title={
                codeExecutionEnabled
                  ? t("chat_code_execution_on")
                  : t("chat_code_execution_off")
              }
              disabled={loading}
              onClick={() => onCodeExecutionEnabledChange(!codeExecutionEnabled)}
            >
              <SquareTerminal className="w-4 h-4" />
              <span className="text-xs">{t("org_code_execution")}</span>
            </Button>
          </div>
          <div className="flex items-center gap-2">
            {loading ? (
              <Button variant="destructive" size="sm" onClick={onStop}>
                {stopLabel}
              </Button>
            ) : (
              <Button size="sm" onClick={onSend} disabled={!canSend}>
                {sendLabel}
              </Button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
