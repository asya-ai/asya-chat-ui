import {
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type ReactNode,
  type Ref,
  type PointerEvent as ReactPointerEvent,
} from "react"

import type { ChatMessageAttachmentInput, Prompt } from "@/lib/types"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Separator } from "@/components/ui/separator"
import { Textarea } from "@/components/ui/textarea"
import { ArrowUp, Plus, Square, X } from "lucide-react"
import { useI18n } from "@/lib/i18n-context"
import { getSlashPromptTrigger, shouldSubmitOnEnter } from "@/lib/chat-input"
import {
  filterSlashCommands,
  type ComposerSlashCommand,
} from "@/lib/composer-slash-commands"
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

type SlashMenuItem =
  | { kind: "command"; command: ComposerSlashCommand }
  | { kind: "prompt"; prompt: Prompt }

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
  prompts?: Prompt[]
  slashCommands?: ComposerSlashCommand[]
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
  prompts = [],
  slashCommands = [],
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
  const [cursorPosition, setCursorPosition] = useState(0)
  const [slashSelectedIndex, setSlashSelectedIndex] = useState(0)
  const slashListRef = useRef<HTMLDivElement | null>(null)
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

  const updateCursorFromTextarea = (el: HTMLTextAreaElement) => {
    setCursorPosition(el.selectionStart ?? 0)
  }

  const slashTrigger = useMemo(() => {
    if (readOnly || (slashCommands.length === 0 && prompts.length === 0)) return null
    const value = textareaRef.current?.value ?? ""
    return getSlashPromptTrigger(value, cursorPosition)
  }, [cursorPosition, prompts.length, readOnly, slashCommands.length])

  const filteredCommands = useMemo(() => {
    if (!slashTrigger) return []
    return filterSlashCommands(slashCommands, slashTrigger.query)
  }, [slashCommands, slashTrigger])

  const filteredPrompts = useMemo(() => {
    if (!slashTrigger) return []
    const needle = slashTrigger.query.trim().toLowerCase()
    if (!needle) return prompts
    return prompts.filter(
      (prompt) =>
        prompt.name.toLowerCase().includes(needle) ||
        (prompt.description ?? "").toLowerCase().includes(needle)
    )
  }, [prompts, slashTrigger])

  const slashMenuItems = useMemo((): SlashMenuItem[] => {
    const items: SlashMenuItem[] = filteredCommands.map((command) => ({
      kind: "command",
      command,
    }))
    for (const prompt of filteredPrompts) {
      items.push({ kind: "prompt", prompt })
    }
    return items
  }, [filteredCommands, filteredPrompts])

  const slashMenuOpen = slashTrigger !== null

  useEffect(() => {
    setSlashSelectedIndex(0)
  }, [slashTrigger?.query])

  useEffect(() => {
    if (!slashMenuOpen || slashMenuItems.length === 0) return
    slashListRef.current
      ?.querySelector('[data-selected="true"]')
      ?.scrollIntoView({ block: "nearest" })
  }, [slashMenuItems.length, slashMenuOpen, slashSelectedIndex])

  const insertSlashReplacement = (replacement: string) => {
    if (!slashTrigger) return
    const el = textareaRef.current
    if (!el) return
    const current = el.value
    const next =
      current.slice(0, slashTrigger.start) + replacement + current.slice(cursorPosition)
    el.value = next
    syncDraft(next)
    const cursor = slashTrigger.start + replacement.length
    requestAnimationFrame(() => {
      el.focus()
      el.setSelectionRange(cursor, cursor)
      setCursorPosition(cursor)
    })
  }

  const selectSlashItem = (item: SlashMenuItem) => {
    if (item.kind === "command") {
      item.command.onSelect?.()
      insertSlashReplacement(item.command.insertText)
      return
    }
    insertSlashReplacement(item.prompt.body)
  }

  const dismissSlashMenu = () => {
    if (!slashTrigger) return
    const el = textareaRef.current
    if (!el) return
    const current = el.value
    const next = current.slice(0, slashTrigger.start) + current.slice(cursorPosition)
    el.value = next
    syncDraft(next)
    requestAnimationFrame(() => {
      el.focus()
      el.setSelectionRange(slashTrigger.start, slashTrigger.start)
      setCursorPosition(slashTrigger.start)
    })
  }

  const handleTextareaChange = (event: ChangeEvent<HTMLTextAreaElement>) => {
    syncDraft(event.target.value)
    updateCursorFromTextarea(event.target)
  }

  const canSend = !readOnly && (hasText || pendingAttachments.length > 0)

  const handleTextareaKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (slashMenuOpen && slashMenuItems.length > 0) {
      if (event.key === "ArrowDown") {
        event.preventDefault()
        setSlashSelectedIndex((index) => Math.min(index + 1, slashMenuItems.length - 1))
        return
      }
      if (event.key === "ArrowUp") {
        event.preventDefault()
        setSlashSelectedIndex((index) => Math.max(index - 1, 0))
        return
      }
      if (event.key === "Enter" || event.key === "Tab") {
        event.preventDefault()
        const item = slashMenuItems[slashSelectedIndex]
        if (item) selectSlashItem(item)
        return
      }
    }

    if (slashMenuOpen && event.key === "Escape") {
      event.preventDefault()
      dismissSlashMenu()
      return
    }

    if (!shouldSubmitOnEnter(event)) return

    event.preventDefault()
    if (canSend) {
      onSend()
    }
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
          className={cn("relative", !centered && "shrink-0")}
        >
          {slashMenuOpen ? (
            <div
              ref={slashListRef}
              className="absolute bottom-full left-0 z-50 mb-1 flex max-h-60 w-full flex-col overflow-y-auto rounded-md border bg-popover p-1 shadow-md"
              role="listbox"
              aria-label={t("slash_menu_label")}
            >
              {slashMenuItems.length === 0 ? (
                <p className="px-3 py-2 text-muted-foreground text-sm">{t("prompt_no_results")}</p>
              ) : (
                slashMenuItems.map((item, index) => {
                  const showPromptHeader =
                    item.kind === "prompt" &&
                    index > 0 &&
                    slashMenuItems[index - 1]?.kind === "command"
                  return (
                    <div key={item.kind === "command" ? item.command.id : item.prompt.id}>
                      {showPromptHeader ? (
                        <Separator className="my-1" />
                      ) : null}
                      <button
                        type="button"
                        role="option"
                        aria-selected={index === slashSelectedIndex}
                        data-selected={index === slashSelectedIndex ? "true" : undefined}
                        className={cn(
                          "flex w-full flex-col items-start gap-0.5 rounded-sm px-3 py-2 text-left text-sm",
                          index === slashSelectedIndex && "bg-accent"
                        )}
                        onMouseDown={(event) => event.preventDefault()}
                        onClick={() => selectSlashItem(item)}
                        onMouseEnter={() => setSlashSelectedIndex(index)}
                      >
                        {item.kind === "command" ? (
                          <>
                            <span className="w-full truncate font-medium">
                              <span className="text-muted-foreground">/</span>
                              {item.command.name}
                            </span>
                            <span className="w-full truncate text-muted-foreground text-xs">
                              {item.command.description}
                            </span>
                          </>
                        ) : (
                          <>
                            <span className="w-full truncate font-medium">{item.prompt.name}</span>
                            {item.prompt.description ? (
                              <span className="w-full truncate text-muted-foreground text-xs">
                                {item.prompt.description}
                              </span>
                            ) : null}
                          </>
                        )}
                      </button>
                    </div>
                  )
                })
              )}
            </div>
          ) : null}
          <Textarea
            ref={assignTextareaRef}
            defaultValue=""
            onChange={handleTextareaChange}
            onKeyDown={handleTextareaKeyDown}
            onKeyUp={(event) => updateCursorFromTextarea(event.currentTarget)}
            onClick={(event) => updateCursorFromTextarea(event.currentTarget)}
            onSelect={(event) => updateCursorFromTextarea(event.currentTarget)}
            onPaste={onPasteAttachments}
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
