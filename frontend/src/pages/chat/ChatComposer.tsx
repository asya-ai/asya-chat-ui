import { useRef, type ReactNode } from "react"

import type { ChatMessageAttachmentInput } from "@/lib/types"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { ArrowUp, MoreHorizontal, Plus, Square, X } from "lucide-react"
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
  const toolsDisabled = loading || readOnly

  const toolToggle = (
    label: string,
    checked: boolean,
    onCheckedChange: (enabled: boolean) => void
  ) => (
    <label
      className={cn(
        "inline-flex cursor-pointer items-center gap-2 text-sm",
        toolsDisabled && "pointer-events-none opacity-50"
      )}
    >
      <Switch
        size="sm"
        checked={checked}
        disabled={toolsDisabled}
        onCheckedChange={onCheckedChange}
        aria-label={label}
      />
      <span className="whitespace-nowrap text-xs text-foreground/80">{label}</span>
    </label>
  )

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
          "flex w-full max-w-(--chat-content-width) flex-col justify-between bg-card border border-border rounded-2xl",
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
            // Grow with content via field-sizing-content up to 75vh, then scroll.
            "min-h-13 max-h-[75vh] bg-transparent shadow-none border-0 overflow-y-auto text-base resize-none",
            centered
              ? "-mx-px -mt-px w-[calc(100%+2px)] px-5 py-4 leading-5"
              : "px-1.5 py-1",
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
          <div className="flex min-w-0 items-center gap-1">
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

            <div className="hidden min-w-0 items-center gap-2 md:flex">
              {toolToggle(
                t("chat_web_search"),
                webSearchEnabled,
                onWebSearchEnabledChange
              )}
              {toolToggle(
                t("org_code_execution"),
                codeExecutionEnabled,
                onCodeExecutionEnabledChange
              )}
            </div>

            <DropdownMenu modal={false}>
              <DropdownMenuTrigger asChild>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="size-9 shrink-0 text-muted-foreground md:hidden"
                  disabled={toolsDisabled}
                  aria-label={t("common_more")}
                >
                  <MoreHorizontal aria-hidden="true" className="size-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start" className="w-72">
                <DropdownMenuItem
                  className="cursor-pointer"
                  onSelect={(event) => event.preventDefault()}
                  onClick={() => onWebSearchEnabledChange(!webSearchEnabled)}
                >
                  <span className="flex-1">{t("chat_web_search")}</span>
                  <Switch
                    size="sm"
                    checked={webSearchEnabled}
                    onCheckedChange={onWebSearchEnabledChange}
                    aria-label={t("chat_web_search")}
                  />
                </DropdownMenuItem>
                <DropdownMenuItem
                  className="cursor-pointer"
                  onSelect={(event) => event.preventDefault()}
                  onClick={() =>
                    onCodeExecutionEnabledChange(!codeExecutionEnabled)
                  }
                >
                  <span className="flex-1">{t("org_code_execution")}</span>
                  <Switch
                    size="sm"
                    checked={codeExecutionEnabled}
                    onCheckedChange={onCodeExecutionEnabledChange}
                    aria-label={t("org_code_execution")}
                  />
                </DropdownMenuItem>
                {showModelSelect && modelSelect ? (
                  <>
                    <DropdownMenuSeparator />
                    <DropdownMenuLabel>{t("chat_select_model")}</DropdownMenuLabel>
                    <div className="px-1 pb-1 [&_[data-slot=select-trigger]]:h-9 [&_[data-slot=select-trigger]]:w-full [&_[data-slot=select-trigger]]:max-w-none">
                      {modelSelect}
                    </div>
                  </>
                ) : null}
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            {showModelSelect && modelSelect && !loading ? (
              <div className="hidden md:block">{modelSelect}</div>
            ) : null}
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
