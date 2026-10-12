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
} from "react"

import type { ChatMessageAttachmentInput, McpPrompt, Prompt } from "@/lib/types"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Separator } from "@/components/ui/separator"
import { Textarea } from "@/components/ui/textarea"
import { ArrowUp, Check, ChevronDown, Maximize2, Minimize2, Plus, Square, X } from "lucide-react"
import { useI18n } from "@/lib/i18n-context"
import {
  getSlashPromptTrigger,
  shouldSubmitOnEnter,
  type SlashPromptTrigger,
} from "@/lib/chat-input"
import {
  filterSlashCommands,
  type ComposerSlashCommand,
} from "@/lib/composer-slash-commands"
import { composerTextareaHeightStore } from "@/lib/storage"
import { cn } from "@/lib/utils"

const getMaxComposerTextareaHeight = (
  maxHeightVh = composerTextareaHeightStore.maxHeightVh
) => {
  if (typeof window === "undefined") {
    return Math.round(800 * maxHeightVh)
  }
  return Math.round(window.innerHeight * maxHeightVh)
}

const clampComposerTextareaHeight = (
  px: number,
  maxHeightVh = composerTextareaHeightStore.maxHeightVh
) => {
  const max = getMaxComposerTextareaHeight(maxHeightVh)
  return Math.min(max, Math.max(composerTextareaHeightStore.min, Math.round(px)))
}

export type ChatComposerHandle = {
  getValue: () => string
  setValue: (next: string) => void
}

type SlashMenuItem =
  | { kind: "command"; command: ComposerSlashCommand }
  | { kind: "prompt"; prompt: Prompt }
  | { kind: "mcp_prompt"; prompt: McpPrompt }

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
  mcpPrompts?: McpPrompt[]
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
  onSelectMcpPrompt?: (prompt: McpPrompt) => void
  onDraftChange?: (value: string) => void
  sendLabel: string
  stopLabel: string
  welcomeTitle: string
  projects?: Array<{ id: string; name: string }>
  selectedProjectId?: string | null
  onSelectProject?: (projectId: string | null) => void
  centered?: boolean
  /** Keep centered layout on narrow viewports (embed). */
  pinCentered?: boolean
  centeredLeading?: ReactNode
  centeredExtras?: ReactNode
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
  mcpPrompts = [],
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
  onSelectMcpPrompt,
  onDraftChange,
  sendLabel,
  stopLabel,
  welcomeTitle,
  projects = [],
  selectedProjectId = null,
  onSelectProject,
  centered = false,
  pinCentered = false,
  centeredLeading,
  centeredExtras,
}: ChatComposerProps) => {
  const { t } = useI18n()
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)
  const heightPxRef = useRef(composerTextareaHeightStore.min)
  const expandedRef = useRef(false)
  const cursorPositionRef = useRef(0)
  const growRafRef = useRef<number | null>(null)
  const [hasText, setHasText] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const [showExpandButton, setShowExpandButton] = useState(false)
  const [slashTrigger, setSlashTrigger] = useState<SlashPromptTrigger | null>(null)
  const [slashSelectedIndex, setSlashSelectedIndex] = useState(0)
  const slashListRef = useRef<HTMLDivElement | null>(null)
  const onDraftChangeRef = useRef(onDraftChange)
  onDraftChangeRef.current = onDraftChange

  const applyHeight = useCallback((next: number, maxHeightVh?: number) => {
    const clamped = clampComposerTextareaHeight(next, maxHeightVh)
    if (heightPxRef.current !== clamped) {
      heightPxRef.current = clamped
    }
    const el = textareaRef.current
    if (el && el.style.height !== `${clamped}px`) {
      el.style.height = `${clamped}px`
    }
    return clamped
  }, [])

  const syncAutoGrowHeight = useCallback((nextExpanded = expandedRef.current) => {
    const el = textareaRef.current
    if (!el) return
    if (nextExpanded) {
      applyHeight(
        getMaxComposerTextareaHeight(composerTextareaHeightStore.maxHeightVh),
        composerTextareaHeightStore.maxHeightVh
      )
      setShowExpandButton((prev) => (prev ? prev : true))
      return
    }
    const previousHeight = el.style.height
    el.style.height = "0px"
    const contentHeight = el.scrollHeight
    el.style.height = previousHeight
    const autoMax = getMaxComposerTextareaHeight(
      composerTextareaHeightStore.autoGrowMaxHeightVh
    )
    const nextShow = contentHeight > autoMax
    setShowExpandButton((prev) => (prev === nextShow ? prev : nextShow))
    applyHeight(
      Math.max(composerTextareaHeightStore.min, contentHeight),
      composerTextareaHeightStore.autoGrowMaxHeightVh
    )
  }, [applyHeight])

  const scheduleAutoGrowHeight = useCallback(() => {
    if (growRafRef.current != null) cancelAnimationFrame(growRafRef.current)
    growRafRef.current = requestAnimationFrame(() => {
      growRafRef.current = null
      syncAutoGrowHeight()
    })
  }, [syncAutoGrowHeight])

  const resetHeightToDefault = useCallback(() => {
    expandedRef.current = false
    setExpanded(false)
    setShowExpandButton((prev) => (prev ? false : prev))
    applyHeight(
      composerTextareaHeightStore.min,
      composerTextareaHeightStore.autoGrowMaxHeightVh
    )
  }, [applyHeight])

  const toggleExpanded = () => {
    const next = !expandedRef.current
    expandedRef.current = next
    setExpanded(next)
    syncAutoGrowHeight(next)
  }

  const refreshSlashTrigger = useCallback(
    (cursor = cursorPositionRef.current) => {
      cursorPositionRef.current = cursor
      if (
        readOnly ||
        (slashCommands.length === 0 && prompts.length === 0 && mcpPrompts.length === 0)
      ) {
        setSlashTrigger((prev) => (prev === null ? prev : null))
        return
      }
      const value = textareaRef.current?.value ?? ""
      const next = getSlashPromptTrigger(value, cursor)
      setSlashTrigger((prev) => {
        if (prev == null && next == null) return prev
        if (prev && next && prev.start === next.start && prev.query === next.query) {
          return prev
        }
        return next
      })
    },
    [mcpPrompts.length, prompts.length, readOnly, slashCommands.length]
  )

  const assignTextareaRef = useCallback(
    (el: HTMLTextAreaElement | null) => {
      textareaRef.current = el
      if (inputRef) inputRef.current = el
      if (el) {
        el.style.height = `${heightPxRef.current}px`
        scheduleAutoGrowHeight()
      }
    },
    [inputRef, scheduleAutoGrowHeight]
  )

  const syncDraft = (next: string) => {
    const nextHasText = next.trim().length > 0
    setHasText((prev) => (prev === nextHasText ? prev : nextHasText))
    onDraftChangeRef.current?.(next)
    if (!nextHasText) {
      resetHeightToDefault()
      return
    }
    scheduleAutoGrowHeight()
  }

  useEffect(() => {
    const onViewportResize = () => {
      syncAutoGrowHeight()
    }
    window.addEventListener("resize", onViewportResize)
    return () => window.removeEventListener("resize", onViewportResize)
  }, [syncAutoGrowHeight])

  useEffect(() => {
    return () => {
      if (growRafRef.current != null) cancelAnimationFrame(growRafRef.current)
    }
  }, [])

  const updateCursorFromTextarea = (el: HTMLTextAreaElement) => {
    refreshSlashTrigger(el.selectionStart ?? 0)
  }

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

  const filteredMcpPrompts = useMemo(() => {
    if (!slashTrigger) return []
    const needle = slashTrigger.query.trim().toLowerCase()
    if (!needle) return mcpPrompts
    return mcpPrompts.filter(
      (prompt) =>
        prompt.name.toLowerCase().includes(needle) ||
        prompt.server_name.toLowerCase().includes(needle) ||
        (prompt.description ?? "").toLowerCase().includes(needle)
    )
  }, [mcpPrompts, slashTrigger])

  const slashMenuItems = useMemo((): SlashMenuItem[] => {
    const items: SlashMenuItem[] = filteredCommands.map((command) => ({
      kind: "command",
      command,
    }))
    for (const prompt of filteredPrompts) {
      items.push({ kind: "prompt", prompt })
    }
    for (const prompt of filteredMcpPrompts) {
      items.push({ kind: "mcp_prompt", prompt })
    }
    return items
  }, [filteredCommands, filteredMcpPrompts, filteredPrompts])

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
    const cursor = cursorPositionRef.current
    const next =
      current.slice(0, slashTrigger.start) + replacement + current.slice(cursor)
    el.value = next
    syncDraft(next)
    const nextCursor = slashTrigger.start + replacement.length
    requestAnimationFrame(() => {
      el.focus()
      el.setSelectionRange(nextCursor, nextCursor)
      refreshSlashTrigger(nextCursor)
    })
  }

  const clearSlashTrigger = () => {
    if (!slashTrigger) return
    const el = textareaRef.current
    if (!el) return
    const current = el.value
    const cursor = cursorPositionRef.current
    const next = current.slice(0, slashTrigger.start) + current.slice(cursor)
    el.value = next
    syncDraft(next)
    requestAnimationFrame(() => {
      el.focus()
      el.setSelectionRange(slashTrigger.start, slashTrigger.start)
      refreshSlashTrigger(slashTrigger.start)
    })
  }

  const selectSlashItem = (item: SlashMenuItem) => {
    if (item.kind === "command") {
      item.command.onSelect?.()
      insertSlashReplacement(item.command.insertText)
      return
    }
    if (item.kind === "mcp_prompt") {
      clearSlashTrigger()
      onSelectMcpPrompt?.(item.prompt)
      return
    }
    insertSlashReplacement(item.prompt.body)
  }

  const dismissSlashMenu = () => {
    clearSlashTrigger()
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

  const composerCard = (
      <div
        className={cn(
          "@container/composer flex w-full max-w-(--chat-content-width) flex-col justify-between rounded-2xl border border-border bg-card",
          centered
            ? "min-h-26 shrink-0 gap-0 p-0 shadow-[0_25px_50px_-12px_rgba(7,12,28,0.21)]"
            : "gap-2 p-2 shadow-none",
          "transition-[box-shadow,border-color]",
          "focus-within:border-primary/40 focus-within:shadow-[0_0_0_3px] focus-within:shadow-primary/30",
          isDragActive && "border-primary/50 shadow-[0_0_0_3px] shadow-primary/30"
        )}
        onDragEnter={onDragEnter}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
      >
        <div className={cn("relative", !centered && "shrink-0")}>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            tabIndex={showExpandButton ? 0 : -1}
            aria-hidden={!showExpandButton}
            className={cn(
              "top-2 right-2 z-10 absolute bg-muted hover:bg-muted rounded-lg size-9 text-muted-foreground hover:text-foreground",
              showExpandButton
                ? "opacity-100"
                : "pointer-events-none opacity-0"
            )}
            onClick={toggleExpanded}
            aria-label={expanded ? t("composer_collapse") : t("composer_expand")}
            aria-pressed={expanded}
          >
            {expanded ? (
              <Minimize2 aria-hidden="true" className="size-4" />
            ) : (
              <Maximize2 aria-hidden="true" className="size-4" />
            )}
          </Button>
          {slashMenuOpen ? (
            <div
              ref={slashListRef}
              className="bottom-full left-0 z-50 absolute flex flex-col bg-popover shadow-md mb-1 p-1 border rounded-md w-full max-h-60 overflow-y-auto"
              role="listbox"
              aria-label={t("slash_menu_label")}
            >
              {slashMenuItems.length === 0 ? (
                <p className="px-3 py-2 text-muted-foreground text-sm">{t("prompt_no_results")}</p>
              ) : (
                slashMenuItems.map((item, index) => {
                  const prev = slashMenuItems[index - 1]
                  const showPromptHeader =
                    item.kind === "prompt" &&
                    index > 0 &&
                    prev?.kind === "command"
                  const showMcpHeader =
                    item.kind === "mcp_prompt" &&
                    (index === 0 || prev?.kind !== "mcp_prompt")
                  const itemKey =
                    item.kind === "command"
                      ? item.command.id
                      : item.kind === "prompt"
                        ? item.prompt.id
                        : `mcp:${item.prompt.id}`
                  return (
                    <div key={itemKey}>
                      {showPromptHeader || showMcpHeader ? (
                        <Separator className="my-1" />
                      ) : null}
                      <button
                        type="button"
                        role="option"
                        aria-selected={index === slashSelectedIndex}
                        data-selected={index === slashSelectedIndex ? "true" : undefined}
                        className={cn(
                          "flex flex-col items-start gap-0.5 px-3 py-2 rounded-sm w-full text-sm text-left",
                          index === slashSelectedIndex && "bg-accent"
                        )}
                        onMouseDown={(event) => event.preventDefault()}
                        onClick={() => selectSlashItem(item)}
                        onMouseEnter={() => setSlashSelectedIndex(index)}
                      >
                        {item.kind === "command" ? (
                          <>
                            <span className="w-full font-medium truncate">
                              <span className="text-muted-foreground">/</span>
                              {item.command.name}
                            </span>
                            <span className="w-full text-muted-foreground text-xs truncate">
                              {item.command.description}
                            </span>
                          </>
                        ) : item.kind === "mcp_prompt" ? (
                          <>
                            <span className="w-full font-medium truncate">{item.prompt.name}</span>
                            <span className="w-full text-muted-foreground text-xs truncate">
                              {item.prompt.description
                                ? `${item.prompt.server_name} · ${item.prompt.description}`
                                : item.prompt.server_name}
                            </span>
                          </>
                        ) : (
                          <>
                            <span className="w-full font-medium truncate">{item.prompt.name}</span>
                            {item.prompt.description ? (
                              <span className="w-full text-muted-foreground text-xs truncate">
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
            rows={1}
            style={{ height: heightPxRef.current }}
            className={cn(
              "bg-transparent shadow-none border-0 overflow-y-auto text-base resize-none",
              "field-sizing-fixed min-h-0",
              centered
                ? "-mx-px -mt-px w-[calc(100%+2px)] py-4 pl-5 pr-12 leading-5"
                : "py-1 pl-1.5 pr-12",
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
                      className="absolute inset-0 flex justify-center items-center pointer-events-none"
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
                    <div className="absolute inset-0 flex justify-center items-center pointer-events-none">
                      <span className="bg-destructive/90 px-1 rounded text-[10px] text-destructive-foreground">
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
            "flex justify-between gap-2 min-w-0",
            centered ? "h-13 items-center p-2" : "items-end"
          )}
        >
          <div className="flex items-center gap-1 min-w-0">
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
                    className="size-9 text-muted-foreground shrink-0"
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
                className="size-9 text-muted-foreground shrink-0"
                onClick={handlePickFiles}
                disabled={readOnly}
                aria-label={t("chat_add_files")}
              >
                <Plus aria-hidden="true" className="size-4" />
              </Button>
            )}
          </div>
          <div className="flex items-center gap-1 min-w-0 shrink">
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
                <Square aria-hidden="true" className="fill-current size-3.5" />
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
  )

  const poweredByNote = (
    <p className="text-muted-foreground text-xs text-center leading-4">
      <a
        href="https://github.com/asya-ai/asya-chat-ui"
        target="_blank"
        rel="noreferrer"
        className="hover:text-foreground underline underline-offset-2"
      >
        {t("chat_powered_by_prefix")}
      </a>{" "}
      {t("chat_powered_by_mid")}{" "}
      <a
        href="https://www.asya.ai"
        target="_blank"
        rel="noreferrer"
        className="hover:text-foreground underline underline-offset-2"
      >
        www.asya.ai
      </a>
    </p>
  )

  return (
    <div
      className={cn(
        "z-10",
        centered
          ? cn(
              "bottom-[calc(1rem+env(safe-area-inset-bottom))] absolute inset-x-0 flex flex-col items-center px-4 min-h-0 overflow-y-auto",
              pinCentered ? "top-0 py-4" : "top-15"
            )
          : "mx-auto w-[min(var(--chat-content-width),calc(100%-2rem))] shrink-0 pb-[calc(1rem+env(safe-area-inset-bottom))]"
      )}
    >
      {centered ? (
        <>
          <div
            className={cn(
              "flex flex-col flex-1 items-center min-h-0 w-full",
              // Desktop: center tree + composer as one stack. Mobile: tree fills leftover space.
              "md:justify-center md:gap-24"
            )}
          >
            {centeredLeading ? (
              <div
                className={cn(
                  "flex justify-center items-center w-full min-h-0",
                  "flex-1 py-4 md:flex-none md:py-0"
                )}
              >
                {centeredLeading}
              </div>
            ) : null}
            <div className="flex w-full max-w-(--chat-content-width) flex-col gap-8 shrink-0">
              {projects.length > 0 && onSelectProject ? (
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <button
                      type="button"
                      className={cn(
                        "group flex items-center gap-2 mx-auto outline-none max-w-full text-center",
                        "focus-visible:ring-2 focus-visible:ring-ring/40 rounded-lg",
                        !pinCentered && "max-md:hidden"
                      )}
                      aria-label={t("chat_welcome_project_select")}
                    >
                      <span className="min-w-0 font-heading font-normal text-foreground text-4xl truncate leading-10">
                        {welcomeTitle}
                      </span>
                      <ChevronDown
                        aria-hidden="true"
                        className="size-4 text-muted-foreground group-data-[state=open]:rotate-180 transition-transform shrink-0"
                      />
                    </button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent
                    side="top"
                    align="center"
                    sideOffset={8}
                    className="shadow-[0_0_0_3px] shadow-primary/10 p-2 border-border rounded-2xl w-[min(22.5rem,calc(100vw-2rem))]"
                  >
                    <DropdownMenuItem
                      className={cn(
                        "gap-2.5 px-3 py-3 rounded-lg font-medium text-lg cursor-pointer",
                        !selectedProjectId && "bg-accent"
                      )}
                      onSelect={() => onSelectProject(null)}
                    >
                      <span
                        aria-hidden="true"
                        className="size-4.5 figma-icon shrink-0"
                        style={{ maskImage: "url('/icon-projects.svg')" }}
                      />
                      <span className="flex-1 min-w-0 truncate">{t("chat_welcome_default")}</span>
                      {!selectedProjectId ? (
                        <Check className="size-4.5 text-foreground shrink-0" aria-hidden="true" />
                      ) : null}
                    </DropdownMenuItem>
                    {projects.map((project) => {
                      const selected = project.id === selectedProjectId
                      return (
                        <DropdownMenuItem
                          key={project.id}
                          className={cn(
                            "gap-2.5 px-3 py-3 rounded-lg font-medium text-lg cursor-pointer",
                            selected && "bg-accent"
                          )}
                          onSelect={() => onSelectProject(project.id)}
                        >
                          <span
                            aria-hidden="true"
                            className="size-4.5 figma-icon shrink-0"
                            style={{ maskImage: "url('/icon-projects.svg')" }}
                          />
                          <span className="flex-1 min-w-0 truncate">{project.name}</span>
                          {selected ? (
                            <Check
                              className="size-4.5 text-foreground shrink-0"
                              aria-hidden="true"
                            />
                          ) : null}
                        </DropdownMenuItem>
                      )
                    })}
                  </DropdownMenuContent>
                </DropdownMenu>
              ) : (
                <h2
                  className={cn(
                    "w-full font-heading font-normal text-foreground text-4xl text-center leading-10 shrink-0",
                    !pinCentered && "max-md:hidden"
                  )}
                >
                  {welcomeTitle}
                </h2>
              )}
              <div className="flex flex-col gap-3 w-full">
                {composerCard}
                <div className="flex items-start gap-2 w-full text-muted-foreground text-sm leading-4">
                  <span
                    aria-hidden="true"
                    className="mt-0.5 size-4 figma-icon shrink-0"
                    style={{ maskImage: "url('/icon-privacy.svg')" }}
                  />
                  <p className="flex-1 min-w-0">{t("chat_privacy_message")}</p>
                </div>
                {centeredExtras}
              </div>
            </div>
          </div>
          <div className="pt-4 w-full shrink-0">{poweredByNote}</div>
        </>
      ) : (
        <>
          {composerCard}
        </>
      )}
    </div>
  )
}
