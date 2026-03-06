import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import type { CSSProperties } from "react"
import { useNavigate, useParams } from "react-router-dom"
import { useQueryClient } from "@tanstack/react-query"

import { chatApi } from "@/lib/api"
import { modelStore, orgStore } from "@/lib/storage"
import type {
  Chat,
  ChatMessage,
  ChatMessageAttachmentInput,
  GenerationStatus,
} from "@/lib/types"
import { getTheme } from "@/lib/theme"
import { useI18n } from "@/lib/i18n-context"
import { Button } from "@/components/ui/button"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { oneDark, oneLight } from "react-syntax-highlighter/dist/esm/styles/prism"
import { Menu } from "lucide-react"
import { Sheet, SheetContent, SheetTrigger } from "@/components/ui/sheet"
import { Dialog, DialogContent } from "@/components/ui/dialog"
import ChatSidebar from "@/pages/chat/ChatSidebar"
import { ChatComposer } from "@/pages/chat/ChatComposer"
import { MessageList } from "@/pages/chat/MessageList"
import { MessageBubble } from "@/pages/chat/MessageBubble"
import {
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

export const ChatPage = () => {
  const navigate = useNavigate()
  const { chatId } = useParams()
  const [orgId, setOrgId] = useState<string | null>(orgStore.get())
  const [toolEvents, setToolEvents] = useState<ChatMessage[]>([])
  const [message, setMessage] = useState("")
  const [selectedModel, setSelectedModel] = useState<string | undefined>(
    modelStore.get() ?? undefined
  )
  const [loadingByChat, setLoadingByChat] = useState<Record<string, boolean>>({})
  const theme = getTheme()
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null)
  const [editingContent, setEditingContent] = useState("")
  const [editingAttachments, setEditingAttachments] = useState<
    ChatMessageAttachmentInput[]
  >([])
  const messagesEndRef = useRef<HTMLDivElement | null>(null)
  const messagesContainerRef = useRef<HTMLDivElement | null>(null)
  const lastScrolledIdRef = useRef<string | null>(null)
  const [autoScrollEnabled, setAutoScrollEnabled] = useState(true)
  const [pendingAttachments, setPendingAttachments] = useState<
    ChatMessageAttachmentInput[]
  >([])
  const [isDragActive, setIsDragActive] = useState(false)
  const [reasoningEffort, setReasoningEffort] = useState<string | null>(null)
  const [previewAttachment, setPreviewAttachment] =
    useState<ChatMessageAttachmentInput | null>(null)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const composerInputRef = useRef<HTMLTextAreaElement | null>(null)
  const { locale, t } = useI18n()
  const codeTheme = useMemo<Record<string, CSSProperties>>(() => {
    return theme === "dark" ? oneDark : oneLight
  }, [theme])

  const { data: orgs = [], isLoading: orgsLoading } = useOrgsMine()
  const { data: models = [] } = useModels(orgId)
  const { data: chats = [], refetch: refetchChats } = useChats(orgId)
  const {
    data: serverMessages = [],
    isLoading: isMessagesLoading,
  } = useChatMessages(chatId ?? null)
  const createChatMutation = useCreateChat(orgId)
  const deleteChatMutation = useDeleteChat(orgId)

  const appendToolEvent = useCallback((event: NonNullable<ChatMessage["tool_event"]>) => {
    if (!event) return
    setToolEvents((prev) => {
      // If event has an ID, try to update existing one
      if (event.id) {
        const existingIndex = prev.findIndex((msg) => msg.tool_event?.id === event.id)
        if (existingIndex >= 0) {
          const next = [...prev]
          const existing = next[existingIndex]
          if (existing.tool_event) {
            next[existingIndex] = {
              ...existing,
              tool_event: { ...existing.tool_event, ...event },
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
        },
      ]
    })
  }, [])

  const currentCancelRef = useRef<null | (() => void)>(null)
  const taskCursorRef = useRef<Record<string, number>>({})
  const taskSubscriptionsRef = useRef<Record<string, () => void>>({})
  const taskPollingRef = useRef<Record<string, number>>({})

  const stopGeneration = () => {
    if (currentCancelRef.current) {
      currentCancelRef.current()
      currentCancelRef.current = null
    }
    if (chatId) {
      setLoadingByChat((prev) => ({ ...prev, [chatId]: false }))
    }
  }


  const getSourceLabel = (source: { url: string; title?: string | null; host?: string | null }) => {
    const host = source.host || (() => {
      try {
        return new URL(source.url).hostname
      } catch {
        return source.url
      }
    })()
    if (source.title) {
      return `${source.title} — ${host}`
    }
    return host
  }

  const modelNameById = useMemo(() => {
    return Object.fromEntries(models.map((model) => [model.id, model.display_name]))
  }, [models])

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
      if ("task_id" in event && typeof event.task_id === "string") {
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
            if (msg.content.trim().length === 0) {
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
          appendToolEvent(toolEvent)
        }
        return
      }
      if ("error" in event && typeof event.error === "string") {
        const errorText = event.error
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
      }
    },
    [appendToolEvent, updateChatMessagesFor]
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

  const pollTaskEvents = useCallback(
    async (targetChatId: string, taskId: string, assistantId: string) => {
      if (taskPollingRef.current[taskId]) return
      const run = async () => {
        try {
          await fetchTaskEvents(targetChatId, taskId, assistantId)
          const task = await chatApi.getGenerationTask(targetChatId, taskId)
          if (isTerminalStatus(task.status)) {
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
    [fetchTaskEvents, isTerminalStatus]
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
        })
    },
    [applyStreamEvent, pollTaskEvents]
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
      if (toolMessages.length === 0) return baseMessages
      const baseIds = new Set(baseMessages.map((msg) => msg.id))
      const merged = [
        ...baseMessages,
        ...toolMessages.filter((msg) => !baseIds.has(msg.id)),
      ]
      return merged.sort(
        (a, b) => parseChatDate(a.created_at).getTime() - parseChatDate(b.created_at).getTime()
      )
    },
    [parseChatDate]
  )

  const groupedChats = useMemo(() => {
    const now = new Date()
    const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate())
    const dayMs = 24 * 60 * 60 * 1000
    const sorted = [...chats].sort(
      (a, b) =>
        parseChatDate(getChatActivityDate(b)).getTime() -
        parseChatDate(getChatActivityDate(a)).getTime()
    )
    const groups: { label: string; items: Chat[] }[] = []
    for (const chat of sorted) {
      const activityAt = parseChatDate(getChatActivityDate(chat))
      const dayDiff = Math.floor(
        (startOfToday.getTime() - new Date(activityAt.getFullYear(), activityAt.getMonth(), activityAt.getDate()).getTime()) /
          dayMs
      )
      let label = t("chat_group_older")
      if (dayDiff === 0) {
        label = t("chat_group_today")
      } else if (dayDiff <= 7) {
        label = t("chat_group_prev_7")
      } else if (dayDiff <= 30) {
        label = t("chat_group_prev_30")
      }
      const existing = groups.find((group) => group.label === label)
      if (existing) {
        existing.items.push(chat)
      } else {
        groups.push({ label, items: [chat] })
      }
    }
    return groups
  }, [chats, getChatActivityDate, parseChatDate, t])

  const formatRelativeAge = (dateString: string) => {
    const diffMs = Date.now() - parseChatDate(dateString).getTime()
    const diffMinutes = Math.floor(diffMs / (60 * 1000))
    if (diffMinutes < 60) {
      return `${Math.max(diffMinutes, 1)}m`
    }
    const diffHours = Math.floor(diffMs / (60 * 60 * 1000))
    if (diffHours < 24) {
      return `${diffHours}h`
    }
    const diffDays = Math.floor(diffMs / (24 * 60 * 60 * 1000))
    if (diffDays < 7) {
      return `${diffDays}d`
    }
    const diffWeeks = Math.floor(diffDays / 7)
    return `${diffWeeks}w`
  }

  useEffect(() => {
    if (orgsLoading) return
    if (orgs.length === 0) {
      navigate("/settings")
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
  }, [navigate, orgId, orgs, orgsLoading])

  useEffect(() => {
    if (models.length === 0 || selectedModel) return
    const stored = modelStore.get()
    if (stored && models.some((model) => model.id === stored)) {
      setSelectedModel(stored)
      return
    }
    setSelectedModel(models[0].id)
  }, [models, selectedModel])

  const activeChat = useMemo(
    () => chats.find((item) => item.id === chatId) ?? null,
    [chatId, chats]
  )
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

  const visibleMessages = useMemo(
    () => mergeToolEvents(serverMessages, toolEvents),
    [mergeToolEvents, serverMessages, toolEvents]
  )

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

  const handleMessagesScroll = () => {
    const container = messagesContainerRef.current
    if (!container) return
    const threshold = 80
    const distanceFromBottom =
      container.scrollHeight - container.scrollTop - container.clientHeight
    setAutoScrollEnabled(distanceFromBottom <= threshold)
  }


  const startNewChat = () => {
    replaceCurrentChatMessages([])
    setToolEvents([])
    setMessage("")
    setPendingAttachments([])
    navigate("/chat", { replace: true })
  }

  const createChat = async (): Promise<Chat | null> => {
    if (!orgId) return null
    const chat = await createChatMutation.mutateAsync({
      model_id: selectedModel,
      title: t("chat_new_title"),
    })
    navigate(`/chat/${chat.id}`, { replace: true })
    replaceChatMessagesFor(chat.id, [])
    refetchChats().catch(() => null)
    return chat
  }

  const deleteChat = async (chatIdToDelete: string) => {
    await deleteChatMutation.mutateAsync(chatIdToDelete)
    if (chatId === chatIdToDelete) {
      replaceCurrentChatMessages([])
      setToolEvents([])
      navigate("/chat", { replace: true })
    }
  }

  const sendMessage = async () => {
    if (chatId && loadingByChat[chatId]) return
    const trimmed = message.trim()
    if (!trimmed && pendingAttachments.length === 0) return
    setAutoScrollEnabled(true)
    let requestChatId: string | null = null
    try {
      let chat = activeChat
      if (!chat) {
        chat = await createChat()
      }
      if (!chat) return
      requestChatId = chat.id
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
        attachments: pendingAttachments,
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
        pendingAttachments,
        reasoningEffort,
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
        if (chat.id) {
          chatApi
            .messages(chat.id)
            .then((data) => replaceChatMessagesFor(chat.id, data))
            .catch(() => null)
        }
      }
    } finally {
      currentCancelRef.current = null
      if (requestChatId) {
        setLoadingByChat((prev) => ({ ...prev, [requestChatId as string]: false }))
      }
    }
  }

  const handleFilesSelected = async (files: File[]) => {
    const next = await readFilesAsAttachments(files)
    if (next.length === 0) return
    setPendingAttachments((prev) => [...prev, ...next])
  }

  const isLikelyCode = (text: string) => {
    if (!text.includes("\n")) return false
    if (text.includes("```")) return false
    const codeRegex =
      /(^|\n)\s*(def |class |import |from |function |const |let |var |if |for |while |return |#include |SELECT |INSERT |UPDATE |DELETE |WITH |-- |\/\/)/i
    const hasBracesOrSemicolons = /[{};]/.test(text)
    const hasIndentation = /(\n\t|\n {4,})/.test(text)
    return codeRegex.test(text) || hasBracesOrSemicolons || hasIndentation
  }

  const wrapInCodeFence = (text: string) => {
    const trimmed = text.replace(/\s+$/, "")
    return `\`\`\`\n${trimmed}\n\`\`\``
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
      event.preventDefault()
      setPendingAttachments((prev) => [...prev, ...next])
      return
    }

    const text = event.clipboardData.getData("text")
    if (!text || !isLikelyCode(text)) return

    event.preventDefault()
    const input = composerInputRef.current
    const start = input?.selectionStart ?? message.length
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
    setPendingAttachments((prev) => prev.filter((_, idx) => idx !== index))
  }

  const handleEditFilesSelected = async (files: File[]) => {
    const next = await readFilesAsAttachments(files)
    if (next.length === 0) return
    setEditingAttachments((prev) => [...prev, ...next])
  }

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
    const next = await readFilesAsAttachments(files)
    if (next.length > 0) {
      setPendingAttachments((prev) => [...prev, ...next])
    }
  }

  const handleEditPasteAttachments = async (
    event: React.ClipboardEvent<HTMLTextAreaElement>
  ) => {
    const items = event.clipboardData.items
    const next = await readClipboardImagesAsAttachments(items)
    if (next.length > 0) {
      event.preventDefault()
      setEditingAttachments((prev) => [...prev, ...next])
      return
    }

    const text = event.clipboardData.getData("text")
    if (!text || !isLikelyCode(text)) return

    event.preventDefault()
    const start = event.currentTarget.selectionStart ?? editingContent.length
    const end = event.currentTarget.selectionEnd ?? editingContent.length
    const wrapped = wrapInCodeFence(text)
    const nextValue = insertAtCursor(editingContent, wrapped, start, end)
    setEditingContent(nextValue)
    requestAnimationFrame(() => {
      event.currentTarget.selectionStart = start + wrapped.length
      event.currentTarget.selectionEnd = start + wrapped.length
    })
  }

  const removeEditingAttachment = (index: number) => {
    setEditingAttachments((prev) => prev.filter((_, idx) => idx !== index))
  }

  const startEditMessage = (msg: ChatMessage) => {
    setEditingMessageId(msg.id)
    setEditingContent(msg.content)
    setEditingAttachments(
      (msg.attachments ?? []).map((attachment) => ({
        file_name: attachment.file_name,
        content_type: attachment.content_type,
        data_base64: attachment.data_base64,
      }))
    )
  }

  const cancelEditMessage = () => {
    setEditingMessageId(null)
    setEditingContent("")
    setEditingAttachments([])
  }

  const saveEditedMessage = async (msg: ChatMessage) => {
    if (!activeChat) return
    if (msg.id.startsWith("temp-")) return
    const trimmed = editingContent.trim()
    if (!trimmed && editingAttachments.length === 0) return
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
        attachments: editingAttachments,
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
      editingAttachments,
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
      chatApi
        .messages(activeChat.id)
        .then((data) => replaceChatMessagesFor(activeChat.id, data))
        .catch(() => null)
    } finally {
      currentCancelRef.current = null
      setLoadingByChat((prev) => ({ ...prev, [activeChat.id]: false }))
    }
  }

  const deleteFromMessage = async (msg: ChatMessage) => {
    if (!activeChat) return
    stopGeneration()
    await chatApi.deleteBranchFromMessage(activeChat.id, msg.id)
    updateChatMessagesFor(activeChat.id, (prev) => {
      const index = prev.findIndex((item) => item.id === msg.id)
      if (index === -1) return prev
      return prev.slice(0, index)
    })
  }

  const handleSelectChat = useCallback(
    (chat: Chat, onSelect?: () => void) => {
      navigate(`/chat/${chat.id}`)
      onSelect?.()
    },
    [navigate]
  )

  return (
    <div className="flex bg-background h-screen overflow-hidden">
      <aside className="hidden md:flex shrink-0 flex-col bg-background p-4 border-r w-72 min-h-0">
        <ChatSidebar
          title={t("chat_title")}
          labels={{
            newChat: t("chat_new"),
            untitled: t("chat_untitled"),
            settings: t("common_settings"),
            delete: t("chat_delete"),
          }}
          groups={groupedChats}
          activeChatId={chatId ?? null}
          onNewChat={startNewChat}
          onSelectChat={(chat: Chat) => handleSelectChat(chat)}
          onDeleteChat={(chat: Chat) => deleteChat(chat.id)}
          onOpenSettings={() => navigate("/settings/me")}
          formatRelativeAge={formatRelativeAge}
          getChatActivityDate={getChatActivityDate}
        />
      </aside>
      <main className="flex flex-col flex-1 bg-background min-h-0 overflow-hidden">
        <div className="flex items-center gap-3 p-4 border-b">
          <Sheet open={sidebarOpen} onOpenChange={setSidebarOpen}>
            <SheetTrigger asChild>
              <Button variant="ghost" size="icon" className="md:hidden">
                <Menu className="w-5 h-5" />
              </Button>
            </SheetTrigger>
            <SheetContent side="left" className="p-4 w-72">
              <div className="flex flex-col h-full">
                <ChatSidebar
                  title={t("chat_title")}
                  labels={{
                    newChat: t("chat_new"),
                    untitled: t("chat_untitled"),
                    settings: t("common_settings"),
                    delete: t("chat_delete"),
                  }}
                  groups={groupedChats}
                  activeChatId={chatId ?? null}
                  onNewChat={startNewChat}
                  onSelectChat={(chat: Chat) => handleSelectChat(chat, () => setSidebarOpen(false))}
                  onDeleteChat={(chat: Chat) => deleteChat(chat.id)}
                  onOpenSettings={() => {
                    setSidebarOpen(false)
                    navigate("/settings/me")
                  }}
                  formatRelativeAge={formatRelativeAge}
                  getChatActivityDate={getChatActivityDate}
                />
              </div>
            </SheetContent>
          </Sheet>
          <Select value={selectedModel} onValueChange={setSelectedModel}>
            <SelectTrigger className="w-56">
              <SelectValue placeholder={t("chat_select_model")} />
            </SelectTrigger>
            <SelectContent className="max-h-96">
              {models.map((model) => (
                <SelectItem
                  key={model.id}
                  value={model.id}
                  disabled={model.is_available === false}
                >
                  {model.display_name} ({model.provider}){" "}
                  {model.is_available === false ? `(${t("common_disabled")})` : ""}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <MessageList
          messages={visibleMessages}
          emptyLabel={t("chat_no_messages")}
          isLoading={isMessagesLoading}
          onScroll={handleMessagesScroll}
          containerRef={messagesContainerRef}
          endRef={messagesEndRef}
          renderMessage={(msg) => {
            const isUser = msg.role === "user"
            const isCodeEvent = msg.tool_event?.type === "code_execution"
            const isImageMessage =
              (msg.attachments && msg.attachments.length > 0) ||
              (msg.model_name ? msg.model_name.toLowerCase().includes("image") : false)
            const activeThinking = msg.thinking_steps ?? []
            const isThinking =
              msg.role === "assistant" &&
              msg.content.trim().length === 0 &&
              (!isImageMessage || activeThinking.length > 0)
            const thinkingLabels =
              activeThinking.length > 0
                ? activeThinking
                : isImageMessage
                  ? [t("chat_generating_image")]
                  : [t("chat_thinking")]
            return (
              <MessageBubble
                key={msg.id}
                msg={msg}
                isUser={isUser}
                isCodeEvent={isCodeEvent}
                isThinking={isThinking}
                thinkingLabels={thinkingLabels}
                editingMessageId={editingMessageId}
                editingContent={editingContent}
                editingAttachments={editingAttachments}
                codeTheme={codeTheme}
                t={t}
                getSourceLabel={getSourceLabel}
                onStartEdit={startEditMessage}
                onDeleteFromMessage={deleteFromMessage}
                onSaveEditedMessage={saveEditedMessage}
                onCancelEdit={cancelEditMessage}
                onEditContentChange={setEditingContent}
                onEditPasteAttachments={handleEditPasteAttachments}
                onEditFilesSelected={handleEditFilesSelected}
                onRemoveEditingAttachment={removeEditingAttachment}
                onPreviewAttachment={setPreviewAttachment}
              />
            )
          }}
        />
        <ChatComposer
          message={message}
          placeholder={t("chat_message_placeholder")}
          loading={currentChatLoading}
          isDragActive={isDragActive}
          pendingAttachments={pendingAttachments}
          reasoningEffort={reasoningEffort}
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
          onReasoningEffortChange={setReasoningEffort}
          sendLabel={t("common_send")}
          stopLabel={t("common_stop")}
        />
        <Dialog
          open={Boolean(previewAttachment)}
          onOpenChange={(open) => {
            if (!open) setPreviewAttachment(null)
          }}
        >
          <DialogContent className="flex justify-center items-center p-2 w-auto max-w-[90vw] sm:max-w-[90vw] h-auto max-h-[90vh]">
            {previewAttachment && previewAttachment.content_type.startsWith("image/") ? (
              <img
                src={`data:${previewAttachment.content_type};base64,${previewAttachment.data_base64}`}
                alt={previewAttachment.file_name}
                className="w-auto max-w-[90vw] h-auto max-h-[90vh] object-contain"
              />
            ) : null}
          </DialogContent>
        </Dialog>
      </main>
    </div>
  )
}
