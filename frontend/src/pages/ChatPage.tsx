import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import type { CSSProperties } from "react"
import { useLocation, useNavigate, useParams } from "react-router-dom"
import { useQueryClient } from "@tanstack/react-query"

import { ApiError, agentApi, authApi, chatApi } from "@/lib/api"
import {
  codeExecutionEnabledStore,
  modelStore,
  orgStore,
  reasoningEffortStore,
  toolCallLogsVisibleStore,
  webSearchEnabledStore,
} from "@/lib/storage"
import type {
  Chat,
  Agent,
  ChatModel,
  ChatMessage,
  ChatMessageAttachmentInput,
  GenerationStatus,
} from "@/lib/types"
import { useI18n } from "@/lib/i18n-context"
import { supportsImageInput, supportsImageOutput } from "@/lib/modelCapabilities"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism"
import { Image as ImageIcon, Menu, PanelLeftOpen } from "lucide-react"
import { Sheet, SheetContent, SheetTrigger } from "@/components/ui/sheet"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import ChatSidebar from "@/pages/chat/ChatSidebar"
import HistoryPanel from "@/pages/chat/HistoryPanel"
import { ChatComposer } from "@/pages/chat/ChatComposer"
import { MessageList } from "@/pages/chat/MessageList"
import { MessageBubble } from "@/pages/chat/MessageBubble"
import {
  useChatSearch,
  useChatMessages,
  useChats,
  useCreateChat,
  useDeleteChat,
  useModels,
  useOrgsMine,
} from "@/hooks/use-chat-query"
import {
  readClipboardImagesAsAttachments,
  readFilesAsAttachments,
} from "@/lib/file-utils"

const ATTACHMENTS_MAX_FILES = 10
const ATTACHMENTS_MAX_FILE_BYTES = 20_000_000
const ATTACHMENTS_MAX_TOTAL_BYTES = 50_000_000

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

export const ChatPage = () => {
  const navigate = useNavigate()
  const location = useLocation()
  const { chatId, shareToken: shareTokenParam } = useParams()
  const activeAgentIdFromQuery = useMemo(
    () => new URLSearchParams(location.search).get("agent"),
    [location.search]
  )
  const [orgId, setOrgId] = useState<string | null>(orgStore.get())
  const [agents, setAgents] = useState<Agent[]>([])
  const [toolEvents, setToolEvents] = useState<ChatMessage[]>([])
  const [message, setMessage] = useState("")
  const [selectedModel, setSelectedModel] = useState<string | undefined>(
    modelStore.get() ?? undefined
  )
  const [loadingByChat, setLoadingByChat] = useState<Record<string, boolean>>({})
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null)
  const [editingContent, setEditingContent] = useState("")
  const [editingAttachments, setEditingAttachments] = useState<
    ChatMessageAttachmentInput[]
  >([])
  const [editingAttachmentError, setEditingAttachmentError] = useState<string | null>(null)
  const [isEditDragActive, setIsEditDragActive] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement | null>(null)
  const messagesContainerRef = useRef<HTMLDivElement | null>(null)
  const lastScrolledIdRef = useRef<string | null>(null)
  const [autoScrollEnabled, setAutoScrollEnabled] = useState(true)
  const [pendingAttachments, setPendingAttachments] = useState<
    ChatMessageAttachmentInput[]
  >([])
  const [isUploadingAttachments, setIsUploadingAttachments] = useState(false)
  const [attachmentError, setAttachmentError] = useState<string | null>(null)
  const [isDragActive, setIsDragActive] = useState(false)
  const [reasoningEffort, setReasoningEffort] = useState<string | null>(() => {
    const stored = reasoningEffortStore.get()
    if (stored === "low" || stored === "medium" || stored === "high") {
      return stored
    }
    if (stored === "none") return null
    return "medium"
  })
  const [webSearchEnabled, setWebSearchEnabled] = useState<boolean>(() => {
    const stored = webSearchEnabledStore.get()
    return stored == null ? true : stored === "1"
  })
  const [codeExecutionEnabled, setCodeExecutionEnabled] = useState<boolean>(() => {
    const stored = codeExecutionEnabledStore.get()
    return stored == null ? true : stored === "1"
  })
  const [showToolCallLogs, setShowToolCallLogs] = useState<boolean>(() => {
    return toolCallLogsVisibleStore.get() === "1"
  })
  const [previewAttachment, setPreviewAttachment] =
    useState<ChatMessageAttachmentInput | null>(null)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [desktopSidebarOpen, setDesktopSidebarOpen] = useState(true)
  const [currentUser, setCurrentUser] = useState<{
    email: string
    username: string | null
    display_name: string | null
    avatar_url: string | null
  } | null>(null)
  const [chatSearchQuery, setChatSearchQuery] = useState("")
  const [chatSearchDebounced, setChatSearchDebounced] = useState("")
  const [blockedLinkDialogOpen, setBlockedLinkDialogOpen] = useState(false)
  const [deleteConfirmChat, setDeleteConfirmChat] = useState<Chat | null>(null)
  const [clearHistoryConfirmOpen, setClearHistoryConfirmOpen] = useState(false)
  const [clearingHistory, setClearingHistory] = useState(false)
  const [shareDialogUrl, setShareDialogUrl] = useState<string | null>(null)
  const [shareCopied, setShareCopied] = useState<boolean | null>(null)
  const composerInputRef = useRef<HTMLTextAreaElement | null>(null)
  const { locale, t, tCount } = useI18n()
  const shareToken = useMemo(() => {
    if (shareTokenParam) return shareTokenParam
    return new URLSearchParams(location.search).get("share")
  }, [location.search, shareTokenParam])
  const isHistoryView = location.pathname === "/history"
  const codeTheme = useMemo<Record<string, CSSProperties>>(() => oneDark, [])

  const { data: orgs = [], isLoading: orgsLoading } = useOrgsMine()
  const { data: models = [] } = useModels(orgId)
  const { data: chats = [], refetch: refetchChats } = useChats(orgId)
  const { data: searchedChats = [] } = useChatSearch(orgId, chatSearchDebounced)
  const {
    data: serverMessages = [],
    isLoading: isMessagesLoading,
    error: messagesError,
  } = useChatMessages(chatId ?? null, shareToken)
  const createChatMutation = useCreateChat(orgId)
  const deleteChatMutation = useDeleteChat(orgId)

  useEffect(() => {
    authApi
      .me()
      .then(setCurrentUser)
      .catch(() => setCurrentUser(null))
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
    const timerId = window.setTimeout(() => {
      setChatSearchDebounced(chatSearchQuery.trim())
    }, 250)
    return () => window.clearTimeout(timerId)
  }, [chatSearchQuery])

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
  const taskCursorRef = useRef<Record<string, number>>({})
  const taskSubscriptionsRef = useRef<Record<string, () => void>>({})
  const taskPollingRef = useRef<Record<string, number>>({})

  const stopGeneration = () => {
    if (!chatId) return
    const activeAssistant = [...visibleMessages]
      .reverse()
      .find(
        (msg) =>
          msg.role === "assistant" &&
          !!msg.task_id &&
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
      updateChatMessagesFor(chatId, (prev) =>
        prev.map((msg) =>
          msg.task_id === activeTaskId
            ? {
                ...msg,
                generation_status: "cancelled",
                thinking_steps: [],
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
    setLoadingByChat((prev) => ({ ...prev, [chatId]: false }))
  }


  const getSourceLabel = useCallback((source: {
    url: string | null | undefined
    title?: string | null
    host?: string | null
  }) => {
    const sourceUrl = typeof source.url === "string" ? source.url : ""
    const isInternal = sourceUrl.startsWith("/chat/")
    if (isInternal) {
      return source.title || t("chat_untitled")
    }
    const host = source.host || (() => {
      try {
        return sourceUrl ? new URL(sourceUrl).hostname : "source"
      } catch {
        return sourceUrl || "source"
      }
    })()
    if (source.title) {
      return `${source.title} — ${host}`
    }
    return host
  }, [t])

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
      const matchesAssistant = (msg: ChatMessage) =>
        msg.id === assistantId ||
        ("task_id" in event && msg.task_id === event.task_id) ||
        ("message_id" in event && msg.id === event.message_id) ||
        ("assistant_message_id" in event && msg.id === event.assistant_message_id)

      if ("done" in event && event.done === true) {
        const messageId = typeof event.message_id === "string" ? event.message_id : null
        const content = typeof event.content === "string" ? event.content : null
        const modelName = typeof event.model_name === "string" ? event.model_name : null
        const modelId = typeof event.model_id === "string" ? event.model_id : null
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
                  thinking_steps: [],
                  generation_status: "completed",
                }
              : msg
          )
        )
        return
      }
      if ("error" in event && typeof event.error === "string") {
        const errorText = event.error.trim() || t("chat_generation_failed")
        updateChatMessagesFor(targetChatId, (prev) =>
          prev.map((msg) =>
            matchesAssistant(msg)
              ? {
                  ...msg,
                  content: errorText,
                  thinking_steps: [],
                  generation_status: "failed",
                }
              : msg
          )
        )
        return
      }
      if ("delta" in event && typeof event.delta === "string") {
        updateChatMessagesFor(targetChatId, (prev) =>
          prev.map((msg) =>
            matchesAssistant(msg)
              ? {
                  ...msg,
                  content: msg.content + event.delta,
                  generation_status: "streaming",
                }
              : msg
          )
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
              const withoutSteps = isStep
                ? current.filter((label) => !/^Step \d+\/\d+$/.test(label))
                : current
              const next = Array.from(new Set([...withoutSteps, activity.label]))
              return { ...msg, thinking_steps: next }
            }
            if (isStep) {
              return msg
            }
            return {
              ...msg,
              thinking_steps: current.filter((label) => label !== activity.label),
            }
          })
        )
        return
      }
      if ("tool_event" in event) {
        const toolEvent = event.tool_event as ChatMessage["tool_event"]
        if (toolEvent) {
          appendToolEvent(
            toolEvent,
            typeof event.task_id === "string" ? event.task_id : null
          )
        }
        return
      }
    },
    [appendToolEvent, t, updateChatMessagesFor]
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
      return null
    },
    []
  )

  const fetchTaskEvents = useCallback(
    async (targetChatId: string, taskId: string, assistantId: string) => {
      const after = taskCursorRef.current[taskId] ?? 0
      const events = await chatApi.listGenerationEvents(targetChatId, taskId, after)
      if (events.length > 0) {
        taskCursorRef.current[taskId] = events[events.length - 1].sequence
        events.forEach((event) => {
          const normalized = normalizeTaskEvent(event)
          if (normalized) {
            applyStreamEvent(targetChatId, assistantId, normalized as Record<string, unknown>)
          }
        })
      }
    },
    [applyStreamEvent, normalizeTaskEvent]
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
            const fallback = (errorMessage || "").trim() || t("chat_generation_failed")
            return {
              ...msg,
              content: msg.content?.trim().length ? msg.content : fallback,
              thinking_steps: [],
              generation_status: "failed",
            }
          }
          if (status === "cancelled") {
            const fallback =
              (errorMessage || "").trim() || t("chat_generation_cancelled")
            return {
              ...msg,
              content: msg.content?.trim().length ? msg.content : fallback,
              thinking_steps: [],
              generation_status: "cancelled",
            }
          }
          return {
            ...msg,
            thinking_steps: [],
            generation_status: "completed",
          }
        })
      )
    },
    [t, updateChatMessagesFor]
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
      const after = taskCursorRef.current[taskId]
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
      queryClient.setQueryData(["chatMessages", targetChatId], messages)
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
        if (a.task_id && b.task_id && a.task_id === b.task_id) {
          if (a.role === "tool" && b.role === "assistant") return -1
          if (a.role === "assistant" && b.role === "tool") return 1
        }
        return parseChatDate(a.created_at).getTime() - parseChatDate(b.created_at).getTime()
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

  const formatHistoryDate = useCallback(
    (dateString: string) => {
      const date = parseChatDate(dateString)
      const now = new Date()
      if (
        date.getFullYear() === now.getFullYear() &&
        date.getMonth() === now.getMonth() &&
        date.getDate() === now.getDate()
      ) {
        return t("chat_group_today")
      }
      return new Intl.DateTimeFormat(locale, {
        month: "short",
        day: "numeric",
        year: "numeric",
      }).format(date)
    },
    [locale, parseChatDate, t]
  )

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
        navigate(`/chat/${resolved.chat_id}?share=${encodeURIComponent(shareTokenParam)}`, {
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
    if (!(messagesError instanceof ApiError)) return
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

  const activeChat = useMemo(
    () => chats.find((item) => item.id === chatId) ?? null,
    [chatId, chats]
  )
  const activeAgentId = activeAgentIdFromQuery ?? activeChat?.agent_id ?? null
  const isAgentMode = Boolean(activeAgentId)
  const isSharedView = Boolean(chatId && shareToken && !activeChat)
  const currentChatLoading = Boolean(chatId && loadingByChat[chatId])

  const isChatSwitchRef = useRef(false)

  useEffect(() => {
    setToolEvents([])
    lastScrolledIdRef.current = null
    isChatSwitchRef.current = true
    Object.values(taskSubscriptionsRef.current).forEach((cancel) => cancel())
    taskSubscriptionsRef.current = {}
    Object.values(taskPollingRef.current).forEach((timeoutId) =>
      window.clearTimeout(timeoutId)
    )
    taskPollingRef.current = {}
    taskCursorRef.current = {}
  }, [chatId])

  useEffect(() => {
    if (selectedModel) {
      modelStore.set(selectedModel)
    }
  }, [selectedModel])

  useEffect(() => {
    if (!reasoningEffort) {
      reasoningEffortStore.set("none")
      return
    }
    reasoningEffortStore.set(reasoningEffort)
  }, [reasoningEffort])

  useEffect(() => {
    webSearchEnabledStore.set(webSearchEnabled)
  }, [webSearchEnabled])

  useEffect(() => {
    codeExecutionEnabledStore.set(codeExecutionEnabled)
  }, [codeExecutionEnabled])

  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key === "chatui_toolcall_logs_visible") {
        setShowToolCallLogs(event.newValue === "1")
      }
    }
    const onFocus = () => {
      setShowToolCallLogs(toolCallLogsVisibleStore.get() === "1")
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
        tasks.forEach((task) => {
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
          fetchTaskEvents(chatId, task.id, task.assistant_message_id).catch(() => null)
          subscribeToTask(chatId, task.id, task.assistant_message_id)
        })
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
    queryClient,
  ])

  const visibleMessages = useMemo(() => {
    const merged = mergeToolEvents(serverMessages, toolEvents).filter((msg) => {
      if (!msg.tool_event || msg.tool_event.type !== "tool_call") return true
      if (msg.tool_event.tool_name === "code_execution") return false
      return showToolCallLogs
    })
    return collapseActivityEvents(merged)
  }, [
    collapseActivityEvents,
    mergeToolEvents,
    serverMessages,
    toolEvents,
    showToolCallLogs,
  ])

  const scrollToBottom = (behavior: ScrollBehavior = "smooth") => {
    const container = messagesContainerRef.current
    if (container) {
      container.scrollTo({ top: container.scrollHeight, behavior })
      return
    }
    messagesEndRef.current?.scrollIntoView({ behavior, block: "end" })
  }

  useEffect(() => {
    if (!autoScrollEnabled && !currentChatLoading) return

    const lastMsg = visibleMessages[visibleMessages.length - 1]
    if (!lastMsg) return

    const isNew = lastMsg.id !== lastScrolledIdRef.current
    if (isNew) {
      lastScrolledIdRef.current = lastMsg.id
      const behavior: ScrollBehavior = isChatSwitchRef.current ? "instant" : "smooth"
      isChatSwitchRef.current = false
      if (lastMsg.role === "user") {
        scrollToBottom(behavior)
      } else if (lastMsg.role === "assistant") {
        const container = messagesContainerRef.current
        const target = container?.querySelector(
          `[data-message-id="${lastMsg.id}"]`
        )
        if (target && container) {
          const top = (target as HTMLElement).offsetTop - container.offsetTop
          container.scrollTo({ top, behavior })
        } else {
          scrollToBottom(behavior)
        }
      }
    }
  }, [visibleMessages, autoScrollEnabled, currentChatLoading])

  const handleMessagesScroll = useCallback(() => {
    const container = messagesContainerRef.current
    if (!container) return
    const threshold = 80
    const distanceFromBottom =
      container.scrollHeight - container.scrollTop - container.clientHeight
    setAutoScrollEnabled(distanceFromBottom <= threshold)
  }, [])

  const startNewChat = () => {
    replaceCurrentChatMessages([])
    setToolEvents([])
    setMessage("")
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
      title: t("chat_new_title"),
      agent_id: isAgentMode && activeAgentId ? activeAgentId : undefined,
    })
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

  const clearHistory = async () => {
    if (!orgId || chats.length === 0) return
    setClearingHistory(true)
    try {
      await Promise.all(chats.map((chat) => chatApi.deleteChat(chat.id)))
      queryClient.setQueryData<Chat[]>(["chats", orgId], [])
      setClearHistoryConfirmOpen(false)
    } finally {
      setClearingHistory(false)
      refetchChats().catch(() => null)
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
      if (chatId === chat.id && shareToken) {
        navigate(`/chat/${chat.id}`, { replace: true })
      }
      return
    }
    const shared = await chatApi.share(chat.id)
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
    if (shared.share_token) {
      const sharedPath = `/shared/${encodeURIComponent(shared.share_token)}`
      const fullUrl = `${window.location.origin}${sharedPath}`
      let copied: boolean
      try {
        await navigator.clipboard.writeText(fullUrl)
        copied = true
      } catch {
        copied = false
      }
      setShareDialogUrl(fullUrl)
      setShareCopied(copied)
    }
  }

  const sendMessage = async () => {
    if (chatId && loadingByChat[chatId]) return
    const trimmed = message.trim()
    if (!trimmed && pendingAttachments.length === 0) return
    setAttachmentError(null)
    setAutoScrollEnabled(true)
    let requestChatId: string | null = null
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
      setLoadingByChat((prev) => ({ ...prev, [chat.id]: true }))
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
      setMessage("")
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
        reasoningEffort,
        webSearchEnabled,
        codeExecutionEnabled,
        locale,
        (event) => {
          applyStreamEvent(chat.id, assistantId, event)
        }
      )
      currentCancelRef.current = cancel
      try {
        await promise
        refetchChats()
      } catch {
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
      setIsUploadingAttachments(false)
      currentCancelRef.current = null
      if (requestChatId) {
        setLoadingByChat((prev) => ({ ...prev, [requestChatId as string]: false }))
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

    const keywordLineCount = lines.filter((line) =>
      /^(def |class |import |from |function |const |let |var |if\b|for\b|while\b|return\b|#include\b|SELECT\b|INSERT\b|UPDATE\b|DELETE\b|WITH\b|CREATE\b|ALTER\b|DROP\b|--\s|\/\/)/i.test(
        line.trim()
      )
    ).length
    const indentedLineCount = lines.filter((line) => /^( {4,}|\t)/.test(line)).length
    const symbolHeavy = /[{}();=<>[\]$\\]/.test(text)
    const operatorHeavy = /(\+\+|--|=>|==|!=|<=|>=|:=|&&|\|\|)/.test(text)
    const proseLikeLineCount = lines.filter((line) =>
      /^[A-Za-z0-9 ,.'"!?()-]+$/.test(line.trim())
    ).length

    let score = 0
    if (keywordLineCount >= 1) score += 2
    if (indentedLineCount >= 2) score += 1
    if (symbolHeavy) score += 1
    if (operatorHeavy) score += 1

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
    const start = input?.selectionStart ?? message.length
    if (isInsideMarkdownCodeFence(message, start)) {
      return
    }

    event.preventDefault()
    const end = input?.selectionEnd ?? message.length
    const wrapped = wrapInCodeFence(text)
    const nextValue = insertAtCursor(message, wrapped, start, end)
    setMessage(nextValue)
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

    const start = textarea.selectionStart ?? editingContent.length
    if (isInsideMarkdownCodeFence(editingContent, start)) {
      return
    }

    event.preventDefault()
    const end = textarea.selectionEnd ?? editingContent.length
    const wrapped = wrapInCodeFence(text)
    const nextValue = insertAtCursor(editingContent, wrapped, start, end)
    setEditingContent(nextValue)
    requestAnimationFrame(() => {
      if (!document.contains(textarea)) return
      textarea.selectionStart = start + wrapped.length
      textarea.selectionEnd = start + wrapped.length
    })
  }, [editingAttachments, editingContent, t])

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
    setEditingContent(msg.content)
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
    setEditingContent("")
    setEditingAttachments([])
    setEditingAttachmentError(null)
    setIsEditDragActive(false)
  }, [])

  const saveEditedMessage = useCallback(async (msg: ChatMessage) => {
    if (!activeChat) return
    if (msg.id.startsWith("temp-")) return
    const trimmed = editingContent.trim()
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
    setAutoScrollEnabled(true)
    await queryClient.cancelQueries({ queryKey: ["chatMessages", activeChat.id] })
    setLoadingByChat((prev) => ({ ...prev, [activeChat.id]: true }))
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
      reasoningEffort,
      webSearchEnabled,
      codeExecutionEnabled,
      locale,
      (event) => {
        applyStreamEvent(activeChat.id, tempAssistantId, event)
      }
    )
    currentCancelRef.current = cancel
    try {
      await promise
      refetchChats()
    } catch {
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
      currentCancelRef.current = null
      setLoadingByChat((prev) => ({ ...prev, [activeChat.id]: false }))
    }
  }, [
    activeChat,
    editingContent,
    editingAttachments,
    uploadAttachmentsForChat,
    stopGeneration,
    queryClient,
    updateChatMessagesFor,
    selectedModel,
    modelNameById,
    cancelEditMessage,
    reasoningEffort,
    webSearchEnabled,
    codeExecutionEnabled,
    locale,
    applyStreamEvent,
    refetchChats,
    replaceChatMessagesFor,
    bumpChatActivity,
    t,
  ])

  const deleteFromMessage = useCallback(async (msg: ChatMessage) => {
    if (!activeChat) return
    stopGeneration()
    await chatApi.deleteBranchFromMessage(activeChat.id, msg.id)
    updateChatMessagesFor(activeChat.id, (prev) => {
      const index = prev.findIndex((item) => item.id === msg.id)
      if (index === -1) return prev
      return prev.slice(0, index)
    })
  }, [activeChat, stopGeneration, updateChatMessagesFor])

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
    setAutoScrollEnabled(true)
    await queryClient.cancelQueries({ queryKey: ["chatMessages", chatId] })
    setLoadingByChat((prev) => ({ ...prev, [chatId]: true }))
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
      setLoadingByChat((prev) => ({ ...prev, [chatId]: false }))
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
      reasoningEffort,
      webSearchEnabled,
      codeExecutionEnabled,
      locale,
      (event) => {
        applyStreamEvent(chatId, tempAssistantId, event)
      }
    )
    currentCancelRef.current = cancel
    try {
      await promise
      refetchChats()
    } catch {
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
      currentCancelRef.current = null
      setLoadingByChat((prev) => ({ ...prev, [chatId]: false }))
    }
  }, [
    chatId,
    activeChat,
    loadingByChat,
    queryClient,
    stopGeneration,
    selectedModel,
    modelNameById,
    reasoningEffort,
    webSearchEnabled,
    codeExecutionEnabled,
    locale,
    applyStreamEvent,
    refetchChats,
    t,
    uploadAttachmentsForChat,
    updateChatMessagesFor,
    bumpChatActivity,
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
        (label) => !/^Step \d+\/\d+$/.test(label)
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
          : isImageMessage
            ? [t("chat_generating_image")]
            : [t("chat_thinking")]
      const isEditing = editingMessageId === msg.id
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
          actionsEnabled={!isSharedView}
          isEditing={isEditing}
          isEditDragActive={isEditing && isEditDragActive}
          editingContent={isEditing ? editingContent : ""}
          editingAttachments={isEditing ? editingAttachments : []}
          editAttachmentError={isEditing ? editingAttachmentError : null}
          codeTheme={codeTheme}
          t={t}
          getSourceLabel={getSourceLabel}
          onStartEdit={startEditMessage}
          onDeleteFromMessage={deleteFromMessage}
          onRetryMessage={retryFailedMessage}
          onSaveEditedMessage={saveEditedMessage}
          onCancelEdit={cancelEditMessage}
          onEditContentChange={setEditingContent}
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
      getSourceLabel,
      isTerminalStatus,
      editingMessageId,
      editingContent,
      editingAttachments,
      editingAttachmentError,
      isEditDragActive,
      startEditMessage,
      deleteFromMessage,
      retryFailedMessage,
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
  const profileLabel =
    currentUser?.display_name || currentUser?.username || currentUser?.email || t("me_settings")
  const profileFirstName = profileLabel.trim().split(/\s+/)[0]
  const welcomeTitle =
    currentUser?.display_name || currentUser?.username
      ? t("chat_welcome_named").replace("{name}", profileFirstName)
      : t("chat_welcome_title")
  const sidebarFooter = (
    <Button
      variant="ghost"
      className="h-14 w-full justify-start gap-1.5 p-1.5 text-left"
      onClick={() => navigate("/settings/me")}
    >
      {currentUser?.avatar_url ? (
        <img
          src={currentUser.avatar_url}
          alt=""
          className="size-11 shrink-0 rounded-lg object-cover"
        />
      ) : (
        <span className="flex size-11 shrink-0 items-center justify-center rounded-lg bg-secondary text-sm font-semibold">
          {profileLabel.slice(0, 1).toUpperCase()}
        </span>
      )}
      <span className="min-w-0">
        <span className="block truncate text-sm font-semibold leading-4">{profileLabel}</span>
        {activeOrgName ? (
          <span className="block truncate text-xs font-medium leading-4 text-muted-foreground">
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
      <SheetContent side="left" className="w-57.25 bg-sidebar p-2" showCloseButton={false}>
        <ChatSidebar
          title={t("chat_title")}
          labels={{
            newChat: t("chat_new"),
            history: t("chat_history"),
            projects: t("project_title"),
            close: t("common_close"),
          }}
          activeSection={isHistoryView ? "history" : activeAgentId ? "projects" : null}
          onNewChat={startNewChat}
          onOpenHistory={() => {
            setSidebarOpen(false)
            navigate("/history")
          }}
          onOpenProjects={() => {
            setSidebarOpen(false)
            navigate("/projects")
          }}
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
          className="figma-icon size-4"
          style={{ maskImage: "url('/icon-panel.svg')" }}
        />
      ) : (
        <PanelLeftOpen aria-hidden="true" />
      )}
    </Button>
  )

  return (
    <div className="flex h-svh overflow-hidden bg-background">
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
          }}
          activeSection={isHistoryView ? "history" : activeAgentId ? "projects" : null}
          onNewChat={startNewChat}
          onOpenHistory={() => navigate("/history")}
          onOpenProjects={() => navigate("/projects")}
          footer={sidebarFooter}
        />
      </aside>
      <main
        id="main-content"
        className={`relative flex min-h-0 flex-1 flex-col overflow-hidden bg-background md:m-2 md:rounded-card md:border md:border-border ${
          desktopSidebarOpen ? "md:ml-0" : "md:ml-2"
        }`}
      >
        {isHistoryView ? (
          <HistoryPanel
            chats={historyChats}
            query={chatSearchQuery}
            leadingAction={
              <>
                {mobileSidebar}
                {desktopSidebarToggle}
              </>
            }
            labels={{
              title: t("chat_history"),
              clearAll: t("chat_history_clear_all"),
              search: t("chat_search_placeholder"),
              empty: chatSearchQuery
                ? t("chat_search_no_results")
                : t("chat_history_empty"),
              untitled: t("chat_untitled"),
              delete: t("chat_delete"),
              share: t("chat_share"),
              unshare: t("chat_unshare"),
              actions: t("chat_history_actions"),
            }}
            onQueryChange={setChatSearchQuery}
            onSelectChat={(chat) => handleSelectChat(chat)}
            onClearAll={() => setClearHistoryConfirmOpen(true)}
            onDeleteChat={setDeleteConfirmChat}
            onToggleShareChat={toggleShareChat}
            formatDate={formatHistoryDate}
          />
        ) : (
          <>
        <h1 className="sr-only">{activeChatTitle}</h1>
        <div className="relative -left-px -top-px flex h-15 w-[calc(100%+2px)] shrink-0 items-center justify-between bg-background px-3 py-2.5">
          <div className="flex min-w-0 items-center gap-2">
          {mobileSidebar}
          {desktopSidebarToggle}
          <Select value={selectedModel} onValueChange={setSelectedModel}>
            <SelectTrigger
              className="w-[267px] border-0 bg-transparent shadow-none"
              aria-label={t("chat_select_model")}
            >
              <SelectValue placeholder={t("chat_select_model")} />
            </SelectTrigger>
            <SelectContent className="max-h-96">
              {selectableChatModels.map((model) => (
                <SelectItem
                  key={model.id}
                  value={model.id}
                  disabled={model.is_available === false}
                >
                  <span className="inline-flex items-center gap-2">
                    {model.provider === "openai" ? (
                      <span
                        aria-hidden="true"
                        className="figma-icon size-5"
                        style={{ maskImage: "url('/icon-provider-openai.svg')" }}
                      />
                    ) : isImageOutputModel(model) ? (
                      <ImageIcon className="w-3.5 h-3.5 text-muted-foreground" />
                    ) : null}
                    <span>{model.display_name}</span>
                  </span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select
            value={reasoningEffort ?? "default"}
            onValueChange={(value) =>
              setReasoningEffort(value === "default" ? null : value)
            }
          >
            <SelectTrigger
              className="hidden w-[130px] border-0 bg-transparent shadow-none sm:flex"
              aria-label={t("chat_reasoning_effort")}
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {[
                ["default", t("chat_reasoning_default")],
                ["low", t("chat_reasoning_low")],
                ["medium", t("chat_reasoning_medium")],
                ["high", t("chat_reasoning_high")],
              ].map(([value, label]) => (
                <SelectItem key={value} value={value}>
                  <span className="inline-flex items-center gap-1.5">
                    <span className="flex size-5 items-center justify-center">
                      <span
                        aria-hidden="true"
                        className="figma-icon size-[14.1667px]"
                        style={{ maskImage: "url('/icon-reasoning.svg')" }}
                      />
                    </span>
                    <span>{label}</span>
                  </span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {isAgentMode ? (
            <div className="flex items-center gap-3 text-muted-foreground text-sm">
              <span>
                {t("project_label")}{" "}
                {activeAgentId ? (
                  <button
                    type="button"
                    onClick={() => navigate(`/projects/${encodeURIComponent(activeAgentId)}`)}
                    className="font-medium text-foreground hover:underline"
                  >
                    {activeAgent?.name ?? t("project_unknown")}
                  </button>
                ) : (
                  <span className="font-medium text-foreground">{t("project_unknown")}</span>
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
          <Button
            variant="ghost"
            className="hidden h-9 px-4 md:inline-flex"
            aria-disabled="true"
            title={t("chat_enable_incognito")}
          >
            <span
              aria-hidden="true"
              className="figma-icon size-4"
              style={{ maskImage: "url('/icon-incognito.svg')" }}
            />
            {t("chat_enable_incognito")}
          </Button>
        </div>
        <MessageList
          messages={visibleMessages}
          welcomeTitle={welcomeTitle}
          isLoading={isMessagesLoading}
          onScroll={handleMessagesScroll}
          containerRef={messagesContainerRef}
          endRef={messagesEndRef}
          renderMessage={renderMessage}
        />
        <ChatComposer
          message={message}
          placeholder={isAgentMode ? t("project_message_placeholder") : t("chat_message_placeholder")}
          loading={currentChatLoading || isUploadingAttachments}
          readOnly={isSharedView}
          isDragActive={isDragActive}
          pendingAttachments={pendingAttachments}
          attachmentError={attachmentError}
          webSearchEnabled={webSearchEnabled}
          codeExecutionEnabled={codeExecutionEnabled}
          inputRef={composerInputRef}
          onMessageChange={setMessage}
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
          sendLabel={t("common_send")}
          stopLabel={t("common_stop")}
          welcomeTitle={welcomeTitle}
          centered={isEmptyChat}
        />
          </>
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
        <Dialog open={clearHistoryConfirmOpen} onOpenChange={setClearHistoryConfirmOpen}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>{t("chat_history_clear_confirm_title")}</DialogTitle>
              <DialogDescription>{t("chat_history_clear_confirm_desc")}</DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button
                autoFocus
                variant="outline"
                disabled={clearingHistory}
                onClick={() => setClearHistoryConfirmOpen(false)}
              >
                {t("chat_cancel")}
              </Button>
              <Button
                variant="destructive"
                disabled={clearingHistory}
                onClick={() => clearHistory().catch(() => null)}
              >
                {clearingHistory ? t("chat_history_clearing") : t("chat_history_clear_all")}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
        <Dialog
          open={Boolean(shareDialogUrl)}
          onOpenChange={(open) => {
            if (!open) {
              setShareDialogUrl(null)
              setShareCopied(null)
            }
          }}
        >
          <DialogContent>
            <DialogHeader>
              <DialogTitle>{t("chat_share_dialog_title")}</DialogTitle>
              <DialogDescription>{t("chat_share_dialog_desc")}</DialogDescription>
            </DialogHeader>
            <div className="space-y-2">
              <Input readOnly value={shareDialogUrl ?? ""} />
              {shareCopied ? (
                <p className="text-muted-foreground text-sm">
                  {t("chat_share_dialog_copied")}
                </p>
              ) : null}
            </div>
            <DialogFooter>
              <Button onClick={() => setShareDialogUrl(null)}>
                {t("common_close")}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </main>
    </div>
  )
}
