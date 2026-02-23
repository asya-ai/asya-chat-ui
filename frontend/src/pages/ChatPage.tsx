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
  const [loading, setLoading] = useState(false)
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
  const { locale, t } = useI18n()
  const codeTheme = useMemo<Record<string, CSSProperties>>(() => {
    return theme === "dark" ? oneDark : oneLight
  }, [theme])

  const { data: orgs = [], isLoading: orgsLoading } = useOrgsMine()
  const { data: models = [] } = useModels(orgId)
  const { data: chats = [], refetch: refetchChats } = useChats(orgId)
  const { data: serverMessages = [], isLoading: isMessagesLoading } = useChatMessages(chatId ?? null)
  const createChatMutation = useCreateChat(orgId)
  const deleteChatMutation = useDeleteChat(orgId)

  const appendToolEvent = (event: ChatMessage["tool_event"]) => {
    if (!event) return
    setToolEvents((prev) => [
      ...prev,
      {
        id: `tool-${Date.now()}-${Math.random().toString(16).slice(2)}`,
        role: "tool",
        content: "",
        created_at: new Date().toISOString(),
        tool_event: event,
      },
    ])
  }

  const currentCancelRef = useRef<null | (() => void)>(null)

  const stopGeneration = () => {
    if (currentCancelRef.current) {
      currentCancelRef.current()
      currentCancelRef.current = null
    }
    setLoading(false)
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

  useEffect(() => {
    setToolEvents([])
  }, [chatId])

  useEffect(() => {
    if (selectedModel) {
      modelStore.set(selectedModel)
    }
  }, [selectedModel])

  const visibleMessages = useMemo(
    () => mergeToolEvents(serverMessages, toolEvents),
    [mergeToolEvents, serverMessages, toolEvents]
  )

  useEffect(() => {
    if (!autoScrollEnabled && !loading) return

    const lastMsg = visibleMessages[visibleMessages.length - 1]
    if (!lastMsg) return

    const isNew = lastMsg.id !== lastScrolledIdRef.current
    if (isNew) {
      lastScrolledIdRef.current = lastMsg.id
      if (lastMsg.role === "user") {
        messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
      } else if (lastMsg.role === "assistant") {
        const target = messagesContainerRef.current?.querySelector(
          `[data-message-id="${lastMsg.id}"]`
        )
        if (target) {
          target.scrollIntoView({ behavior: "smooth", block: "start" })
        } else {
          messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
        }
      }
    }
  }, [visibleMessages, autoScrollEnabled, loading])

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
    if (loading) return
    const trimmed = message.trim()
    if (!trimmed && pendingAttachments.length === 0) return
    setAutoScrollEnabled(true)
    setLoading(true)
    try {
      let chat = activeChat
      if (!chat) {
        chat = await createChat()
      }
      if (!chat) return
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
        if ("delta" in event) {
          updateMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantId
                ? { ...msg, content: msg.content + event.delta }
                : msg
            )
          )
        } else if ("user_message_id" in event) {
          updateMessages((prev) =>
            prev.map((msg) =>
              msg.id === tempUserId
                ? { ...msg, id: event.user_message_id }
                : msg
            )
          )
        } else if ("activity" in event) {
          const isStep = /^Step \d+\/\d+$/.test(event.activity.label)
          updateMessages((prev) =>
            prev.map((msg) => {
              if (msg.id !== assistantId) return msg
              const current = msg.thinking_steps ?? []
              if (event.activity.state === "start") {
                const withoutSteps = isStep
                  ? current.filter((label) => !/^Step \d+\/\d+$/.test(label))
                  : current
                const next = Array.from(
                  new Set([...withoutSteps, event.activity.label])
                )
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
                thinking_steps: current.filter(
                  (label) => label !== event.activity.label
                ),
              }
            })
          )
        } else if ("tool_event" in event) {
          appendToolEvent(event.tool_event)
        } else if ("error" in event) {
          updateMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantId
                ? { ...msg, content: event.error, thinking_steps: [] }
                : msg
            )
          )
          return
        } else if ("done" in event && event.done) {
          updateMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantId
                ? {
                    ...msg,
                    content: msg.content || event.content || "",
                    model_name: event.model_name ?? msg.model_name ?? null,
                    model_id: event.model_id ?? msg.model_id ?? null,
                    attachments: event.attachments ?? msg.attachments,
                    sources: event.sources ?? msg.sources,
                    thinking_steps: [],
                  }
                : msg
            )
          )
        }
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
      setLoading(false)
    }
  }

  const handleFilesSelected = async (files: File[]) => {
    const next = await readFilesAsAttachments(files)
    if (next.length === 0) return
    setPendingAttachments((prev) => [...prev, ...next])
  }

  const handlePasteAttachments = async (
    event: React.ClipboardEvent<HTMLTextAreaElement>
  ) => {
    const next = await readClipboardImagesAsAttachments(event.clipboardData.items)
    if (next.length === 0) return
    event.preventDefault()
    setPendingAttachments((prev) => [...prev, ...next])
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
    const next = await readClipboardImagesAsAttachments(event.clipboardData.items)
    if (next.length === 0) return
    event.preventDefault()
    setEditingAttachments((prev) => [...prev, ...next])
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
    setLoading(true)
    setToolEvents([])
    const activityAt = new Date().toISOString()
    bumpChatActivity(activeChat.id, activityAt)
    const tempAssistantId = `temp-assistant-edit-${Date.now()}`
    let updatedUserId = msg.id
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
        if ("delta" in event) {
          updateMessages((prev) =>
            prev.map((item) =>
              item.id === tempAssistantId
                ? { ...item, content: item.content + event.delta }
                : item
            )
          )
          return
        }
        if ("activity" in event) {
          const isStep = /^Step \d+\/\d+$/.test(event.activity.label)
          updateMessages((prev) =>
            prev.map((item) => {
              if (item.id !== tempAssistantId) return item
              const current = item.thinking_steps ?? []
              if (event.activity.state === "start") {
                const withoutSteps = isStep
                  ? current.filter((label) => !/^Step \d+\/\d+$/.test(label))
                  : current
                const next = Array.from(
                  new Set([...withoutSteps, event.activity.label])
                )
                return { ...item, thinking_steps: next }
              }
              if (isStep) {
                return item
              }
              if (item.content.trim().length === 0) {
                return item
              }
              return {
                ...item,
                thinking_steps: current.filter(
                  (label) => label !== event.activity.label
                ),
              }
            })
          )
          return
        }
        if ("tool_event" in event) {
          appendToolEvent(event.tool_event)
          return
        }
        if ("user_message_id" in event) {
          if (event.edited_message_id && event.edited_message_id !== msg.id) {
            return
          }
          updatedUserId = event.user_message_id
          updateMessages((prev) =>
            prev.map((item) =>
              item.id === msg.id ? { ...item, id: updatedUserId } : item
            )
          )
          return
        }
        if ("error" in event) {
          updateMessages((prev) =>
            prev.map((item) =>
              item.id === tempAssistantId
                ? { ...item, content: event.error, thinking_steps: [] }
                : item
            )
          )
          return
        }
        if (event.done) {
          updateMessages((prev) => {
            const index = prev.findIndex(
              (item) => item.id === updatedUserId || item.id === msg.id
            )
            if (index === -1) return prev
            const userMessage: ChatMessage = {
              ...prev[index],
              id: updatedUserId,
              content: trimmed,
              attachments: editingAttachments,
            }
            const assistantMessage: ChatMessage = {
              id: event.message_id ?? tempAssistantId,
              role: "assistant",
              content: event.content ?? "",
              created_at: new Date().toISOString(),
              model_id: event.model_id ?? selectedModel ?? null,
              model_name:
                event.model_name ??
                (selectedModel ? modelNameById[selectedModel] ?? null : null),
              attachments: event.attachments ?? [],
              sources: event.sources ?? [],
              thinking_steps: [],
            }
            return [...prev.slice(0, index), userMessage, assistantMessage]
          })
        }
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
      setLoading(false)
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
    <div className="flex bg-background h-screen">
      <aside className="hidden md:flex flex-col bg-background p-4 border-r w-72 min-h-0">
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
      <main className="flex flex-col flex-1 bg-background min-h-0">
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
                <SelectItem key={model.id} value={model.id}>
                  {model.display_name} ({model.provider})
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
          loading={loading}
          isDragActive={isDragActive}
          pendingAttachments={pendingAttachments}
          reasoningEffort={reasoningEffort}
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
          <DialogContent className="p-2 max-w-[90vw] max-h-[90vh]">
            {previewAttachment && previewAttachment.content_type.startsWith("image/") ? (
              <img
                src={`data:${previewAttachment.content_type};base64,${previewAttachment.data_base64}`}
                alt={previewAttachment.file_name}
                className="max-w-[90vw] max-h-[90vh] object-contain"
              />
            ) : null}
          </DialogContent>
        </Dialog>
      </main>
    </div>
  )
}
