import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import type { CSSProperties } from "react"
import { useLocation, useNavigate, useParams } from "react-router"
import { useQueryClient } from "@tanstack/react-query"

import { ApiError, agentApi, chatApi, configApi, promptApi } from "@/lib/api"
import { chatGenerationIndicatorsStore } from "@/lib/chat-generation-indicators"
import {
  actionInfoLevelStore,
  codeExecutionEnabledStore,
  modelStore,
  orgStore,
  webSearchEnabledStore,
  type ActionInfoLevel,
} from "@/lib/storage"
import type {
  Chat,
  Agent,
  ChatModel,
  ChatMessage,
  ChatMessageAttachmentInput,
  GenerationStatus,
  Prompt,
  SourceItem,
} from "@/lib/types"
import { useI18n } from "@/lib/i18n-context"
import { supportsImageInput, supportsImageOutput } from "@/lib/modelCapabilities"
import { ProviderIcon } from "@/components/ProviderIcon"
import { PromptFormDialog } from "@/components/PromptFormDialog"
import { getProviderIconCandidates } from "@/lib/providerIcons"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism"
import { Image as ImageIcon, Menu, PanelLeftOpen, Plus } from "lucide-react"
import { toast } from "sonner"
import { Sheet, SheetContent, SheetTrigger } from "@/components/ui/sheet"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import ChatSidebar from "@/pages/chat/ChatSidebar"
import { CoworkPanel } from "@/pages/chat/CoworkPanel"
import { useCoworkDocument } from "@/pages/chat/useCoworkDocument"
import { useIsMobile } from "@/hooks/use-mobile"
import HistoryPanel from "@/pages/chat/HistoryPanel"
import { ChatComposer, type ChatComposerHandle } from "@/pages/chat/ChatComposer"
import { MessageList } from "@/pages/chat/MessageList"
import { MessageBubble, getFinalAnswerText } from "@/pages/chat/MessageBubble"
import { SourcesPanel } from "@/pages/chat/SourcesPanel"
import {
  useChatSearch,
  useChatMessages,
  useChats,
  useCreateChat,
  useDeleteChat,
  useMe,
  useModels,
  useOrgsMine,
} from "@/hooks/use-chat-query"
import {
  readClipboardImagesAsAttachments,
  readFilesAsAttachments,
} from "@/lib/file-utils"

const DEFAULT_ATTACHMENT_LIMITS = {
  max_files: 50,
  max_file_bytes: 20_000_000,
  max_total_bytes: 50_000_000,
}

const PROVIDER_REASONING_LEVELS: Record<string, string[]> = {
  openai: ["none", "low", "medium", "high"],
  azure: ["none", "low", "medium", "high"],
  openrouter: ["none", "low", "medium", "high"],
  anthropic: ["low", "medium", "high"],
  gemini: ["low", "medium", "high"],
  vertex: ["low", "medium", "high"],
  groq: ["low", "medium", "high"],
}

const DEFAULT_REASONING_LEVELS = ["none", "low", "medium", "high"]

const getProviderReasoningLevels = (provider?: string | null): string[] => {
  if (!provider) return DEFAULT_REASONING_LEVELS
  const configured = PROVIDER_REASONING_LEVELS[provider] ?? DEFAULT_REASONING_LEVELS
  return configured.length > 0 ? configured : DEFAULT_REASONING_LEVELS
}

const resolveReasoningStopIndex = (
  effort: string | null | undefined,
  providerLevels: string[]
): number => {
  const normalized = effort?.trim().toLowerCase()
  if (!normalized) {
    return providerLevels.indexOf("none") >= 0 ? providerLevels.indexOf("none") : 0
  }
  const effortIndex = providerLevels.indexOf(normalized)
  if (effortIndex >= 0) return effortIndex
  return providerLevels.indexOf("none") >= 0 ? providerLevels.indexOf("none") : 0
}

const SESSION_OWNED_CHAT_IDS_KEY = "chatui_session_owned_chat_ids"

const readSessionOwnedChatIds = (): string[] => {
  try {
    const raw = window.sessionStorage.getItem(SESSION_OWNED_CHAT_IDS_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw) as unknown
    return Array.isArray(parsed)
      ? parsed.filter((id): id is string => typeof id === "string")
      : []
  } catch {
    return []
  }
}

const rememberSessionOwnedChatId = (chatId: string) => {
  const next = Array.from(new Set([...readSessionOwnedChatIds(), chatId]))
  window.sessionStorage.setItem(SESSION_OWNED_CHAT_IDS_KEY, JSON.stringify(next))
  return next
}

const estimateBase64Bytes = (value: string): number => {
  if (!value) return 0
  const padding = value.match(/=+$/)?.[0]?.length ?? 0
  return Math.max(Math.floor((value.length * 3) / 4) - padding, 0)
}

const extractAttachmentIdFromContentUrl = (url?: string): string | undefined => {
  if (!url) return undefined
  const match = url.match(/\/attachments\/([0-9a-fA-F-]{36})\/content/)
  return match?.[1]
}

const isTimelineActionLabel = (label: string): boolean =>
  !/^Step \d+\/\d+$/.test(label) && label !== "Thinking" && label !== "Answering"

const appendStreamText = (
  parts: ChatMessage["stream_parts"],
  delta: string
): NonNullable<ChatMessage["stream_parts"]> => {
  const next = [...(parts ?? [])]
  const last = next[next.length - 1]
  if (last?.type === "text") {
    next[next.length - 1] = { type: "text", text: last.text + delta }
  } else {
    next.push({ type: "text", text: delta })
  }
  return next
}

const appendStreamAction = (
  parts: ChatMessage["stream_parts"],
  label: string
): NonNullable<ChatMessage["stream_parts"]> => [...(parts ?? []), { type: "action", label }]

const actionLabelMatchesToolEvent = (
  label: string,
  toolEvent: NonNullable<ChatMessage["tool_event"]>
): boolean => {
  if (toolEvent.type === "tool_call") {
    const summary = toolEvent.action_summary?.trim()
    if (summary) return label === summary
    if (toolEvent.tool_name === "generate_image") return label === "Generating image"
    if (toolEvent.tool_name === "edit_image") return label === "Editing image"
    if (toolEvent.tool_name === "download_attachments") {
      return label === "Downloading attachments"
    }
    if (toolEvent.tool_name === "code_execution") {
      return label === "Running code" || label.startsWith("Running code (")
    }
    if (toolEvent.tool_name === "extract_pdf") return label === "Extracting PDF"
    return false
  }
  if (toolEvent.type === "code_execution") {
    return label === "Running code" || label.startsWith("Running code (")
  }
  if (toolEvent.type === "url_attachments") {
    return label === "Downloading attachments"
  }
  if (toolEvent.type === "context_summary") {
    return label === "Summarizing context" || label.toLowerCase().includes("summar")
  }
  if (toolEvent.type === "coworking") {
    return (
      label === "Opening co-editing" ||
      label === "Updating document" ||
      label === "Closing co-editing" ||
      label === "Co-editing" ||
      label.toLowerCase().includes("co-edit") ||
      label.toLowerCase().includes("cowork") ||
      label.toLowerCase().includes("document")
    )
  }
  return false
}

const isSpecializedToolEvent = (
  toolEvent: NonNullable<ChatMessage["tool_event"]>
): boolean =>
  toolEvent.type === "code_execution" ||
  toolEvent.type === "url_attachments" ||
  toolEvent.type === "context_summary" ||
  toolEvent.type === "coworking"

const resolveToolEventActionLabel = (
  toolEvent: NonNullable<ChatMessage["tool_event"]>
): string => {
  if (toolEvent.type === "tool_call") {
    const summary = toolEvent.action_summary?.trim()
    if (summary) return summary
    if (toolEvent.tool_name === "generate_image") return "Generating image"
    if (toolEvent.tool_name === "edit_image") return "Editing image"
    if (toolEvent.tool_name === "download_attachments") return "Downloading attachments"
    if (toolEvent.tool_name === "code_execution") return "Running code"
    if (toolEvent.tool_name === "extract_pdf") return "Extracting PDF"
    if (toolEvent.tool_name === "start_coworking") return "Opening co-editing"
    if (toolEvent.tool_name === "cowork_write") return "Writing document"
    if (toolEvent.tool_name === "cowork_str_replace") return "Editing document"
    if (toolEvent.tool_name === "cowork_append") return "Appending to document"
    if (toolEvent.tool_name === "cowork_read") return "Reading document"
    return `Running ${toolEvent.tool_name}`
  }
  if (toolEvent.type === "code_execution") return "Running code"
  if (toolEvent.type === "url_attachments") return "Downloading attachments"
  if (toolEvent.type === "context_summary") return "Summarizing context"
  if (toolEvent.type === "coworking") {
    if (toolEvent.action === "open") return "Opening co-editing"
    if (toolEvent.action === "writing") return "Writing document"
    if (toolEvent.action === "update") return "Updating document"
    if (toolEvent.action === "close") return "Closing co-editing"
    return "Co-editing"
  }
  return "Running tool"
}

const attachStreamActionToolEvent = (
  parts: ChatMessage["stream_parts"],
  toolEvent: NonNullable<ChatMessage["tool_event"]>
): NonNullable<ChatMessage["stream_parts"]> => {
  const next = [...(parts ?? [])]
  if (toolEvent.id) {
    for (let i = next.length - 1; i >= 0; i -= 1) {
      const part = next[i]
      if (part.type !== "action" || part.tool_event?.id !== toolEvent.id) continue
      next[i] = { ...part, tool_event: toolEvent }
      return next
    }
  }
  for (let i = next.length - 1; i >= 0; i -= 1) {
    const part = next[i]
    if (part.type !== "action") continue
    if (!actionLabelMatchesToolEvent(part.label, toolEvent)) continue
    if (
      part.tool_event &&
      isSpecializedToolEvent(part.tool_event) &&
      toolEvent.type === "tool_call"
    ) {
      // Keep richer specialized payload; action is already covered.
      return next
    }
    if (part.tool_event && !isSpecializedToolEvent(toolEvent) && part.tool_event.id !== toolEvent.id) {
      continue
    }
    next[i] = { ...part, tool_event: toolEvent }
    return next
  }
  next.push({
    type: "action",
    label: resolveToolEventActionLabel(toolEvent),
    tool_event: toolEvent,
  })
  return next
}

const attachStreamActionAttachments = (
  parts: ChatMessage["stream_parts"],
  label: string,
  attachments: NonNullable<ChatMessage["attachments"]>
): NonNullable<ChatMessage["stream_parts"]> => {
  const next = [...(parts ?? [])]
  for (let i = next.length - 1; i >= 0; i -= 1) {
    const part = next[i]
    if (part.type === "action" && part.label === label && !part.attachments?.length) {
      next[i] = { ...part, attachments }
      return next
    }
  }
  for (let i = next.length - 1; i >= 0; i -= 1) {
    const part = next[i]
    if (part.type === "action" && part.label === label) {
      next[i] = {
        ...part,
        attachments: [...(part.attachments ?? []), ...attachments],
      }
      return next
    }
  }
  next.push({ type: "action", label, attachments })
  return next
}

const attachmentIdentity = (attachment: {
  file_name?: string | null
  content_type?: string | null
  data_base64?: string | null
  content_url?: string | null
}) =>
  attachment.data_base64 ||
  attachment.content_url ||
  `${attachment.file_name ?? ""}:${attachment.content_type ?? ""}`

const mergeMessageAttachments = (
  existing: ChatMessage["attachments"],
  incoming: NonNullable<ChatMessage["attachments"]>
): NonNullable<ChatMessage["attachments"]> => {
  const merged = [...(existing ?? [])]
  const seen = new Set(merged.map(attachmentIdentity))
  for (const item of incoming) {
    const key = attachmentIdentity(item)
    if (seen.has(key)) continue
    seen.add(key)
    merged.push(item)
  }
  return merged
}

type InsertPromptPickerProps = {
  open: boolean
  prompts: Prompt[]
  title: string
  description: string
  searchPlaceholder: string
  onOpenChange: (open: boolean) => void
  onSelect: (body: string) => void
}

const InsertPromptPicker = ({
  open,
  prompts,
  title,
  description,
  searchPlaceholder,
  onOpenChange,
  onSelect,
}: InsertPromptPickerProps) => {
  const [query, setQuery] = useState("")

  useEffect(() => {
    if (!open) setQuery("")
  }, [open])

  const needle = query.trim().toLowerCase()
  const filtered = needle
    ? prompts.filter(
        (prompt) =>
          prompt.name.toLowerCase().includes(needle) ||
          (prompt.description ?? "").toLowerCase().includes(needle)
      )
    : prompts

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <Input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={searchPlaceholder}
        />
        <div className="flex max-h-72 flex-col gap-1 overflow-y-auto">
          {filtered.map((prompt) => (
            <Button
              key={prompt.id}
              type="button"
              variant="ghost"
              className="flex h-auto min-h-10 w-full flex-col items-start gap-0.5 px-3 py-2 text-left"
              onClick={() => {
                onSelect(prompt.body)
                onOpenChange(false)
              }}
            >
              <span className="w-full truncate text-sm font-medium">{prompt.name}</span>
              {prompt.description ? (
                <span className="w-full truncate text-xs text-muted-foreground">
                  {prompt.description}
                </span>
              ) : null}
            </Button>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  )
}

export const ChatPage = () => {
  const navigate = useNavigate()
  const location = useLocation()
  const [attachmentLimits, setAttachmentLimits] = useState(DEFAULT_ATTACHMENT_LIMITS)
  const { chatId, shareToken: shareTokenParam } = useParams()
  const activeAgentIdFromQuery = useMemo(
    () => new URLSearchParams(location.search).get("agent"),
    [location.search]
  )
  const [orgId, setOrgId] = useState<string | null>(orgStore.get())
  const [agents, setAgents] = useState<Agent[]>([])
  const [toolEvents, setToolEvents] = useState<ChatMessage[]>([])
  const cowork = useCoworkDocument(chatId)
  const isMobile = useIsMobile()
  const coworkHandleRef = useRef(cowork.handleCoworkingEvent)
  coworkHandleRef.current = cowork.handleCoworkingEvent
  const composerRef = useRef<ChatComposerHandle>(null)
  const [promptCount, setPromptCount] = useState(0)
  const [insertPromptOpen, setInsertPromptOpen] = useState(false)
  const [insertPrompts, setInsertPrompts] = useState<Prompt[]>([])
  const [savePromptOpen, setSavePromptOpen] = useState(false)
  const [savePromptBody, setSavePromptBody] = useState("")
  const [selectedModel, setSelectedModel] = useState<string | undefined>(
    modelStore.get() ?? undefined
  )
  const [reasoningStopIndex, setReasoningStopIndex] = useState(0)
  const [loadingByChat, setLoadingByChat] = useState<Record<string, boolean>>({})
  const chatIdRef = useRef(chatId)
  chatIdRef.current = chatId
  const setChatGenerating = useCallback(
    (targetChatId: string, generating: boolean, options?: { unreadIfAway?: boolean }) => {
      setLoadingByChat((prev) => ({ ...prev, [targetChatId]: generating }))
      if (generating) {
        chatGenerationIndicatorsStore.start(targetChatId)
        return
      }
      const unreadIfAway = options?.unreadIfAway ?? true
      if (unreadIfAway && chatIdRef.current !== targetChatId) {
        chatGenerationIndicatorsStore.finish(targetChatId, true)
      } else {
        chatGenerationIndicatorsStore.clear(targetChatId)
      }
    },
    []
  )
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null)
  const [editingAttachments, setEditingAttachments] = useState<
    ChatMessageAttachmentInput[]
  >([])
  const [editingAttachmentError, setEditingAttachmentError] = useState<string | null>(null)
  const [isEditDragActive, setIsEditDragActive] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement | null>(null)
  const messagesContainerRef = useRef<HTMLDivElement | null>(null)
  // Stable across temp→server id remaps (conversation turn count).
  const lastScrolledKeyRef = useRef<string | null>(null)
  const deepLinkedScrolledRef = useRef<string | null>(null)
  const [autoScrollEnabled, setAutoScrollEnabled] = useState(true)
  const [pendingAttachments, setPendingAttachments] = useState<
    ChatMessageAttachmentInput[]
  >([])
  const [isUploadingAttachments, setIsUploadingAttachments] = useState(false)
  const [attachmentError, setAttachmentError] = useState<string | null>(null)
  const [isDragActive, setIsDragActive] = useState(false)
  const ATTACHMENTS_MAX_FILES = attachmentLimits.max_files
  const ATTACHMENTS_MAX_FILE_BYTES = attachmentLimits.max_file_bytes
  const ATTACHMENTS_MAX_TOTAL_BYTES = attachmentLimits.max_total_bytes
  const [webSearchEnabled, setWebSearchEnabled] = useState<boolean>(() => {
    const stored = webSearchEnabledStore.get()
    return stored == null ? true : stored === "1"
  })
  const [codeExecutionEnabled, setCodeExecutionEnabled] = useState<boolean>(() => {
    const stored = codeExecutionEnabledStore.get()
    return stored == null ? true : stored === "1"
  })
  const [incognitoEnabled, setIncognitoEnabled] = useState(
    () => window.sessionStorage.getItem("chatui_incognito_enabled") === "1"
  )
  const [actionInfoLevel, setActionInfoLevel] = useState<ActionInfoLevel>(() =>
    actionInfoLevelStore.get()
  )
  const [previewAttachment, setPreviewAttachment] =
    useState<ChatMessageAttachmentInput | null>(null)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [desktopSidebarOpen, setDesktopSidebarOpen] = useState(true)
  const [chatSearchDebounced, setChatSearchDebounced] = useState("")
  const [blockedLinkDialogOpen, setBlockedLinkDialogOpen] = useState(false)
  const [deleteConfirmChat, setDeleteConfirmChat] = useState<Chat | null>(null)
  const [deleteConfirmMessage, setDeleteConfirmMessage] = useState<ChatMessage | null>(null)
  const [sessionOwnedChatIds, setSessionOwnedChatIds] = useState<string[]>(() =>
    readSessionOwnedChatIds()
  )
  const [sourcesPanelSources, setSourcesPanelSources] = useState<SourceItem[] | null>(null)
  const composerInputRef = useRef<HTMLTextAreaElement | null>(null)
  const { locale, t, tCount } = useI18n()
  const isHistoryView = location.pathname === "/history"
  const codeTheme = useMemo<Record<string, CSSProperties>>(() => oneDark, [])

  const { data: currentUser } = useMe()
  const { data: orgs = [], isLoading: orgsLoading } = useOrgsMine()
  const { data: models = [] } = useModels(orgId)
  const { data: chats = [], isFetched: chatsFetched, refetch: refetchChats } = useChats(orgId)
  const { data: searchedChats = [] } = useChatSearch(orgId, chatSearchDebounced)
  const {
    data: serverMessages = [],
    isLoading: isMessagesLoading,
    error: messagesError,
  } = useChatMessages(chatId ?? null)
  const createChatMutation = useCreateChat(orgId)
  const deleteChatMutation = useDeleteChat(orgId)

  useEffect(() => {
    configApi
      .attachmentLimits()
      .then(setAttachmentLimits)
      .catch(() => {
        // Keep backend defaults when limits cannot be loaded.
      })
  }, [])

  useEffect(() => {
    if (!orgId) {
      setAgents([])
      return
    }
    let cancelled = false
    agentApi
      .list()
      .then((items) => {
        if (cancelled) return
        setAgents(items)
      })
      .catch(() => {
        if (cancelled) return
        setAgents([])
      })
      .finally(() => {
        if (cancelled) return
      })
    return () => {
      cancelled = true
    }
  }, [orgId])

  useEffect(() => {
    if (isHistoryView) return
    setChatSearchDebounced("")
  }, [isHistoryView])

  const appendToolEvent = useCallback((
    event: NonNullable<ChatMessage["tool_event"]>,
    taskId?: string | null
  ) => {
    if (!event) return
    setToolEvents((prev) => {
      // If event has an ID, update only the in-flight placeholder for that call
      // (same id, same task, and no output yet). This preserves multiple runs.
      if (event.id) {
        const existingIndex = prev.findIndex((msg) => {
          if (msg.tool_event?.id !== event.id) return false
          if (taskId && msg.task_id && msg.task_id !== taskId) return false
          if (msg.tool_event?.type === "code_execution") {
            const output = msg.tool_event.output
            const hasMaterializedOutput =
              Boolean(output?.stdout) ||
              Boolean(output?.stderr) ||
              Boolean(output?.error) ||
              Boolean(output?.requires_approval) ||
              Boolean(output?.timed_out) ||
              typeof output?.exit_code === "number" ||
              Boolean(output?.output_files?.length)
            return !hasMaterializedOutput
          }
          if (msg.tool_event?.type === "tool_call") {
            const status = msg.tool_event.output?.status
            return status !== "ok" && status !== "error"
          }
          if (msg.tool_event?.type === "coworking") {
            const status = msg.tool_event.output?.status
            return status !== "ok" && status !== "error"
          }
          return false
        })
        if (existingIndex >= 0) {
          const next = [...prev]
          const existing = next[existingIndex]
          if (existing.tool_event) {
            next[existingIndex] = {
              ...existing,
              tool_event: event,
            }
            return next
          }
        }
      }
      // Otherwise append new
      return [
        ...prev,
        {
          id: `tool-${Date.now()}-${Math.random().toString(16).slice(2)}`,
          role: "tool",
          content: "",
          created_at: new Date().toISOString(),
          tool_event: event,
          task_id: taskId ?? null,
        },
      ]
    })
  }, [])

  const currentCancelRef = useRef<null | (() => void)>(null)
  const generationEpochRef = useRef(0)
  const composerBusyRef = useRef(false)
  const taskCursorRef = useRef<Record<string, number>>({})
  const taskSubscriptionsRef = useRef<Record<string, () => void>>({})
  const taskPollingRef = useRef<Record<string, number>>({})

  const stopGeneration = () => {
    if (!chatId) return
    generationEpochRef.current += 1
    const activeAssistant = [...visibleMessages]
      .reverse()
      .find(
        (msg) =>
          msg.role === "assistant" &&
          !isTerminalStatus(msg.generation_status ?? null)
      )
    const activeTaskId = activeAssistant?.task_id ?? null
    if (activeTaskId) {
      chatApi.cancelGenerationTask(chatId, activeTaskId).catch(() => null)
      if (taskSubscriptionsRef.current[activeTaskId]) {
        taskSubscriptionsRef.current[activeTaskId]()
        delete taskSubscriptionsRef.current[activeTaskId]
      }
      if (taskPollingRef.current[activeTaskId]) {
        window.clearTimeout(taskPollingRef.current[activeTaskId])
        delete taskPollingRef.current[activeTaskId]
      }
    }
    if (activeAssistant) {
      updateChatMessagesFor(chatId, (prev) =>
        prev.map((msg) =>
          msg.id === activeAssistant.id ||
          (activeTaskId && msg.task_id === activeTaskId && msg.role === "assistant")
            ? {
                ...msg,
                generation_status: "cancelled" as const,
                thinking_steps: msg.thinking_steps ?? [],
                content:
                  msg.content && msg.content.trim().length > 0
                    ? msg.content
                    : t("chat_generation_cancelled"),
              }
            : msg
        )
      )
    }
    if (currentCancelRef.current) {
      currentCancelRef.current()
      currentCancelRef.current = null
    }
    setChatGenerating(chatId, false, { unreadIfAway: false })
  }


  const modelNameById = useMemo(() => {
    return Object.fromEntries(models.map((model) => [model.id, model.display_name]))
  }, [models])
  const isImageOutputModel = useCallback(
    (model: ChatModel) => supportsImageOutput(model),
    []
  )
  const isEmbeddingModel = useCallback((model: ChatModel) => {
    const name = `${model.display_name} ${model.model_name}`.toLowerCase()
    return /(^|[\s/_-])(embedding|embeddings|text-embedding|embed)([\s/_-]|$)/.test(
      name
    )
  }, [])
  const selectableChatModels = useMemo(
    () => models.filter((model) => !isEmbeddingModel(model)),
    [models, isEmbeddingModel]
  )
  const selectedChatModel = useMemo(
    () => selectableChatModels.find((model) => model.id === selectedModel) ?? null,
    [selectableChatModels, selectedModel]
  )
  const providerReasoningLevels = useMemo(
    () => getProviderReasoningLevels(selectedChatModel?.provider ?? null),
    [selectedChatModel?.provider]
  )
  const selectedReasoningEffort = useMemo(
    () => providerReasoningLevels[reasoningStopIndex] ?? selectedChatModel?.reasoning_effort ?? undefined,
    [providerReasoningLevels, reasoningStopIndex, selectedChatModel?.reasoning_effort]
  )
  const activeReasoningStopIndex = useMemo(() => {
    const effortIndex = providerReasoningLevels.indexOf(selectedReasoningEffort ?? "")
    return effortIndex >= 0 ? effortIndex : reasoningStopIndex
  }, [providerReasoningLevels, selectedReasoningEffort, reasoningStopIndex])
  const rejectUnsupportedImageAttachments = useCallback(
    (
      items: Array<{ content_type?: string | null }>,
      setError: (value: string | null) => void
    ) => {
      const hasImages = items.some((item) =>
        (item.content_type ?? "").startsWith("image/")
      )
      if (hasImages && !supportsImageInput(selectedChatModel)) {
        setError(t("chat_attachment_image_not_supported"))
        return true
      }
      return false
    },
    [selectedChatModel, t]
  )

  const parseChatDate = useCallback((value: string) => {
    const hasTimezone = /[zZ]|[+-]\d{2}:?\d{2}$/.test(value)
    const normalized = hasTimezone ? value : `${value}Z`
    return new Date(normalized)
  }, [])

  const getChatActivityDate = useCallback(
    (chat: Chat) => chat.last_activity_at || chat.created_at,
    []
  )

  const queryClient = useQueryClient()

  const updateChatMessagesFor = useCallback(
    (targetChatId: string, updater: (prev: ChatMessage[]) => ChatMessage[]) => {
      queryClient.setQueryData<ChatMessage[]>(
        ["chatMessages", targetChatId],
        (prev) => updater(prev ?? [])
      )
    },
    [queryClient]
  )

  const isTerminalStatus = useCallback(
    (status?: GenerationStatus | null) =>
      status === "completed" || status === "failed" || status === "cancelled",
    []
  )

  const applyStreamEvent = useCallback(
    (targetChatId: string, assistantId: string, event: Record<string, unknown>) => {
      const matchesAssistant = (msg: ChatMessage) => {
        // Never match tool/event bubbles that share a task_id with the assistant.
        if (msg.role !== "assistant") return false
        if (msg.id === assistantId) return true
        if (
          typeof event.message_id === "string" &&
          event.message_id &&
          msg.id === event.message_id
        ) {
          return true
        }
        if (
          typeof event.assistant_message_id === "string" &&
          event.assistant_message_id &&
          msg.id === event.assistant_message_id
        ) {
          return true
        }
        return (
          typeof event.task_id === "string" &&
          Boolean(event.task_id) &&
          msg.task_id === event.task_id
        )
      }

      if (typeof event.chat_title === "string" && event.chat_title.trim()) {
        const titledChatId =
          typeof event.chat_id === "string" && event.chat_id
            ? event.chat_id
            : targetChatId
        if (orgId) {
          queryClient.setQueryData<Chat[]>(["chats", orgId], (prev) =>
            prev
              ? prev.map((chat) =>
                  chat.id === titledChatId
                    ? { ...chat, title: event.chat_title as string }
                    : chat
                )
              : prev
          )
        }
        return
      }

      if ("done" in event && event.done === true) {
        const messageId = typeof event.message_id === "string" ? event.message_id : null
        const content = typeof event.content === "string" ? event.content : null
        const modelName = typeof event.model_name === "string" ? event.model_name : null
        const modelId = typeof event.model_id === "string" ? event.model_id : null
        const usage =
          event.usage && typeof event.usage === "object"
            ? (event.usage as ChatMessage["usage"])
            : null
        updateChatMessagesFor(targetChatId, (prev) =>
          prev.map((msg) =>
            matchesAssistant(msg)
              ? {
                  ...msg,
                  id: messageId ?? msg.id,
                  content: content && content.length > 0 ? content : msg.content || "",
                  model_name: modelName ?? msg.model_name ?? null,
                  model_id: modelId ?? msg.model_id ?? null,
                  attachments:
                    (event.attachments as ChatMessage["attachments"]) ??
                    msg.attachments,
                  sources:
                    (event.sources as ChatMessage["sources"]) ?? msg.sources,
                  usage: usage ?? msg.usage ?? null,
                  thinking_steps: msg.thinking_steps ?? [],
                  generation_status: "completed",
                }
              : msg
          )
        )
        setChatGenerating(targetChatId, false)
        return
      }
      if ("error" in event && typeof event.error === "string") {
        const raw = event.error.trim()
        const errorText =
          raw === "Chat usage limit exceeded"
            ? t("chat_usage_limit_exceeded")
            : raw || t("chat_generation_failed")
        updateChatMessagesFor(targetChatId, (prev) =>
          prev.map((msg) =>
            matchesAssistant(msg)
              ? {
                  ...msg,
                  content: errorText,
                  thinking_steps: msg.thinking_steps ?? [],
                  generation_status: "failed",
                }
              : msg
          )
        )
        setChatGenerating(targetChatId, false, { unreadIfAway: false })
        return
      }
      if ("delta" in event && typeof event.delta === "string") {
        const delta = event.delta as string
        updateChatMessagesFor(targetChatId, (prev) =>
          prev.map((msg) => {
            if (!matchesAssistant(msg)) return msg
            // If timeline was never built (e.g. resume raced ahead of message load),
            // seed from persisted content so new deltas don't hide history.
            const seed =
              !msg.stream_parts?.length && msg.content ? msg.content : null
            const seededParts = seed
              ? ([{ type: "text", text: seed }] as NonNullable<ChatMessage["stream_parts"]>)
              : msg.stream_parts
            return {
              ...msg,
              content: seed ? seed + delta : msg.content + delta,
              stream_parts: appendStreamText(seededParts, delta),
              generation_status: "streaming",
            }
          })
        )
        return
      }
      if ("user_message_id" in event && typeof event.user_message_id === "string") {
        const userMessageId = event.user_message_id
        const editedMessageId =
          "edited_message_id" in event && typeof event.edited_message_id === "string"
            ? event.edited_message_id
            : null
        updateChatMessagesFor(targetChatId, (prev) =>
          prev.map((msg) =>
            editedMessageId
              ? msg.id === editedMessageId
                ? { ...msg, id: userMessageId }
                : msg
              : msg.id.startsWith("temp-user-")
                ? { ...msg, id: userMessageId }
                : msg
          )
        )
        return
      }
      if (
        "task_id" in event &&
        typeof event.task_id === "string" &&
        !("error" in event) &&
        !("delta" in event) &&
        !("done" in event) &&
        !("tool_event" in event) &&
        !("activity" in event)
      ) {
        const taskId = event.task_id
        const assistantMessageId =
          "assistant_message_id" in event &&
          typeof event.assistant_message_id === "string"
            ? event.assistant_message_id
            : null
        updateChatMessagesFor(targetChatId, (prev) =>
          prev.map((msg) => {
            if (!matchesAssistant(msg)) return msg
            const nextId = assistantMessageId ?? msg.id
            return {
              ...msg,
              id: nextId,
              task_id: taskId,
              generation_status: msg.generation_status ?? "queued",
            }
          })
        )
        return
      }
      if (
        "activity" in event &&
        typeof event.activity === "object" &&
        event.activity
      ) {
        const activity = event.activity as { label: string; state: "start" | "end" }
        const isStep = /^Step \d+\/\d+$/.test(activity.label)
        updateChatMessagesFor(targetChatId, (prev) =>
          prev.map((msg) => {
            if (!matchesAssistant(msg)) return msg
            const current = msg.thinking_steps ?? []
            if (activity.state === "start") {
              if (isStep) {
                const withoutSteps = current.filter(
                  (label) => !/^Step \d+\/\d+$/.test(label)
                )
                return {
                  ...msg,
                  thinking_steps: [...withoutSteps, activity.label],
                }
              }
              // Keep a chronological action log (allow repeats for distinct runs).
              return {
                ...msg,
                thinking_steps: [...current, activity.label],
                stream_parts: isTimelineActionLabel(activity.label)
                  ? appendStreamAction(msg.stream_parts, activity.label)
                  : msg.stream_parts,
              }
            }
            // Non-debug thinking UI keeps completed actions visible until the
            // generation finishes; only clear ephemeral "Thinking" waits.
            if (activity.label === "Thinking") {
              const lastThinkingIndex = current.lastIndexOf("Thinking")
              if (lastThinkingIndex < 0) return msg
              return {
                ...msg,
                thinking_steps: [
                  ...current.slice(0, lastThinkingIndex),
                  ...current.slice(lastThinkingIndex + 1),
                ],
              }
            }
            return msg
          })
        )
        return
      }
      if ("tool_event" in event) {
        const toolEvent = event.tool_event as ChatMessage["tool_event"]
        if (toolEvent) {
          if (toolEvent.type === "coworking") {
            coworkHandleRef.current?.(toolEvent)
          }
          appendToolEvent(
            toolEvent,
            typeof event.task_id === "string" ? event.task_id : null
          )
          updateChatMessagesFor(targetChatId, (prev) =>
            prev.map((msg) =>
              matchesAssistant(msg)
                ? {
                    ...msg,
                    stream_parts: attachStreamActionToolEvent(msg.stream_parts, toolEvent),
                  }
                : msg
            )
          )
          if (
            toolEvent.type === "tool_call" &&
            toolEvent.state === "end" &&
            Array.isArray(toolEvent.output?.attachments) &&
            toolEvent.output.attachments.length > 0
          ) {
            const attachments = toolEvent.output.attachments
            const label = resolveToolEventActionLabel(toolEvent)
            updateChatMessagesFor(targetChatId, (prev) =>
              prev.map((msg) =>
                matchesAssistant(msg)
                  ? {
                      ...msg,
                      stream_parts: attachStreamActionAttachments(
                        msg.stream_parts,
                        label,
                        attachments
                      ),
                      attachments: mergeMessageAttachments(msg.attachments, attachments),
                    }
                  : msg
              )
            )
          }
        }
        return
      }
    },
    [appendToolEvent, orgId, queryClient, t, updateChatMessagesFor, setChatGenerating]
  )

  const normalizeTaskEvent = useCallback(
    (event: { event_type: string; payload?: Record<string, unknown> | null }) => {
      if (!event) return null
      if (event.event_type === "activity") {
        return { activity: event.payload }
      }
      if (event.event_type === "tool_event") {
        return { tool_event: event.payload }
      }
      if (event.event_type === "delta") {
        return event.payload
      }
      if (event.event_type === "done") {
        return event.payload
      }
      if (event.event_type === "error") {
        return event.payload
      }
      if (event.event_type === "chat_title") {
        return event.payload
      }
      return null
    },
    []
  )

  const fetchTaskEvents = useCallback(
    async (targetChatId: string, taskId: string, assistantId: string) => {
      const after = taskCursorRef.current[taskId] ?? 0
      const events = await chatApi.listGenerationEvents(targetChatId, taskId, after)
      // Another resume/fetch may have advanced the cursor while we were in-flight.
      // Ignore stale full replays so activity labels don't double.
      const currentAfter = taskCursorRef.current[taskId] ?? 0
      if (after === 0 && currentAfter > 0) {
        return
      }
      if (events.length === 0) return

      let contentBackup = ""
      if (after === 0) {
        // Rebuild timeline from events. Clear content first so deltas don't
        // duplicate text already persisted on the message.
        updateChatMessagesFor(targetChatId, (prev) =>
          prev.map((msg) => {
            if (msg.role !== "assistant") return msg
            if (msg.id !== assistantId && msg.task_id !== taskId) return msg
            contentBackup = msg.content || contentBackup
            return { ...msg, thinking_steps: [], stream_parts: [], content: "" }
          })
        )
      }
      taskCursorRef.current[taskId] = events[events.length - 1].sequence
      events.forEach((event) => {
        const normalized = normalizeTaskEvent(event)
        if (normalized) {
          applyStreamEvent(targetChatId, assistantId, normalized as Record<string, unknown>)
        }
      })
      if (after === 0) {
        updateChatMessagesFor(targetChatId, (prev) =>
          prev.map((msg) => {
            if (msg.role !== "assistant") return msg
            if (msg.id !== assistantId && msg.task_id !== taskId) return msg
            const partsText = (msg.stream_parts ?? [])
              .filter(
                (part): part is Extract<NonNullable<ChatMessage["stream_parts"]>[number], { type: "text" }> =>
                  part.type === "text"
              )
              .map((part) => part.text)
              .join("")
            if (partsText.length > 0) {
              return { ...msg, content: partsText }
            }
            if (contentBackup) {
              return { ...msg, content: contentBackup }
            }
            return msg
          })
        )
      }
    },
    [applyStreamEvent, normalizeTaskEvent, updateChatMessagesFor]
  )

  const applyTerminalTaskStatus = useCallback(
    (
      targetChatId: string,
      assistantId: string,
      taskId: string,
      status: GenerationStatus,
      errorMessage?: string | null
    ) => {
      updateChatMessagesFor(targetChatId, (prev) =>
        prev.map((msg) => {
          if (!(msg.id === assistantId || msg.task_id === taskId)) return msg
          if (status === "failed") {
            const raw = (errorMessage || "").trim()
            const fallback =
              raw === "Chat usage limit exceeded"
                ? t("chat_usage_limit_exceeded")
                : raw || t("chat_generation_failed")
            return {
              ...msg,
              content: msg.content?.trim().length ? msg.content : fallback,
              thinking_steps: msg.thinking_steps ?? [],
              generation_status: "failed",
            }
          }
          if (status === "cancelled") {
            const fallback =
              (errorMessage || "").trim() || t("chat_generation_cancelled")
            return {
              ...msg,
              content: msg.content?.trim().length ? msg.content : fallback,
              thinking_steps: msg.thinking_steps ?? [],
              generation_status: "cancelled",
            }
          }
          return {
            ...msg,
            thinking_steps: msg.thinking_steps ?? [],
            generation_status: "completed",
          }
        })
      )
      const unreadIfAway = status === "completed"
      setChatGenerating(targetChatId, false, { unreadIfAway })
    },
    [t, updateChatMessagesFor, setChatGenerating]
  )

  const syncTerminalTaskStatus = useCallback(
    async (targetChatId: string, taskId: string, assistantId: string) => {
      try {
        const task = await chatApi.getGenerationTask(targetChatId, taskId)
        if (!isTerminalStatus(task.status)) return false
        applyTerminalTaskStatus(
          targetChatId,
          assistantId,
          taskId,
          task.status,
          task.error
        )
        return true
      } catch {
        return false
      }
    },
    [applyTerminalTaskStatus, isTerminalStatus]
  )

  const pollTaskEvents = useCallback(
    async (targetChatId: string, taskId: string, assistantId: string) => {
      if (taskPollingRef.current[taskId]) return
      const run = async () => {
        try {
          await fetchTaskEvents(targetChatId, taskId, assistantId)
          const task = await chatApi.getGenerationTask(targetChatId, taskId)
          if (isTerminalStatus(task.status)) {
            applyTerminalTaskStatus(
              targetChatId,
              assistantId,
              taskId,
              task.status,
              task.error
            )
            delete taskPollingRef.current[taskId]
            return
          }
        } catch {
          delete taskPollingRef.current[taskId]
          return
        }
        taskPollingRef.current[taskId] = window.setTimeout(run, 2000)
      }
      taskPollingRef.current[taskId] = window.setTimeout(run, 2000)
    },
    [applyTerminalTaskStatus, fetchTaskEvents, isTerminalStatus]
  )

  const subscribeToTask = useCallback(
    (targetChatId: string, taskId: string, assistantId: string) => {
      if (taskSubscriptionsRef.current[taskId]) return
      const after = taskCursorRef.current[taskId] ?? 0
      const { promise, cancel } = chatApi.subscribeGenerationTask(
        targetChatId,
        taskId,
        after,
        (event) => {
          applyStreamEvent(targetChatId, assistantId, event as Record<string, unknown>)
        }
      )
      taskSubscriptionsRef.current[taskId] = cancel
      promise
        .catch(() => {
          pollTaskEvents(targetChatId, taskId, assistantId)
        })
        .finally(() => {
          delete taskSubscriptionsRef.current[taskId]
          syncTerminalTaskStatus(targetChatId, taskId, assistantId).catch(() => null)
        })
    },
    [applyStreamEvent, pollTaskEvents, syncTerminalTaskStatus]
  )

  const replaceChatMessagesFor = useCallback(
    (targetChatId: string, messages: ChatMessage[]) => {
      queryClient.setQueryData<ChatMessage[]>(["chatMessages", targetChatId], (prev) => {
        // Never clobber local/optimistic turns with an empty server snapshot
        // (common right after create/send races or failed pre-persist WS errors).
        if (!messages.length) return prev?.length ? prev : messages
        if (!prev?.length) return messages
        const preservedSteps = new Map<string, string[]>()
        const preservedParts = new Map<string, NonNullable<ChatMessage["stream_parts"]>>()
        for (const msg of prev) {
          if (msg.role !== "assistant") continue
          if (msg.thinking_steps?.length) {
            if (msg.task_id) preservedSteps.set(`task:${msg.task_id}`, msg.thinking_steps)
            preservedSteps.set(`id:${msg.id}`, msg.thinking_steps)
          }
          if (msg.stream_parts?.length) {
            if (msg.task_id) preservedParts.set(`task:${msg.task_id}`, msg.stream_parts)
            preservedParts.set(`id:${msg.id}`, msg.stream_parts)
          }
        }
        if (preservedSteps.size === 0 && preservedParts.size === 0) return messages
        return messages.map((msg) => {
          if (msg.role !== "assistant") return msg
          const steps =
            (msg.task_id ? preservedSteps.get(`task:${msg.task_id}`) : undefined) ||
            preservedSteps.get(`id:${msg.id}`)
          const parts =
            (msg.task_id ? preservedParts.get(`task:${msg.task_id}`) : undefined) ||
            preservedParts.get(`id:${msg.id}`)
          if (!steps?.length && !parts?.length) return msg
          return {
            ...msg,
            ...(steps?.length ? { thinking_steps: steps } : {}),
            ...(parts?.length ? { stream_parts: parts } : {}),
          }
        })
      })
    },
    [queryClient]
  )

  const replaceCurrentChatMessages = useCallback(
    (messages: ChatMessage[]) => {
      if (!chatId) return
      replaceChatMessagesFor(chatId, messages)
    },
    [chatId, replaceChatMessagesFor]
  )

  const bumpChatActivity = useCallback(
    (chatIdToUpdate: string, at = new Date().toISOString()) => {
      if (!orgId) return
      queryClient.setQueryData<Chat[]>(["chats", orgId], (prev) =>
        prev
          ? prev.map((chat) =>
              chat.id === chatIdToUpdate ? { ...chat, last_activity_at: at } : chat
            )
          : prev
      )
    },
    [orgId, queryClient]
  )

  const mergeToolEvents = useCallback(
    (baseMessages: ChatMessage[], toolMessages: ChatMessage[]): ChatMessage[] => {
      const baseIds = new Set(baseMessages.map((msg) => msg.id))
      const activeTaskIds = new Set(
        baseMessages
          .map((msg) => msg.task_id)
          .filter((taskId): taskId is string => Boolean(taskId))
      )
      const baseToolEventIds = new Set(
        baseMessages
          .map((msg) => msg.tool_event?.id)
          .filter((eventId): eventId is string => Boolean(eventId))
      )
      const merged = [
        ...baseMessages,
        ...toolMessages.filter((msg) => {
          // Drop stale local tool bubbles whose task no longer exists
          // in current branch messages (e.g. after edit/rerun).
          if (msg.task_id && !activeTaskIds.has(msg.task_id)) return false
          if (baseIds.has(msg.id)) return false
          const toolEventId = msg.tool_event?.id
          if (!toolEventId) return true
          return !baseToolEventIds.has(toolEventId)
        }),
      ]
      return [...merged].sort((a, b) => {
        const timeDiff =
          parseChatDate(a.created_at).getTime() - parseChatDate(b.created_at).getTime()
        if (timeDiff !== 0) return timeDiff
        // Stable tie-break within the same task: assistant text, then tool cards.
        if (a.task_id && b.task_id && a.task_id === b.task_id) {
          if (a.role === "assistant" && b.role === "tool") return -1
          if (a.role === "tool" && b.role === "assistant") return 1
        }
        return 0
      })
    },
    [parseChatDate]
  )

  const collapseActivityEvents = useCallback(
    (messages: ChatMessage[]): ChatMessage[] => {
      const debounceMs = 5 * 60 * 1000
      const output: ChatMessage[] = []
      let pending: ChatMessage[] = []

      const flushPending = () => {
        if (pending.length === 0) return
        if (pending.length === 1) {
          output.push(pending[0])
          pending = []
          return
        }
        const first = pending[0]
        const opens = pending.flatMap((item) => {
          const listed = item.activity_event?.opens ?? []
          if (listed.length > 0) return listed
          return [{ viewer: t("chat_anonymous_user"), opened_at: item.created_at }]
        })
        output.push({
          ...first,
          id: `view-group-${first.id}-${pending[pending.length - 1].id}`,
          role: "event",
          activity_event: {
            type: "chat_view",
            count: opens.length,
            opens,
          },
        })
        pending = []
      }

      for (const msg of messages) {
        const isViewEvent = msg.role === "event" && msg.activity_event?.type === "chat_view"
        if (!isViewEvent) {
          flushPending()
          output.push(msg)
          continue
        }
        if (pending.length === 0) {
          pending = [msg]
          continue
        }
        const prev = pending[pending.length - 1]
        const delta =
          parseChatDate(msg.created_at).getTime() - parseChatDate(prev.created_at).getTime()
        if (delta <= debounceMs) {
          pending.push(msg)
        } else {
          flushPending()
          pending = [msg]
        }
      }
      flushPending()
      return output
    },
    [parseChatDate, t]
  )

  const historyChats = useMemo(() => {
    const currentOrgChatIds = new Set(chats.map((chat) => chat.id))
    const sourceChats = chatSearchDebounced
      ? searchedChats.filter((chat) => currentOrgChatIds.has(chat.id))
      : chats
    return [...sourceChats].sort(
      (a, b) =>
        parseChatDate(getChatActivityDate(b)).getTime() -
        parseChatDate(getChatActivityDate(a)).getTime()
    )
  }, [chatSearchDebounced, chats, getChatActivityDate, parseChatDate, searchedChats])

  const historyGroups = useMemo(() => {
    const agentNameById = Object.fromEntries(agents.map((agent) => [agent.id, agent.name]))
    const startOfToday = new Date()
    startOfToday.setHours(0, 0, 0, 0)
    const startOfYesterday = new Date(startOfToday)
    startOfYesterday.setDate(startOfYesterday.getDate() - 1)

    const buckets = new Map<string, typeof historyChats>()
    const order: string[] = []

    for (const chat of historyChats) {
      const date = parseChatDate(getChatActivityDate(chat))
      const day = new Date(date)
      day.setHours(0, 0, 0, 0)
      let label: string
      if (day.getTime() === startOfToday.getTime()) {
        label = t("chat_group_today")
      } else if (day.getTime() === startOfYesterday.getTime()) {
        label = t("chat_group_yesterday")
      } else {
        label = new Intl.DateTimeFormat(locale, {
          month: "short",
          day: "numeric",
          year: date.getFullYear() === startOfToday.getFullYear() ? undefined : "numeric",
        }).format(date)
      }
      if (!buckets.has(label)) {
        buckets.set(label, [])
        order.push(label)
      }
      buckets.get(label)!.push(chat)
    }

    return order.map((label) => ({
      label,
      rows: (buckets.get(label) ?? []).map((chat) => ({
        chat,
        modelLabel: chat.model_id ? modelNameById[chat.model_id] ?? null : null,
        spaceLabel: chat.agent_id ? agentNameById[chat.agent_id] ?? null : null,
      })),
    }))
  }, [
    agents,
    getChatActivityDate,
    historyChats,
    locale,
    modelNameById,
    parseChatDate,
    t,
  ])

  useEffect(() => {
    setSourcesPanelSources(null)
  }, [chatId])

  useEffect(() => {
    if (orgsLoading) return
    if (orgs.length === 0) {
      orgStore.clear()
      setOrgId(null)
      if (chatId) {
        navigate("/chat", { replace: true })
      }
      return
    }
    const storedId = orgId ?? orgStore.get()
    const nextId = storedId && orgs.some((org) => org.id === storedId)
      ? storedId
      : orgs[0].id
    if (nextId !== orgId) {
      orgStore.set(nextId)
      setOrgId(nextId)
    }
  }, [chatId, navigate, orgId, orgs, orgsLoading])

  useEffect(() => {
    if (!shareTokenParam || chatId) return
    let cancelled = false
    chatApi
      .resolveShared(shareTokenParam)
      .then((resolved) => {
        if (cancelled) return
        navigate(`/chat/${resolved.chat_id}`, {
          replace: true,
        })
      })
      .catch(() => {
        if (cancelled) return
        navigate("/chat", {
          replace: true,
          state: { blockedSharedChat: true },
        })
      })
    return () => {
      cancelled = true
    }
  }, [chatId, navigate, shareTokenParam])

  useEffect(() => {
    if (!chatId) return
    const params = new URLSearchParams(location.search)
    if (!params.has("share")) return
    params.delete("share")
    const nextSearch = params.toString()
    navigate(
      {
        pathname: location.pathname,
        search: nextSearch ? `?${nextSearch}` : "",
      },
      { replace: true }
    )
  }, [chatId, location.pathname, location.search, navigate])

  useEffect(() => {
    if (!chatId) return
    if (!(messagesError instanceof ApiError)) return
    if (messagesError.status === 404) {
      navigate("/chat", { replace: true })
      return
    }
    if (messagesError.status !== 403 || messagesError.detail !== "CHAT_NOT_SHARED") return
    navigate("/chat", {
      replace: true,
      state: { blockedSharedChat: true },
    })
  }, [chatId, messagesError, navigate])

  useEffect(() => {
    const state = location.state as { blockedSharedChat?: boolean } | null
    if (!state?.blockedSharedChat) return
    setBlockedLinkDialogOpen(true)
    navigate(`${location.pathname}${location.search}`, { replace: true, state: null })
  }, [location.pathname, location.search, location.state, navigate])

  useEffect(() => {
    if (selectableChatModels.length === 0) return
    if (selectedModel && selectableChatModels.some((model) => model.id === selectedModel)) return
    const stored = modelStore.get()
    if (stored && selectableChatModels.some((model) => model.id === stored)) {
      setSelectedModel(stored)
      return
    }
    setSelectedModel(selectableChatModels[0].id)
  }, [selectableChatModels, selectedModel])

  const activeChat = useMemo(() => {
    const fromList = chats.find((item) => item.id === chatId)
    if (fromList) return fromList
    if (chatId && sessionOwnedChatIds.includes(chatId)) {
      return {
        id: chatId,
        created_at: "",
        last_activity_at: "",
        is_incognito: true,
      }
    }
    // Keep a temporary stub while the chat list is still loading.
    if (chatId && !chatsFetched) {
      return {
        id: chatId,
        created_at: "",
        last_activity_at: "",
      }
    }
    return null
  }, [chatId, chats, chatsFetched, sessionOwnedChatIds])
  const activeAgentId = activeAgentIdFromQuery ?? activeChat?.agent_id ?? null
  const isAgentMode = Boolean(activeAgentId)
  const isSharedView = Boolean(
    chatId &&
      orgId &&
      chatsFetched &&
      !chats.some((item) => item.id === chatId) &&
      !sessionOwnedChatIds.includes(chatId)
  )
  const currentChatLoading = Boolean(chatId && loadingByChat[chatId])

  const isChatSwitchRef = useRef(false)

  useEffect(() => {
    setToolEvents([])
    lastScrolledKeyRef.current = null
    deepLinkedScrolledRef.current = null
    isChatSwitchRef.current = true
    setAutoScrollEnabled(true)
    if (chatId) chatGenerationIndicatorsStore.markRead(chatId)
    // Keep task subscriptions/polling for other chats so background generations
    // still update sidebar indicators and can be stopped after a reload.
  }, [chatId])

  useEffect(() => {
    if (selectedModel) {
      modelStore.set(selectedModel)
    }
  }, [selectedModel])

  useEffect(() => {
    setReasoningStopIndex(
      resolveReasoningStopIndex(
        selectedChatModel?.reasoning_effort,
        providerReasoningLevels
      )
    )
  }, [selectedChatModel?.id, selectedChatModel?.reasoning_effort, providerReasoningLevels])

  useEffect(() => {
    webSearchEnabledStore.set(webSearchEnabled)
  }, [webSearchEnabled])

  useEffect(() => {
    codeExecutionEnabledStore.set(codeExecutionEnabled)
  }, [codeExecutionEnabled])

  useEffect(() => {
    window.sessionStorage.setItem("chatui_incognito_enabled", incognitoEnabled ? "1" : "0")
  }, [incognitoEnabled])

  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key === "chatui_toolcall_logs_visible") {
        setActionInfoLevel(actionInfoLevelStore.parse(event.newValue))
      }
    }
    const onFocus = () => {
      setActionInfoLevel(actionInfoLevelStore.get())
    }
    window.addEventListener("storage", onStorage)
    window.addEventListener("focus", onFocus)
    return () => {
      window.removeEventListener("storage", onStorage)
      window.removeEventListener("focus", onFocus)
    }
  }, [])

  useEffect(() => {
    if (!chatId) return
    if (loadingByChat[chatId]) return
    // Wait for messages so catch-up can attach to the assistant bubble.
    // Otherwise the cursor advances against an empty list and live deltas
    // create a partial timeline that hides persisted content.
    if (isMessagesLoading) return
    let cancelled = false
    const resumeTasks = async () => {
      try {
        const tasks = await chatApi.listGenerationTasks(chatId, true)
        if (cancelled) return
        if (tasks.length === 0) {
          const cachedMessages =
            queryClient.getQueryData<ChatMessage[]>(["chatMessages", chatId]) ?? []
          if (cachedMessages.length > 0) {
            return
          }
          const freshMessages = await chatApi.messages(chatId)
          if (!cancelled) {
            replaceChatMessagesFor(chatId, freshMessages)
          }
          return
        }
        let attached = 0
        for (const task of tasks) {
          if (cancelled) return
          const messages =
            queryClient.getQueryData<ChatMessage[]>(["chatMessages", chatId]) ?? []
          const hasAssistant = messages.some(
            (msg) => msg.role === "assistant" && msg.id === task.assistant_message_id
          )
          if (!hasAssistant) {
            // Messages query may still be empty/stale; retry on next effect run.
            continue
          }
          taskCursorRef.current[task.id] = taskCursorRef.current[task.id] ?? 0
          updateChatMessagesFor(chatId, (prev) =>
            prev.map((msg) =>
              msg.id === task.assistant_message_id
                ? {
                    ...msg,
                    task_id: task.id,
                    generation_status: task.status,
                  }
                : msg
            )
          )
          // Catch up from DB first, then subscribe from the advanced cursor so
          // activity/tool events are not applied twice on refresh.
          await fetchTaskEvents(chatId, task.id, task.assistant_message_id)
          if (cancelled) return
          subscribeToTask(chatId, task.id, task.assistant_message_id)
          attached += 1
        }
        if (attached > 0) {
          setChatGenerating(chatId, true)
        }
      } catch {
        // ignore resume errors
      }
    }
    resumeTasks()
    return () => {
      cancelled = true
    }
  }, [
    chatId,
    fetchTaskEvents,
    subscribeToTask,
    updateChatMessagesFor,
    replaceChatMessagesFor,
    loadingByChat,
    isMessagesLoading,
    // Length is enough to retry once messages appear; avoid re-running on every delta.
    serverMessages.length,
    queryClient,
    setChatGenerating,
  ])

  useEffect(() => {
    // After reload, re-attach to generating chats that are not currently open.
    const generatingIds = chatGenerationIndicatorsStore
      .generatingChatIds()
      .filter((id) => id !== chatId)
    if (generatingIds.length === 0) return
    let cancelled = false
    const reconcile = async () => {
      for (const targetChatId of generatingIds) {
        if (cancelled) return
        try {
          const tasks = await chatApi.listGenerationTasks(targetChatId, true)
          if (cancelled) return
          if (tasks.length === 0) {
            setChatGenerating(targetChatId, false)
            continue
          }
          setChatGenerating(targetChatId, true)
          for (const task of tasks) {
            if (cancelled) return
            if (
              taskSubscriptionsRef.current[task.id] ||
              taskPollingRef.current[task.id]
            ) {
              continue
            }
            taskCursorRef.current[task.id] = taskCursorRef.current[task.id] ?? 0
            subscribeToTask(targetChatId, task.id, task.assistant_message_id)
          }
        } catch {
          // ignore per-chat reconcile errors
        }
      }
    }
    void reconcile()
    return () => {
      cancelled = true
    }
  }, [chatId, setChatGenerating, subscribeToTask])

  const visibleMessages = useMemo(() => {
    const nestedToolEventIds = new Set<string>()
    const nestedActionKeys = new Set<string>()
    for (const msg of serverMessages) {
      if (msg.role !== "assistant" || !msg.stream_parts?.length) continue
      for (const part of msg.stream_parts) {
        if (part.type !== "action" || !part.tool_event) continue
        if (part.tool_event.id) nestedToolEventIds.add(part.tool_event.id)
        nestedActionKeys.add(
          msg.task_id ? `${msg.task_id}:${part.label}` : `id:${msg.id}:${part.label}`
        )
      }
    }
    const merged = mergeToolEvents(serverMessages, toolEvents).filter((msg) => {
      if (!msg.tool_event) return true
      if (actionInfoLevel !== "detailed") return false
      const event = msg.tool_event
      if (event.id && nestedToolEventIds.has(event.id)) return false
      // Hide generic tool_call / specialized bubbles already nested under a timeline action.
      for (const assistant of serverMessages) {
        if (assistant.role !== "assistant" || !assistant.stream_parts?.length) continue
        if (msg.task_id && assistant.task_id && msg.task_id !== assistant.task_id) continue
        for (const part of assistant.stream_parts) {
          if (part.type !== "action" || !part.tool_event) continue
          if (!actionLabelMatchesToolEvent(part.label, event)) continue
          const key = assistant.task_id
            ? `${assistant.task_id}:${part.label}`
            : `id:${assistant.id}:${part.label}`
          if (nestedActionKeys.has(key)) return false
        }
      }
      return true
    })
    return collapseActivityEvents(merged)
  }, [
    collapseActivityEvents,
    mergeToolEvents,
    serverMessages,
    toolEvents,
    actionInfoLevel,
  ])

  const scrollToBottom = useCallback((behavior: ScrollBehavior = "smooth") => {
    const container = messagesContainerRef.current
    if (container) {
      container.scrollTo({ top: container.scrollHeight, behavior })
      return
    }
    messagesEndRef.current?.scrollIntoView({ behavior, block: "end" })
  }, [])

  const scrollToMessageStart = useCallback(
    (messageId: string, behavior: ScrollBehavior = "smooth") => {
      const container = messagesContainerRef.current
      const target = container?.querySelector(`[data-message-id="${messageId}"]`)
      if (!container || !(target instanceof HTMLElement)) {
        scrollToBottom(behavior)
        return
      }
      const top =
        target.getBoundingClientRect().top -
        container.getBoundingClientRect().top +
        container.scrollTop
      container.scrollTo({ top, behavior })
    },
    [scrollToBottom]
  )

  const handleMessagesScroll = useCallback(() => {
    const container = messagesContainerRef.current
    if (!container) return
    const threshold = 80
    const distanceFromBottom =
      container.scrollHeight - container.scrollTop - container.clientHeight
    setAutoScrollEnabled(distanceFromBottom <= threshold)
  }, [])

  useEffect(() => {
    // Never fight the user — even while a reply is streaming.
    if (!autoScrollEnabled) return

    // Tool/event rows are excluded so they can't change the scroll target mid-turn.
    const conversationMessages = visibleMessages.filter(
      (msg) => msg.role === "user" || msg.role === "assistant"
    )
    const lastMsg = conversationMessages[conversationMessages.length - 1]
    if (!lastMsg) return

    // Index-based key survives temp→server id remaps and task_id assignment.
    const scrollKey = `${lastMsg.role}:${conversationMessages.length}`
    if (scrollKey === lastScrolledKeyRef.current) return
    lastScrolledKeyRef.current = scrollKey

    const behavior: ScrollBehavior = isChatSwitchRef.current ? "instant" : "smooth"
    isChatSwitchRef.current = false
    if (lastMsg.role === "user") {
      scrollToBottom(behavior)
    } else {
      scrollToMessageStart(lastMsg.id, behavior)
    }
  }, [visibleMessages, autoScrollEnabled, scrollToBottom, scrollToMessageStart])

  const deepLinkedMessageId = useMemo(() => {
    const params = new URLSearchParams(location.search)
    return params.get("message")
  }, [location.search])

  useEffect(() => {
    if (!chatId || !deepLinkedMessageId || isMessagesLoading) return
    const scrollKey = `${chatId}:${deepLinkedMessageId}`
    if (deepLinkedScrolledRef.current === scrollKey) return
    const exists = visibleMessages.some((msg) => msg.id === deepLinkedMessageId)
    if (!exists) return
    deepLinkedScrolledRef.current = scrollKey
    setAutoScrollEnabled(false)
    lastScrolledKeyRef.current = `deep-link:${deepLinkedMessageId}`
    const frame = window.requestAnimationFrame(() => {
      scrollToMessageStart(deepLinkedMessageId, "smooth")
    })
    return () => window.cancelAnimationFrame(frame)
  }, [
    chatId,
    deepLinkedMessageId,
    isMessagesLoading,
    scrollToMessageStart,
    visibleMessages,
  ])

  const startNewChat = () => {
    replaceCurrentChatMessages([])
    setToolEvents([])
    composerRef.current?.setValue("")
    setPendingAttachments([])
    setAttachmentError(null)
    navigate(
      isAgentMode && activeAgentId
        ? `/chat?agent=${encodeURIComponent(activeAgentId)}`
        : "/chat",
      { replace: true }
    )
  }

  const createChat = async (): Promise<Chat | null> => {
    if (!orgId) return null
    const chat = await createChatMutation.mutateAsync({
      model_id: selectedModel,
      agent_id: isAgentMode && activeAgentId ? activeAgentId : undefined,
      is_incognito: incognitoEnabled,
    })
    setSessionOwnedChatIds(rememberSessionOwnedChatId(chat.id))
    navigate(
      isAgentMode && activeAgentId
        ? `/chat/${chat.id}?agent=${encodeURIComponent(activeAgentId)}`
        : `/chat/${chat.id}`,
      { replace: true }
    )
    replaceChatMessagesFor(chat.id, [])
    refetchChats().catch(() => null)
    return chat
  }

  const uploadAttachmentsForChat = useCallback(async (
    targetChatId: string,
    attachments: ChatMessageAttachmentInput[]
  ): Promise<ChatMessageAttachmentInput[]> => {
    if (attachments.length === 0) return []
    const uploaded: ChatMessageAttachmentInput[] = []
    for (const attachment of attachments) {
      const reusableId = attachment.upload_id || extractAttachmentIdFromContentUrl(attachment.content_url)
      if (reusableId) {
        uploaded.push({
          upload_id: reusableId,
          file_name: attachment.file_name,
          content_type: attachment.content_type,
          data_base64: attachment.data_base64,
          content_url: attachment.content_url,
        })
        continue
      }
      if (!attachment.data_base64) {
        throw new Error(`Attachment ${attachment.file_name} is missing data.`)
      }
      const created = await chatApi.uploadAttachment(targetChatId, {
        file_name: attachment.file_name,
        content_type: attachment.content_type,
        data_base64: attachment.data_base64,
      })
      uploaded.push({
        upload_id: created.id,
        file_name: created.file_name,
        content_type: created.content_type,
        data_base64: attachment.data_base64,
        content_url: attachment.content_url,
      })
    }
    return uploaded
  }, [])

  const deleteChat = async (chatIdToDelete: string) => {
    await deleteChatMutation.mutateAsync(chatIdToDelete)
    if (chatId === chatIdToDelete) {
      replaceCurrentChatMessages([])
      setToolEvents([])
      navigate("/chat", { replace: true })
    }
  }

  const toggleShareChat = async (chat: Chat) => {
    if (chat.is_shared) {
      await chatApi.unshare(chat.id)
      queryClient.setQueryData<Chat[]>(["chats", orgId], (prev) =>
        prev
          ? prev.map((item) =>
              item.id === chat.id
                ? {
                    ...item,
                    is_shared: false,
                  }
                : item
            )
          : prev
      )
      return
    }
    await chatApi.share(chat.id)
    queryClient.setQueryData<Chat[]>(["chats", orgId], (prev) =>
      prev
        ? prev.map((item) =>
            item.id === chat.id
              ? {
                  ...item,
                  is_shared: true,
                }
              : item
          )
        : prev
    )
    const fullUrl = `${window.location.origin}/chat/${chat.id}`
    try {
      await navigator.clipboard.writeText(fullUrl)
      toast.success(t("chat_share_dialog_copied"))
    } catch {
      toast.message(t("chat_share_dialog_title"), {
        description: fullUrl,
      })
    }
  }

  const shareMessage = useCallback(
    async (msg: ChatMessage) => {
      if (!chatId || !activeChat) return
      if (msg.id.startsWith("temp-")) return
      if (activeChat.is_incognito) {
        toast.error(t("chat_share_incognito_blocked"))
        return
      }
      if (!activeChat.is_shared) {
        await chatApi.share(chatId)
        queryClient.setQueryData<Chat[]>(["chats", orgId], (prev) =>
          prev
            ? prev.map((item) =>
                item.id === chatId
                  ? {
                      ...item,
                      is_shared: true,
                    }
                  : item
              )
            : prev
        )
      }
      const fullUrl = `${window.location.origin}/chat/${chatId}?message=${encodeURIComponent(msg.id)}`
      try {
        await navigator.clipboard.writeText(fullUrl)
        toast.success(t("chat_share_dialog_copied"))
      } catch {
        toast.message(t("chat_share_dialog_title"), {
          description: fullUrl,
        })
      }
    },
    [activeChat, chatId, orgId, queryClient, t]
  )

  const sendMessage = async () => {
    if (composerBusyRef.current) return
    const trimmed = (composerRef.current?.getValue() ?? "").trim()
    if (!trimmed && pendingAttachments.length === 0) return
    if (chatId && loadingByChat[chatId]) {
      stopGeneration()
    }
    setAttachmentError(null)
    setAutoScrollEnabled(true)
    lastScrolledKeyRef.current = null
    let requestChatId: string | null = null
    let streamCancel: (() => void) | null = null
    let generationEpoch = 0
    composerBusyRef.current = true
    try {
      let chat = activeChat
      if (isAgentMode && activeAgentId && chat && chat.agent_id !== activeAgentId) {
        chat = null
      }
      if (!chat) {
        chat = await createChat()
      }
      if (!chat) return
      requestChatId = chat.id
      let uploadedAttachments: ChatMessageAttachmentInput[] = []
      try {
        setIsUploadingAttachments(true)
        uploadedAttachments = await uploadAttachmentsForChat(chat.id, pendingAttachments)
      } catch (error) {
        const message =
          error instanceof Error ? error.message : t("chat_attachment_upload_failed")
        setAttachmentError(message)
        return
      } finally {
        setIsUploadingAttachments(false)
      }
      await queryClient.cancelQueries({ queryKey: ["chatMessages", chat.id] })
      generationEpoch = ++generationEpochRef.current
      setChatGenerating(chat.id, true)
      const updateMessages = (updater: (prev: ChatMessage[]) => ChatMessage[]) =>
        updateChatMessagesFor(chat.id, updater)
      const activityAt = new Date().toISOString()
      bumpChatActivity(chat.id, activityAt)
      const tempUserId = `temp-user-${Date.now()}`
      const userMessage: ChatMessage = {
        id: tempUserId,
        role: "user",
        content: trimmed,
        created_at: activityAt,
        attachments: uploadedAttachments,
      }
      const assistantId = `temp-assistant-${Date.now()}`
      const assistantMessage: ChatMessage = {
        id: assistantId,
        role: "assistant",
        content: "",
        created_at: activityAt,
        model_id: selectedModel ?? null,
        model_name: selectedModel ? modelNameById[selectedModel] ?? null : null,
        generation_status: "queued",
      }
      updateMessages((prev) => [...prev, userMessage, assistantMessage])
      composerRef.current?.setValue("")
      setPendingAttachments([])
      const { promise, cancel } = chatApi.sendMessageStream(
        chat.id,
        trimmed,
        selectedModel,
        uploadedAttachments.map((item) => ({
          upload_id: item.upload_id,
          file_name: item.file_name,
          content_type: item.content_type,
        })),
        webSearchEnabled,
        codeExecutionEnabled,
        selectedReasoningEffort,
        locale,
        (event) => {
          applyStreamEvent(chat.id, assistantId, event)
        }
      )
      streamCancel = cancel
      currentCancelRef.current = cancel
      composerBusyRef.current = false
      try {
        await promise
        if (generationEpochRef.current === generationEpoch) {
          refetchChats()
        }
      } catch {
        if (generationEpochRef.current !== generationEpoch) return
        updateMessages((prev) =>
          prev.map((msg) =>
            msg.id === assistantId
              ? {
                  ...msg,
                  content: msg.content?.trim() ? msg.content : t("chat_generation_failed"),
                  thinking_steps: [],
                  generation_status: "failed",
                }
              : msg
          )
        )
        if (chat.id) {
          chatApi
            .messages(chat.id)
            .then((data) => replaceChatMessagesFor(chat.id, data))
            .catch(() => null)
        }
      }
    } finally {
      composerBusyRef.current = false
      setIsUploadingAttachments(false)
      if (streamCancel && currentCancelRef.current === streamCancel) {
        currentCancelRef.current = null
      }
      if (requestChatId && generationEpochRef.current === generationEpoch) {
        setChatGenerating(requestChatId, false)
      }
    }
  }

  const handleFilesSelected = async (files: File[]) => {
    if (
      rejectUnsupportedImageAttachments(
        files.map((file) => ({ content_type: file.type })),
        setAttachmentError
      )
    ) {
      return
    }
    if (pendingAttachments.length + files.length > ATTACHMENTS_MAX_FILES) {
      setAttachmentError(
        tCount("chat_attachment_limit_file", "chat_attachment_limit_files", ATTACHMENTS_MAX_FILES)
      )
      return
    }
    for (const file of files) {
      if (file.size > ATTACHMENTS_MAX_FILE_BYTES) {
        setAttachmentError(
          t("chat_attachment_limit_file_size", {
            file: file.name || t("chat_attachment_fallback_name"),
            max_mb: String(Math.round(ATTACHMENTS_MAX_FILE_BYTES / 1_000_000)),
          })
        )
        return
      }
    }
    const currentTotal = pendingAttachments.reduce(
      (sum, item) => sum + estimateBase64Bytes(item.data_base64 || ""),
      0
    )
    const incomingTotal = files.reduce((sum, file) => sum + file.size, 0)
    if (currentTotal + incomingTotal > ATTACHMENTS_MAX_TOTAL_BYTES) {
      setAttachmentError(
        t("chat_attachment_limit_total_size", {
          max_mb: String(Math.round(ATTACHMENTS_MAX_TOTAL_BYTES / 1_000_000)),
        })
      )
      return
    }
    const next = await readFilesAsAttachments(files)
    if (next.length === 0) return
    setAttachmentError(null)
    setPendingAttachments((prev) => [...prev, ...next])
  }

  const isLikelyCode = (text: string) => {
    if (!text.includes("\n")) return false
    if (text.includes("```")) return false
    const lines = text
      .split("\n")
      .map((line) => line.trimEnd())
      .filter((line) => line.trim().length > 0)
    if (lines.length < 2) return false

    // Treat stack/code-reference style prefixes as code, e.g. foo@bar:baz# or foo@bar:baz~
    if (lines.some((line) => /^\s*[^@\s]+@[^:\s]+:[^\s]*[#~]/.test(line))) {
      return true
    }

    // Markdown structure — don't wrap docs/notes that merely share tokens with code.
    const mdHeadingCount = lines.filter((line) =>
      /^#{1,6}\s+\S/.test(line.trim())
    ).length
    const mdListCount = lines.filter((line) =>
      /^([-*+]|\d+[.)])\s+\S/.test(line.trim())
    ).length
    const mdBlockquoteCount = lines.filter((line) =>
      /^>\s?\S/.test(line.trim())
    ).length
    const mdLinkCount = (text.match(/!?\[[^\]]*]\([^)]+\)/g) || []).length
    const mdTableCount = lines.filter((line) =>
      /^\|.+\|\s*$/.test(line.trim())
    ).length
    let markdownSignals = 0
    if (mdHeadingCount >= 1) markdownSignals += 1
    if (mdHeadingCount >= 3) markdownSignals += 1
    if (mdListCount >= 2) markdownSignals += 1
    if (mdListCount >= 4) markdownSignals += 1
    if (mdLinkCount >= 1) markdownSignals += 1
    if (mdLinkCount >= 3) markdownSignals += 1
    if (mdBlockquoteCount >= 2) markdownSignals += 1
    if (mdTableCount >= 2) markdownSignals += 1
    if (markdownSignals >= 2) return false

    // Prefer code-shaped keywords over prose starters ("If …", "For …", "Create …").
    const hardKeywordLineCount = lines.filter((line) =>
      /^(def\s+\w+|import\s+\w+|from\s+\S+\s+import\b|function\s+\w+|const\s+\w+|let\s+\w+|var\s+\w+|#include\b|SELECT\s+\S|INSERT\s+INTO\b|UPDATE\s+\S|DELETE\s+FROM\b|WITH\s+\w+\s+AS\b|CREATE\s+(TABLE|INDEX|VIEW|DATABASE|SCHEMA|OR\s+REPLACE)\b|ALTER\s+TABLE\b|DROP\s+(TABLE|INDEX|VIEW|DATABASE|SCHEMA)\b|\/\/|\/\*)/i.test(
        line.trim()
      )
    ).length
    const softKeywordLineCount = lines.filter((line) =>
      /^(class\s+\w+(\s*[:{(]|\s+extends\b|\s+implements\b)|if\s*\(|for\s*\(|while\s*\(|return\s*([;([]|true\b|false\b|null\b|None\b|undefined\b)|--\s)/i.test(
        line.trim()
      )
    ).length
    const keywordLineCount = hardKeywordLineCount + softKeywordLineCount
    const indentedLineCount = lines.filter((line) => /^( {4,}|\t)/.test(line)).length
    // Ignore markdown links/autolinks so [text](url) does not count as "symbol heavy".
    const strippedForSymbols = text
      .replace(/!?\[[^\]]*]\([^)]+\)/g, " ")
      .replace(/<https?:\/\/[^>\s]+>/gi, " ")
    const symbolHeavy = /[{}();=$\\]/.test(strippedForSymbols)
    // Omit bare `--` (common in markdown/CLI prose); keep `++` / `=>` / comparisons.
    const operatorHeavy = /(\+\+|=>|==|!=|<=|>=|:=|&&|\|\|)/.test(text)
    const proseLikeLineCount = lines.filter((line) =>
      /^[A-Za-z0-9 ,.'"!?()-]+$/.test(line.trim())
    ).length

    let score = 0
    if (hardKeywordLineCount >= 1) score += 2
    else if (softKeywordLineCount >= 2) score += 2
    else if (softKeywordLineCount >= 1) score += 1
    if (indentedLineCount >= 2) score += 1
    if (symbolHeavy) score += 1
    if (operatorHeavy) score += 1
    if (markdownSignals >= 1) score -= 1

    const proseRatio = proseLikeLineCount / lines.length
    if (score < 2) return false
    if (proseRatio > 0.75 && keywordLineCount === 0 && !symbolHeavy) return false
    return true
  }

  const wrapInCodeFence = (text: string) => {
    const trimmed = text.replace(/\s+$/, "")
    return `\`\`\`\n${trimmed}\n\`\`\``
  }

  const isInsideMarkdownCodeFence = (text: string, cursorIndex: number) => {
    const beforeCursor = text.slice(0, cursorIndex)
    const lines = beforeCursor.split("\n")
    let inFence = false
    let activeFenceChar: "`" | "~" | null = null
    let activeFenceLength = 0

    for (const line of lines) {
      const trimmedStart = line.trimStart()
      const fenceMatch = /^(`{3,}|~{3,})/.exec(trimmedStart)
      if (!fenceMatch) continue
      const marker = fenceMatch[1]
      const markerChar = marker[0] as "`" | "~"
      const markerLength = marker.length
      if (!inFence) {
        inFence = true
        activeFenceChar = markerChar
        activeFenceLength = markerLength
        continue
      }
      if (activeFenceChar === markerChar && markerLength >= activeFenceLength) {
        inFence = false
        activeFenceChar = null
        activeFenceLength = 0
      }
    }

    return inFence
  }

  const insertAtCursor = (
    current: string,
    insert: string,
    start: number,
    end: number
  ) => {
    return current.slice(0, start) + insert + current.slice(end)
  }

  const insertPromptIntoComposer = useCallback((body: string) => {
    const input = composerInputRef.current
    const current = composerRef.current?.getValue() ?? input?.value ?? ""
    const start = input?.selectionStart ?? current.length
    const end = input?.selectionEnd ?? current.length
    const spacer =
      start > 0 && !/\s$/.test(current.slice(0, start)) && body.length > 0 ? "\n\n" : ""
    const nextValue = insertAtCursor(current, `${spacer}${body}`, start, end)
    composerRef.current?.setValue(nextValue)
    requestAnimationFrame(() => {
      const el = composerInputRef.current
      if (!el) return
      const cursor = start + spacer.length + body.length
      el.focus()
      el.setSelectionRange(cursor, cursor)
    })
  }, [])

  const openInsertPromptPicker = useCallback(async () => {
    try {
      const items = await promptApi.list({
        context_agent_id: activeAgentId ?? null,
      })
      setInsertPrompts(items)
      setInsertPromptOpen(true)
      setPromptCount(items.length)
    } catch {
      setInsertPrompts([])
    }
  }, [activeAgentId])

  const saveMessageAsPrompt = useCallback((msg: ChatMessage) => {
    const body =
      msg.role === "user" ? msg.content.trim() : getFinalAnswerText(msg).trim()
    if (!body) return
    setSavePromptBody(body)
    setSavePromptOpen(true)
  }, [])

  const handlePasteAttachments = async (
    event: React.ClipboardEvent<HTMLTextAreaElement>
  ) => {
    const items = event.clipboardData.items
    const next = await readClipboardImagesAsAttachments(items)
    if (next.length > 0) {
      if (rejectUnsupportedImageAttachments(next, setAttachmentError)) {
        event.preventDefault()
        return
      }
      if (pendingAttachments.length + next.length > ATTACHMENTS_MAX_FILES) {
        event.preventDefault()
        setAttachmentError(
          tCount("chat_attachment_limit_file", "chat_attachment_limit_files", ATTACHMENTS_MAX_FILES)
        )
        return
      }
      for (const attachment of next) {
        const size = estimateBase64Bytes(attachment.data_base64 || "")
        if (size > ATTACHMENTS_MAX_FILE_BYTES) {
          event.preventDefault()
          setAttachmentError(
            t("chat_attachment_limit_file_size", {
              file: attachment.file_name || t("chat_attachment_fallback_name"),
              max_mb: String(Math.round(ATTACHMENTS_MAX_FILE_BYTES / 1_000_000)),
            })
          )
          return
        }
      }
      const currentTotal = pendingAttachments.reduce(
        (sum, item) => sum + estimateBase64Bytes(item.data_base64 || ""),
        0
      )
      const incomingTotal = next.reduce(
        (sum, item) => sum + estimateBase64Bytes(item.data_base64 || ""),
        0
      )
      if (currentTotal + incomingTotal > ATTACHMENTS_MAX_TOTAL_BYTES) {
        event.preventDefault()
        setAttachmentError(
          t("chat_attachment_limit_total_size", {
            max_mb: String(Math.round(ATTACHMENTS_MAX_TOTAL_BYTES / 1_000_000)),
          })
        )
        return
      }
      event.preventDefault()
      setAttachmentError(null)
      setPendingAttachments((prev) => [...prev, ...next])
      return
    }

    const text = event.clipboardData.getData("text")
    if (!text || !isLikelyCode(text)) return

    const input = composerInputRef.current
    const current = composerRef.current?.getValue() ?? input?.value ?? ""
    const start = input?.selectionStart ?? current.length
    if (isInsideMarkdownCodeFence(current, start)) {
      return
    }

    event.preventDefault()
    const end = input?.selectionEnd ?? current.length
    const wrapped = wrapInCodeFence(text)
    const nextValue = insertAtCursor(current, wrapped, start, end)
    composerRef.current?.setValue(nextValue)
    requestAnimationFrame(() => {
      if (!input) return
      const nextPos = start + wrapped.length
      input.selectionStart = nextPos
      input.selectionEnd = nextPos
    })
  }

  const removePendingAttachment = (index: number) => {
    setAttachmentError(null)
    setPendingAttachments((prev) => prev.filter((_, idx) => idx !== index))
  }

  const addEditingFiles = useCallback(async (files: File[]) => {
    if (
      rejectUnsupportedImageAttachments(
        files.map((file) => ({ content_type: file.type })),
        setEditingAttachmentError
      )
    ) {
      return
    }
    if (editingAttachments.length + files.length > ATTACHMENTS_MAX_FILES) {
      setEditingAttachmentError(
        tCount("chat_attachment_limit_file", "chat_attachment_limit_files", ATTACHMENTS_MAX_FILES)
      )
      return
    }
    for (const file of files) {
      if (file.size > ATTACHMENTS_MAX_FILE_BYTES) {
        setEditingAttachmentError(
          t("chat_attachment_limit_file_size", {
            file: file.name || t("chat_attachment_fallback_name"),
            max_mb: String(Math.round(ATTACHMENTS_MAX_FILE_BYTES / 1_000_000)),
          })
        )
        return
      }
    }
    const currentTotal = editingAttachments.reduce(
      (sum, item) => sum + estimateBase64Bytes(item.data_base64 || ""),
      0
    )
    const incomingTotal = files.reduce((sum, file) => sum + file.size, 0)
    if (currentTotal + incomingTotal > ATTACHMENTS_MAX_TOTAL_BYTES) {
      setEditingAttachmentError(
        t("chat_attachment_limit_total_size", {
          max_mb: String(Math.round(ATTACHMENTS_MAX_TOTAL_BYTES / 1_000_000)),
        })
      )
      return
    }
    const next = await readFilesAsAttachments(files)
    if (next.length === 0) return
    setEditingAttachmentError(null)
    setEditingAttachments((prev) => [...prev, ...next])
  }, [editingAttachments, rejectUnsupportedImageAttachments, t])

  const handleEditFilesSelected = useCallback((files: File[]) => {
    void addEditingFiles(files)
  }, [addEditingFiles])

  const handleComposerDragEnter = (event: React.DragEvent<HTMLDivElement>) => {
    if (!event.dataTransfer.types.includes("Files")) return
    event.preventDefault()
    setIsDragActive(true)
  }

  const handleComposerDragOver = (event: React.DragEvent<HTMLDivElement>) => {
    if (!event.dataTransfer.types.includes("Files")) return
    event.preventDefault()
    event.dataTransfer.dropEffect = "copy"
    if (!isDragActive) setIsDragActive(true)
  }

  const handleComposerDragLeave = (event: React.DragEvent<HTMLDivElement>) => {
    if (event.currentTarget.contains(event.relatedTarget as Node)) return
    setIsDragActive(false)
  }

  const handleComposerDrop = async (
    event: React.DragEvent<HTMLDivElement>
  ) => {
    if (!event.dataTransfer.types.includes("Files")) return
    event.preventDefault()
    setIsDragActive(false)
    const files = Array.from(event.dataTransfer.files ?? [])
    if (files.length === 0) return
    if (
      rejectUnsupportedImageAttachments(
        files.map((file) => ({ content_type: file.type })),
        setAttachmentError
      )
    ) {
      return
    }
    if (pendingAttachments.length + files.length > ATTACHMENTS_MAX_FILES) {
      setAttachmentError(
        tCount("chat_attachment_limit_file", "chat_attachment_limit_files", ATTACHMENTS_MAX_FILES)
      )
      return
    }
    for (const file of files) {
      if (file.size > ATTACHMENTS_MAX_FILE_BYTES) {
        setAttachmentError(
          t("chat_attachment_limit_file_size", {
            file: file.name || t("chat_attachment_fallback_name"),
            max_mb: String(Math.round(ATTACHMENTS_MAX_FILE_BYTES / 1_000_000)),
          })
        )
        return
      }
    }
    const currentTotal = pendingAttachments.reduce(
      (sum, item) => sum + estimateBase64Bytes(item.data_base64 || ""),
      0
    )
    const incomingTotal = files.reduce((sum, file) => sum + file.size, 0)
    if (currentTotal + incomingTotal > ATTACHMENTS_MAX_TOTAL_BYTES) {
      setAttachmentError(
        t("chat_attachment_limit_total_size", {
          max_mb: String(Math.round(ATTACHMENTS_MAX_TOTAL_BYTES / 1_000_000)),
        })
      )
      return
    }
    const next = await readFilesAsAttachments(files)
    if (next.length > 0) {
      setAttachmentError(null)
      setPendingAttachments((prev) => [...prev, ...next])
    }
  }

  const handleEditPasteAttachments = useCallback(async (
    event: React.ClipboardEvent<HTMLTextAreaElement>
  ) => {
    const textarea = event.currentTarget
    const items = event.clipboardData.items
    const text = event.clipboardData.getData("text")
    const next = await readClipboardImagesAsAttachments(items)
    if (next.length > 0) {
      event.preventDefault()
      if (editingAttachments.length + next.length > ATTACHMENTS_MAX_FILES) {
        setEditingAttachmentError(
          tCount("chat_attachment_limit_file", "chat_attachment_limit_files", ATTACHMENTS_MAX_FILES)
        )
        return
      }
      for (const attachment of next) {
        const size = estimateBase64Bytes(attachment.data_base64 || "")
        if (size > ATTACHMENTS_MAX_FILE_BYTES) {
          setEditingAttachmentError(
            t("chat_attachment_limit_file_size", {
              file: attachment.file_name || t("chat_attachment_fallback_name"),
              max_mb: String(Math.round(ATTACHMENTS_MAX_FILE_BYTES / 1_000_000)),
            })
          )
          return
        }
      }
      const currentTotal = editingAttachments.reduce(
        (sum, item) => sum + estimateBase64Bytes(item.data_base64 || ""),
        0
      )
      const incomingTotal = next.reduce(
        (sum, item) => sum + estimateBase64Bytes(item.data_base64 || ""),
        0
      )
      if (currentTotal + incomingTotal > ATTACHMENTS_MAX_TOTAL_BYTES) {
        setEditingAttachmentError(
          t("chat_attachment_limit_total_size", {
            max_mb: String(Math.round(ATTACHMENTS_MAX_TOTAL_BYTES / 1_000_000)),
          })
        )
        return
      }
      setEditingAttachmentError(null)
      setEditingAttachments((prev) => [...prev, ...next])
      return
    }

    if (!text || !isLikelyCode(text)) return

    const current = textarea.value
    const start = textarea.selectionStart ?? current.length
    if (isInsideMarkdownCodeFence(current, start)) {
      return
    }

    event.preventDefault()
    const end = textarea.selectionEnd ?? current.length
    const wrapped = wrapInCodeFence(text)
    const nextValue = insertAtCursor(current, wrapped, start, end)
    textarea.value = nextValue
    textarea.dispatchEvent(new Event("input", { bubbles: true }))
    requestAnimationFrame(() => {
      if (!document.contains(textarea)) return
      textarea.selectionStart = start + wrapped.length
      textarea.selectionEnd = start + wrapped.length
    })
  }, [editingAttachments, t])

  const handleEditDragEnter = useCallback((event: React.DragEvent<HTMLDivElement>) => {
    if (!event.dataTransfer.types.includes("Files")) return
    event.preventDefault()
    setIsEditDragActive(true)
  }, [])

  const handleEditDragOver = useCallback((event: React.DragEvent<HTMLDivElement>) => {
    if (!event.dataTransfer.types.includes("Files")) return
    event.preventDefault()
    event.dataTransfer.dropEffect = "copy"
    setIsEditDragActive(true)
  }, [])

  const handleEditDragLeave = useCallback((event: React.DragEvent<HTMLDivElement>) => {
    if (event.currentTarget.contains(event.relatedTarget as Node)) return
    setIsEditDragActive(false)
  }, [])

  const handleEditDrop = useCallback((event: React.DragEvent<HTMLDivElement>) => {
    if (!event.dataTransfer.types.includes("Files")) return
    event.preventDefault()
    setIsEditDragActive(false)
    const files = Array.from(event.dataTransfer.files ?? [])
    if (files.length === 0) return
    void addEditingFiles(files)
  }, [addEditingFiles])

  const removeEditingAttachment = useCallback((index: number) => {
    setEditingAttachmentError(null)
    setEditingAttachments((prev) => prev.filter((_, idx) => idx !== index))
  }, [])

  const startEditMessage = useCallback((msg: ChatMessage) => {
    setEditingMessageId(msg.id)
    setEditingAttachmentError(null)
    setIsEditDragActive(false)
    setEditingAttachments(
      (msg.attachments ?? []).map((attachment) => ({
        upload_id:
          ("id" in attachment ? attachment.id : undefined) ||
          extractAttachmentIdFromContentUrl(attachment.content_url),
        file_name: attachment.file_name,
        content_type: attachment.content_type,
        data_base64: attachment.data_base64,
        content_url: attachment.content_url,
      }))
    )
  }, [])

  const cancelEditMessage = useCallback(() => {
    setEditingMessageId(null)
    setEditingAttachments([])
    setEditingAttachmentError(null)
    setIsEditDragActive(false)
  }, [])

  const saveEditedMessage = useCallback(async (msg: ChatMessage, content: string) => {
    if (!activeChat) return
    if (msg.id.startsWith("temp-")) return
    const trimmed = content.trim()
    if (!trimmed && editingAttachments.length === 0) return
    let uploadedEditingAttachments: ChatMessageAttachmentInput[] = []
    try {
      setIsUploadingAttachments(true)
      uploadedEditingAttachments = await uploadAttachmentsForChat(
        activeChat.id,
        editingAttachments
      )
    } catch (error) {
      const message =
        error instanceof Error ? error.message : t("chat_attachment_upload_failed")
      setAttachmentError(message)
      return
    } finally {
      setIsUploadingAttachments(false)
    }
    stopGeneration()
    const generationEpoch = ++generationEpochRef.current
    setAutoScrollEnabled(true)
    lastScrolledKeyRef.current = null
    await queryClient.cancelQueries({ queryKey: ["chatMessages", activeChat.id] })
    setChatGenerating(activeChat.id, true)
    setToolEvents([])
    const activityAt = new Date().toISOString()
    bumpChatActivity(activeChat.id, activityAt)
    const tempAssistantId = `temp-assistant-edit-${Date.now()}`
    const updateMessages = (updater: (prev: ChatMessage[]) => ChatMessage[]) =>
      updateChatMessagesFor(activeChat.id, updater)
    updateMessages((prev) => {
      const index = prev.findIndex((item) => item.id === msg.id)
      if (index === -1) return prev
      const updated = {
        ...prev[index],
        content: trimmed,
        attachments: uploadedEditingAttachments,
      }
      const placeholder: ChatMessage = {
        id: tempAssistantId,
        role: "assistant",
        content: "",
        created_at: activityAt,
        model_id: selectedModel ?? null,
        model_name: selectedModel ? modelNameById[selectedModel] ?? null : null,
        thinking_steps: [],
        generation_status: "queued",
      }
      return [...prev.slice(0, index), updated, placeholder]
    })
    cancelEditMessage()
    const { promise, cancel } = chatApi.editMessageStream(
      activeChat.id,
      msg.id,
      trimmed,
      selectedModel,
      uploadedEditingAttachments.map((attachment) => ({
        upload_id: attachment.upload_id,
        file_name: attachment.file_name,
        content_type: attachment.content_type,
      })),
      webSearchEnabled,
      codeExecutionEnabled,
      selectedReasoningEffort,
      locale,
      (event) => {
        applyStreamEvent(activeChat.id, tempAssistantId, event)
      }
    )
    currentCancelRef.current = cancel
    try {
      await promise
      if (generationEpochRef.current === generationEpoch) {
        refetchChats()
      }
    } catch {
      if (generationEpochRef.current !== generationEpoch) return
      updateMessages((prev) =>
        prev.map((item) =>
          item.id === tempAssistantId
            ? {
                ...item,
                content: item.content?.trim() ? item.content : t("chat_generation_failed"),
                thinking_steps: [],
                generation_status: "failed",
              }
            : item
        )
      )
      chatApi
        .messages(activeChat.id)
        .then((data) => replaceChatMessagesFor(activeChat.id, data))
        .catch(() => null)
    } finally {
      if (currentCancelRef.current === cancel) {
        currentCancelRef.current = null
      }
      if (generationEpochRef.current === generationEpoch) {
        setChatGenerating(activeChat.id, false)
      }
    }
  }, [
    activeChat,
    editingAttachments,
    uploadAttachmentsForChat,
    stopGeneration,
    queryClient,
    updateChatMessagesFor,
    selectedModel,
    selectedReasoningEffort,
    modelNameById,
    cancelEditMessage,
    webSearchEnabled,
    codeExecutionEnabled,
    locale,
    applyStreamEvent,
    refetchChats,
    replaceChatMessagesFor,
    bumpChatActivity,
    setChatGenerating,
    t,
  ])

  const deleteFromMessage = useCallback((msg: ChatMessage) => {
    setDeleteConfirmMessage(msg)
  }, [])

  const confirmDeleteFromMessage = useCallback(async () => {
    if (!activeChat || !deleteConfirmMessage) return
    const msg = deleteConfirmMessage
    setDeleteConfirmMessage(null)
    stopGeneration()
    await chatApi.deleteBranchFromMessage(activeChat.id, msg.id)
    updateChatMessagesFor(activeChat.id, (prev) => {
      const index = prev.findIndex((item) => item.id === msg.id)
      if (index === -1) return prev
      return prev.slice(0, index)
    })
  }, [activeChat, deleteConfirmMessage, stopGeneration, updateChatMessagesFor])

  const retryFailedMessage = useCallback(async (failedMessage: ChatMessage) => {
    if (!chatId || !activeChat) return
    if (loadingByChat[chatId]) return
    const messages = queryClient.getQueryData<ChatMessage[]>(["chatMessages", chatId]) ?? []
    const failedIndex = messages.findIndex((msg) => msg.id === failedMessage.id)
    if (failedIndex < 0) return
    const sourceUser = [...messages.slice(0, failedIndex)]
      .reverse()
      .find((msg) => msg.role === "user")
    if (!sourceUser || sourceUser.id.startsWith("temp-")) return

    stopGeneration()
    const generationEpoch = ++generationEpochRef.current
    setAutoScrollEnabled(true)
    lastScrolledKeyRef.current = null
    await queryClient.cancelQueries({ queryKey: ["chatMessages", chatId] })
    setChatGenerating(chatId, true)
    setToolEvents([])
    const activityAt = new Date().toISOString()
    bumpChatActivity(chatId, activityAt)
    const tempAssistantId = `temp-assistant-retry-${Date.now()}`
    const placeholder: ChatMessage = {
      id: tempAssistantId,
      role: "assistant",
      content: "",
      created_at: activityAt,
      model_id: selectedModel ?? null,
      model_name: selectedModel ? modelNameById[selectedModel] ?? null : null,
      thinking_steps: [],
      generation_status: "queued",
    }
    updateChatMessagesFor(chatId, (prev) => {
      const userIndex = prev.findIndex((msg) => msg.id === sourceUser.id)
      if (userIndex < 0) return prev
      return [...prev.slice(0, userIndex + 1), placeholder]
    })
    const retryAttachments = (sourceUser.attachments ?? []).map((attachment) => ({
      upload_id:
        ("id" in attachment ? attachment.id : undefined) ||
        extractAttachmentIdFromContentUrl(attachment.content_url),
      file_name: attachment.file_name,
      content_type: attachment.content_type,
      data_base64: attachment.data_base64,
      content_url: attachment.content_url,
    }))
    let uploadedRetryAttachments: ChatMessageAttachmentInput[]
    try {
      setIsUploadingAttachments(true)
      uploadedRetryAttachments = await uploadAttachmentsForChat(chatId, retryAttachments)
    } catch (error) {
      const message =
        error instanceof Error ? error.message : t("chat_attachment_upload_failed")
      setAttachmentError(message)
      setChatGenerating(chatId, false, { unreadIfAway: false })
      return
    } finally {
      setIsUploadingAttachments(false)
    }
    const { promise, cancel } = chatApi.editMessageStream(
      chatId,
      sourceUser.id,
      sourceUser.content,
      selectedModel,
      uploadedRetryAttachments.map((attachment) => ({
        upload_id: attachment.upload_id,
        file_name: attachment.file_name,
        content_type: attachment.content_type,
      })),
      webSearchEnabled,
      codeExecutionEnabled,
      selectedReasoningEffort,
      locale,
      (event) => {
        applyStreamEvent(chatId, tempAssistantId, event)
      }
    )
    currentCancelRef.current = cancel
    try {
      await promise
      if (generationEpochRef.current === generationEpoch) {
        refetchChats()
      }
    } catch {
      if (generationEpochRef.current !== generationEpoch) return
      updateChatMessagesFor(chatId, (prev) =>
        prev.map((msg) =>
          msg.id === tempAssistantId
            ? {
                ...msg,
                content: msg.content?.trim() ? msg.content : t("chat_generation_failed"),
                generation_status: "failed",
              }
            : msg
        )
      )
    } finally {
      if (currentCancelRef.current === cancel) {
        currentCancelRef.current = null
      }
      if (generationEpochRef.current === generationEpoch) {
        setChatGenerating(chatId, false)
      }
    }
  }, [
    chatId,
    activeChat,
    loadingByChat,
    queryClient,
    stopGeneration,
    selectedModel,
    selectedReasoningEffort,
    modelNameById,
    webSearchEnabled,
    codeExecutionEnabled,
    locale,
    applyStreamEvent,
    refetchChats,
    t,
    uploadAttachmentsForChat,
    updateChatMessagesFor,
    bumpChatActivity,
    setChatGenerating,
  ])

  const renderMessage = useCallback(
    (msg: ChatMessage) => {
      const isUser = msg.role === "user"
      const isCodeEvent = msg.tool_event?.type === "code_execution"
      const isImageMessage =
        (msg.attachments && msg.attachments.length > 0) ||
        (msg.model_name ? msg.model_name.toLowerCase().includes("image") : false)
      const activeThinking = msg.thinking_steps ?? []
      const nonStepThinking = activeThinking.filter(
        (label) =>
          !/^Step \d+\/\d+$/.test(label) &&
          label !== "Thinking" &&
          label !== "Answering"
      )
      const stepLabel =
        activeThinking.find((label) => /^Step \d+\/\d+$/.test(label)) ?? null
      const currentToolLabel =
        [...nonStepThinking]
          .reverse()
          .find((label) => Boolean(label)) ?? null
      const isThinking =
        msg.role === "assistant" &&
        !isTerminalStatus(msg.generation_status ?? null) &&
        (msg.content.trim().length === 0 || activeThinking.length > 0) &&
        (!isImageMessage || activeThinking.length > 0)
      const thinkingLabels =
        nonStepThinking.length > 0
          ? nonStepThinking
          : msg.role === "assistant" &&
              isImageMessage &&
              !isTerminalStatus(msg.generation_status ?? null)
            ? [t("chat_generating_image")]
            : []
      const isEditing = editingMessageId === msg.id
      const messageIndex = visibleMessages.findIndex((item) => item.id === msg.id)
      const exportQuestion =
        !isUser && messageIndex > 0
          ? [...visibleMessages.slice(0, messageIndex)]
              .reverse()
              .find((item) => item.role === "user")?.content ?? null
          : null
      return (
        <MessageBubble
          key={msg.id}
          msg={msg}
          isUser={isUser}
          isCodeEvent={isCodeEvent}
          isThinking={isThinking}
          thinkingLabels={thinkingLabels}
          currentStepLabel={stepLabel}
          currentToolLabel={currentToolLabel}
          actionInfoLevel={actionInfoLevel}
          actionsEnabled={!isSharedView}
          isEditing={isEditing}
          isEditDragActive={isEditing && isEditDragActive}
          editingAttachments={isEditing ? editingAttachments : []}
          editAttachmentError={isEditing ? editingAttachmentError : null}
          codeTheme={codeTheme}
          t={t}
          onOpenSources={setSourcesPanelSources}
          exportQuestion={exportQuestion}
          onStartEdit={startEditMessage}
          onDeleteFromMessage={deleteFromMessage}
          onRetryMessage={retryFailedMessage}
          onShareMessage={shareMessage}
          onSaveAsPrompt={saveMessageAsPrompt}
          onSaveEditedMessage={saveEditedMessage}
          onCancelEdit={cancelEditMessage}
          onEditPasteAttachments={handleEditPasteAttachments}
          onEditFilesSelected={handleEditFilesSelected}
          onEditDragEnter={handleEditDragEnter}
          onEditDragOver={handleEditDragOver}
          onEditDragLeave={handleEditDragLeave}
          onEditDrop={handleEditDrop}
          onRemoveEditingAttachment={removeEditingAttachment}
          onPreviewAttachment={setPreviewAttachment}
        />
      )
    },
    [
      codeTheme,
      t,
      isTerminalStatus,
      editingMessageId,
      editingAttachments,
      editingAttachmentError,
      isEditDragActive,
      startEditMessage,
      deleteFromMessage,
      retryFailedMessage,
      shareMessage,
      saveMessageAsPrompt,
      saveEditedMessage,
      cancelEditMessage,
      handleEditPasteAttachments,
      handleEditFilesSelected,
      handleEditDragEnter,
      handleEditDragOver,
      handleEditDragLeave,
      handleEditDrop,
      removeEditingAttachment,
      isSharedView,
      actionInfoLevel,
      visibleMessages,
    ]
  )

  const handleSelectChat = useCallback(
    (chat: Chat, onSelect?: () => void) => {
      navigate(
        chat.agent_id
          ? `/chat/${chat.id}?agent=${encodeURIComponent(chat.agent_id)}`
          : `/chat/${chat.id}`
      )
      onSelect?.()
      window.setTimeout(() => {
        composerInputRef.current?.focus()
      }, 0)
    },
    [navigate]
  )

  const activeAgent = useMemo(
    () => agents.find((item) => item.id === activeAgentId) ?? null,
    [agents, activeAgentId]
  )

  const activeChatTitle = useMemo(() => {
    if (isHistoryView) return t("chat_history")
    if (activeAgent) return activeAgent.name
    const active = chats.find((chat) => chat.id === chatId)
    return active?.title || t("chat_title")
  }, [activeAgent, chats, chatId, isHistoryView, t])

  useEffect(() => {
    document.title = `${activeChatTitle} - ${t("app_title")}`
  }, [activeChatTitle, t])

  const isEmptyChat = !isMessagesLoading && visibleMessages.length === 0
  const activeOrgName = orgs.find((org) => org.id === orgId)?.name
  const profileLabel = currentUser
    ? currentUser.display_name || currentUser.username || currentUser.email || t("me_settings")
    : null
  const profileFirstName = profileLabel?.trim().split(/\s+/)[0] ?? ""
  const welcomeTitle =
    currentUser?.display_name || currentUser?.username
      ? t("chat_welcome_named").replace("{name}", profileFirstName)
      : t("chat_welcome_title")
  const sidebarFooter = (
    <Button
      variant="ghost"
      className="justify-start gap-1.5 p-1.5 w-full h-14 text-left"
      onClick={() => navigate("/settings/me")}
    >
      {currentUser?.avatar_url ? (
        <img
          src={currentUser.avatar_url}
          alt=""
          className="rounded-lg size-11 object-cover shrink-0"
        />
      ) : (
        <span className="flex justify-center items-center bg-secondary rounded-lg size-11 font-semibold text-sm shrink-0">
          {profileLabel ? profileLabel.slice(0, 1).toUpperCase() : null}
        </span>
      )}
      <span className="min-w-0">
        {profileLabel ? (
          <span className="block font-semibold text-sm truncate leading-4">{profileLabel}</span>
        ) : (
          <span className="block bg-secondary rounded w-24 h-4 animate-pulse" />
        )}
        {activeOrgName ? (
          <span className="block font-medium text-muted-foreground text-xs truncate leading-4">
            {activeOrgName}
          </span>
        ) : null}
      </span>
    </Button>
  )
  const mobileSidebar = (
    <Sheet open={sidebarOpen} onOpenChange={setSidebarOpen}>
      <SheetTrigger asChild>
        <Button variant="ghost" size="icon" className="md:hidden" aria-label={t("sidebar_toggle")}>
          <Menu aria-hidden="true" />
        </Button>
      </SheetTrigger>
      <SheetContent side="left" className="bg-sidebar p-2 w-57.25" showCloseButton={false}>
        <ChatSidebar
          title={t("chat_title")}
          labels={{
            newChat: t("chat_new"),
            history: t("chat_history"),
            projects: t("project_title"),
            prompts: t("prompt_library"),
            close: t("common_close"),
          }}
          activeSection={isHistoryView ? "history" : activeAgentId ? "projects" : null}
          activeChatId={chatId}
          activeAgentId={activeAgentId}
          onNewChat={startNewChat}
          onOpenHistory={() => {
            setSidebarOpen(false)
            navigate("/history")
          }}
          onOpenProjects={() => {
            setSidebarOpen(false)
            navigate("/projects")
          }}
          onToggleShareChat={toggleShareChat}
          onInsertPrompt={insertPromptIntoComposer}
          onPromptCountChange={setPromptCount}
          onRequestClose={() => setSidebarOpen(false)}
          footer={sidebarFooter}
        />
      </SheetContent>
    </Sheet>
  )
  const desktopSidebarToggle = (
    <Button
      variant="ghost"
      size="icon"
      className="hidden md:inline-flex"
      onClick={() => setDesktopSidebarOpen((open) => !open)}
      aria-label={t("sidebar_toggle")}
    >
      {desktopSidebarOpen ? (
        <span
          aria-hidden="true"
          className="size-4 figma-icon"
          style={{ maskImage: "url('/icon-panel.svg')" }}
        />
      ) : (
        <PanelLeftOpen aria-hidden="true" />
      )}
    </Button>
  )

  return (
    <div className="flex bg-background h-svh overflow-hidden">
      <aside
        className={`hidden min-h-0 w-57.25 shrink-0 flex-col bg-sidebar p-2 text-sidebar-foreground ${
          desktopSidebarOpen ? "md:flex" : ""
        }`}
      >
        <ChatSidebar
          title={t("chat_title")}
          labels={{
            newChat: t("chat_new"),
            history: t("chat_history"),
            projects: t("project_title"),
            prompts: t("prompt_library"),
          }}
          activeSection={isHistoryView ? "history" : activeAgentId ? "projects" : null}
          activeChatId={chatId}
          activeAgentId={activeAgentId}
          onNewChat={startNewChat}
          onOpenHistory={() => navigate("/history")}
          onOpenProjects={() => navigate("/projects")}
          onToggleShareChat={toggleShareChat}
          onInsertPrompt={insertPromptIntoComposer}
          onPromptCountChange={setPromptCount}
          footer={sidebarFooter}
        />
      </aside>
      <main
        id="main-content"
        className={`relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-background md:m-2 md:rounded-card md:border md:border-border ${
          desktopSidebarOpen ? "md:ml-0" : "md:ml-2"
        }`}
      >
        {isHistoryView ? (
          <HistoryPanel
            groups={historyGroups}
            leadingAction={
              <>
                {mobileSidebar}
                {desktopSidebarToggle}
              </>
            }
            trailingAction={
              <Button onClick={startNewChat}>
                <Plus aria-hidden="true" />
                {t("chat_new")}
              </Button>
            }
            labels={{
              title: t("chat_history"),
              search: t("chat_search_placeholder"),
              empty: t("chat_history_empty"),
              emptySearch: t("chat_search_no_results"),
              untitled: t("chat_untitled"),
              delete: t("chat_delete"),
              share: t("chat_share"),
              unshare: t("chat_unshare"),
              actions: t("chat_history_actions"),
            }}
            onQueryChange={setChatSearchDebounced}
            onSelectChat={(chat) => handleSelectChat(chat)}
            onDeleteChat={setDeleteConfirmChat}
            onToggleShareChat={toggleShareChat}
          />
        ) : (
          <div className="flex flex-1 min-w-0 min-h-0 flex-col">
            {cowork.open && cowork.document && isMobile ? (
              <div className="flex shrink-0 items-center gap-1 border-b border-border px-3 py-2">
                <div className="flex items-center gap-1 rounded-lg bg-muted p-0.5">
                  <Button
                    type="button"
                    size="sm"
                    variant={cowork.mobileTab === "chat" ? "secondary" : "ghost"}
                    className="h-7 px-2.5 text-xs"
                    onClick={() => cowork.setMobileTab("chat")}
                  >
                    Chat
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant={cowork.mobileTab === "document" ? "secondary" : "ghost"}
                    className="h-7 px-2.5 text-xs"
                    onClick={() => cowork.setMobileTab("document")}
                  >
                    Document
                    {cowork.document.version > cowork.document.last_assistant_version ? (
                      <span className="ml-1 inline-block size-1.5 rounded-full bg-amber-500" />
                    ) : null}
                  </Button>
                </div>
              </div>
            ) : null}
            <div className="flex min-h-0 min-w-0 flex-1">
            <div
              className={`relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden ${
                isMobile && cowork.open && cowork.mobileTab === "document" ? "hidden" : ""
              }`}
            >
              <h1 className="sr-only">{activeChatTitle}</h1>
              <div className="-top-px -left-px relative flex justify-between items-center bg-background px-3 py-2.5 w-[calc(100%+2px)] h-15 shrink-0">
                <div className="flex items-center gap-2 min-w-0">
                  {mobileSidebar}
                  {desktopSidebarToggle}
                  {isAgentMode ? (
                    <div className="flex items-center gap-3 text-muted-foreground text-sm">
                      <span>
                        {t("project_label")}{" "}
                        {activeAgentId ? (
                          <button
                            type="button"
                            onClick={() =>
                              navigate(`/projects/${encodeURIComponent(activeAgentId)}`)
                            }
                            className="font-medium text-foreground hover:underline"
                          >
                            {activeAgent?.name ?? t("project_unknown")}
                          </button>
                        ) : (
                          <span className="font-medium text-foreground">
                            {t("project_unknown")}
                          </span>
                        )}
                      </span>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => navigate("/chat")}
                      >
                        {t("project_leave")}
                      </Button>
                    </div>
                  ) : null}
                </div>
                <div className="flex items-center gap-2">
                  {cowork.document && !cowork.open ? (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="hidden h-8 md:inline-flex"
                      onClick={() => {
                        cowork.setOpen(true)
                        cowork.setMobileTab("document")
                      }}
                    >
                      Document
                      {cowork.document.version > cowork.document.last_assistant_version ? (
                        <span className="ml-1 size-1.5 rounded-full bg-amber-500" />
                      ) : null}
                    </Button>
                  ) : null}
                  <label className="hidden md:inline-flex items-center gap-2 cursor-pointer">
                    <span className="text-sm leading-5">{t("chat_save_session")}</span>
                    <Switch
                      checked={!incognitoEnabled}
                      onCheckedChange={(checked) => setIncognitoEnabled(!checked)}
                      aria-label={t("chat_save_session")}
                    />
                  </label>
                </div>
              </div>
              <MessageList
                key={chatId ?? "new"}
                messages={visibleMessages}
                welcomeTitle={welcomeTitle}
                isLoading={isMessagesLoading}
                onScroll={handleMessagesScroll}
                containerRef={messagesContainerRef}
                endRef={messagesEndRef}
                renderMessage={renderMessage}
              />
              <ChatComposer
                ref={composerRef}
                placeholder={
                  isAgentMode
                    ? t("project_message_placeholder")
                    : t("chat_message_placeholder")
                }
                loading={currentChatLoading || isUploadingAttachments}
                readOnly={isSharedView}
                isDragActive={isDragActive}
                pendingAttachments={pendingAttachments}
                attachmentError={attachmentError}
                webSearchEnabled={webSearchEnabled}
                codeExecutionEnabled={codeExecutionEnabled}
                inputRef={composerInputRef}
                showModelSelect
                hasPrompts={promptCount > 0}
                modelSelect={
                  <Select value={selectedModel} onValueChange={setSelectedModel}>
                    <SelectTrigger
                      className="h-9 w-auto min-w-0 max-w-full overflow-hidden bg-transparent shadow-none border-0 [&_[data-slot=select-value]]:min-w-0"
                      aria-label={t("chat_select_model")}
                    >
                      <SelectValue placeholder={t("chat_best_available_model")} />
                    </SelectTrigger>
                    <SelectContent className="z-100 max-h-96">
                      {selectableChatModels.map((model) => {
                        const hasProviderIcon =
                          getProviderIconCandidates(
                            model.provider,
                            model.model_name
                          ).length > 0
                        return (
                          <SelectItem
                            key={model.id}
                            value={model.id}
                            disabled={model.is_available === false}
                          >
                            <span className="inline-flex min-w-0 items-center gap-2">
                              {hasProviderIcon ? (
                                <ProviderIcon
                                  provider={model.provider}
                                  modelName={model.model_name}
                                  className="size-5 shrink-0"
                                />
                              ) : isImageOutputModel(model) ? (
                                <ImageIcon className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
                              ) : null}
                              <span className="truncate">{model.display_name}</span>
                            </span>
                          </SelectItem>
                        )
                      })}
                      <div className="mt-1 border-t px-2 pt-2 pb-1">
                        <div className="mb-1 flex items-center justify-between text-[11px] text-muted-foreground">
                          <span>{t("chat_thinking_level")}</span>
                          <span>{selectedReasoningEffort ?? t("chat_thinking_level_auto")}</span>
                        </div>
                        <div className="reasoning-slider">
                          <div className="reasoning-slider__track">
                            {providerReasoningLevels.map((level, index) => {
                              const stopCount = Math.max(providerReasoningLevels.length - 1, 1)
                              const leftPercent = (index / stopCount) * 100
                              return (
                                <div
                                  key={`${level}-${index}`}
                                  className={
                                    index === activeReasoningStopIndex
                                      ? "reasoning-slider__dot reasoning-slider__dot--active"
                                      : "reasoning-slider__dot"
                                  }
                                  style={{ left: `${leftPercent}%` }}
                                />
                              )
                            })}
                          </div>
                          <input
                            type="range"
                            min={0}
                            max={Math.max(providerReasoningLevels.length - 1, 0)}
                            step={1}
                            value={activeReasoningStopIndex}
                            onChange={(event) => {
                              const index = Number(event.target.value)
                              const maxIndex = Math.max(providerReasoningLevels.length - 1, 0)
                              setReasoningStopIndex(Math.max(0, Math.min(index, maxIndex)))
                            }}
                            className="reasoning-slider__input"
                            aria-label={t("chat_thinking_level")}
                          />
                        </div>
                      </div>
                    </SelectContent>
                  </Select>
                }
                onSend={sendMessage}
                onStop={stopGeneration}
                onFilesSelected={handleFilesSelected}
                onRemoveAttachment={removePendingAttachment}
                onPreviewAttachment={setPreviewAttachment}
                onPasteAttachments={handlePasteAttachments}
                onDragEnter={handleComposerDragEnter}
                onDragOver={handleComposerDragOver}
                onDragLeave={handleComposerDragLeave}
                onDrop={handleComposerDrop}
                onWebSearchEnabledChange={setWebSearchEnabled}
                onCodeExecutionEnabledChange={setCodeExecutionEnabled}
                onInsertPromptRequest={() => {
                  void openInsertPromptPicker()
                }}
                sendLabel={t("common_send")}
                stopLabel={t("common_stop")}
                welcomeTitle={welcomeTitle}
                centered={isEmptyChat}
              />
            </div>
            {cowork.open && cowork.document && (!isMobile || cowork.mobileTab === "document") ? (
              <CoworkPanel
                document={cowork.document}
                documents={cowork.documents}
                open={cowork.open}
                saving={cowork.saving}
                writing={cowork.writing}
                conflict={cowork.conflict}
                content={cowork.content}
                resizable={!isMobile}
                className={isMobile ? "max-w-none border-l-0" : undefined}
                onClose={cowork.closePanel}
                onContentChange={cowork.handleContentChange}
                onDownload={(options) => {
                  void cowork.downloadDocument(options)
                }}
                onActivateDocument={(documentId) => {
                  void cowork.activateDocument(documentId)
                }}
                onDeleteDocument={(documentId) => {
                  void cowork.deleteDocument(documentId)
                }}
                onReloadLatest={() => {
                  void cowork.reloadLatest()
                }}
              />
            ) : null}
            {sourcesPanelSources && !(cowork.open && cowork.document) ? (
              <SourcesPanel
                sources={sourcesPanelSources}
                title={t("chat_sources")}
                emptyLabel={t("chat_sources_empty")}
                closeLabel={t("common_close")}
                onClose={() => setSourcesPanelSources(null)}
                onNavigateInternal={(path) => {
                  setSourcesPanelSources(null)
                  navigate(path)
                }}
              />
            ) : null}
          </div>
          </div>
        )}
        <Dialog
          open={Boolean(previewAttachment)}
          onOpenChange={(open) => {
            if (!open) setPreviewAttachment(null)
          }}
        >
          <DialogContent className="flex justify-center items-center p-2 w-auto max-w-[90vw] sm:max-w-[90vw] h-auto max-h-[90vh]">
            {previewAttachment && previewAttachment.content_type.startsWith("image/") ? (
              <img
                src={
                  previewAttachment.data_base64
                    ? `data:${previewAttachment.content_type};base64,${previewAttachment.data_base64}`
                    : (previewAttachment.content_url ?? "")
                }
                alt={previewAttachment.file_name}
                className="w-auto max-w-[90vw] h-auto max-h-[90vh] object-contain"
              />
            ) : null}
          </DialogContent>
        </Dialog>
        <Dialog open={blockedLinkDialogOpen} onOpenChange={setBlockedLinkDialogOpen}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>{t("chat_link_not_shared_title")}</DialogTitle>
              <DialogDescription>{t("chat_link_not_shared_desc")}</DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button autoFocus onClick={() => setBlockedLinkDialogOpen(false)}>
                {t("common_close")}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
        <Dialog
          open={Boolean(deleteConfirmChat)}
          onOpenChange={(open) => {
            if (!open) setDeleteConfirmChat(null)
          }}
        >
          <DialogContent>
            <DialogHeader>
              <DialogTitle>{t("chat_delete_confirm_title")}</DialogTitle>
              <DialogDescription>
                {t("chat_delete_confirm_desc", {
                  title: deleteConfirmChat?.title || t("chat_untitled"),
                })}
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button autoFocus variant="outline" onClick={() => setDeleteConfirmChat(null)}>
                {t("chat_cancel")}
              </Button>
              <Button
                onClick={() => {
                  if (!deleteConfirmChat) return
                  deleteChat(deleteConfirmChat.id).catch(() => null)
                  setDeleteConfirmChat(null)
                }}
              >
                {t("chat_delete")}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
        <Dialog
          open={Boolean(deleteConfirmMessage)}
          onOpenChange={(open) => {
            if (!open) setDeleteConfirmMessage(null)
          }}
        >
          <DialogContent>
            <DialogHeader>
              <DialogTitle>{t("chat_delete_message_confirm_title")}</DialogTitle>
              <DialogDescription>
                {t("chat_delete_message_confirm_desc")}
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button
                autoFocus
                variant="outline"
                onClick={() => setDeleteConfirmMessage(null)}
              >
                {t("chat_cancel")}
              </Button>
              <Button
                variant="destructive"
                onClick={() => {
                  confirmDeleteFromMessage().catch(() => null)
                }}
              >
                {t("chat_delete_message")}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
        <InsertPromptPicker
          open={insertPromptOpen}
          prompts={insertPrompts}
          title={t("prompt_insert")}
          description={t("prompt_insert_description")}
          searchPlaceholder={t("prompt_search_placeholder")}
          onOpenChange={setInsertPromptOpen}
          onSelect={insertPromptIntoComposer}
        />
        <PromptFormDialog
          open={savePromptOpen}
          onOpenChange={setSavePromptOpen}
          orgId={orgId}
          spaces={agents}
          initial={{
            body: savePromptBody,
            agent_id:
              activeAgentId &&
              agents.some(
                (agent) =>
                  agent.id === activeAgentId &&
                  (agent.role === "owner" || agent.role === "editor")
              )
                ? activeAgentId
                : null,
          }}
          title={t("prompt_save_message")}
          description={t("prompt_form_description")}
          onSaved={() => {
            setPromptCount((count) => count + 1)
            toast.success(t("prompt_saved"))
          }}
        />
      </main>
    </div>
  )
}
