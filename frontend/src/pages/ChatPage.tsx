import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useNavigate, useParams } from "react-router-dom"

import { authApi, chatApi, modelApi, orgApi } from "@/lib/api"
import { modelStore, orgStore } from "@/lib/storage"
import type {
  Chat,
  ChatMessage,
  ChatMessageAttachmentInput,
  ChatModel,
} from "@/lib/types"
import { getTheme } from "@/lib/theme"
import { useI18n } from "@/lib/i18n-context"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter"
import { oneDark, oneLight } from "react-syntax-highlighter/dist/esm/styles/prism"
import { Menu, MoreHorizontal, Pencil, Plus, Trash2, X } from "lucide-react"
import { Sheet, SheetContent, SheetTrigger } from "@/components/ui/sheet"

export const ChatPage = () => {
  const navigate = useNavigate()
  const { chatId } = useParams()
  const [orgId, setOrgId] = useState<string | null>(orgStore.get())
  const [models, setModels] = useState<ChatModel[]>([])
  const [chats, setChats] = useState<Chat[]>([])
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [toolEvents, setToolEvents] = useState<ChatMessage[]>([])
  const [activeChat, setActiveChat] = useState<Chat | null>(null)
  const [message, setMessage] = useState("")
  const [selectedModel, setSelectedModel] = useState<string | undefined>(
    modelStore.get() ?? undefined
  )
  const [loading, setLoading] = useState(false)
  const theme = getTheme()
  const [isAdmin, setIsAdmin] = useState(false)
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null)
  const [editingContent, setEditingContent] = useState("")
  const [editingAttachments, setEditingAttachments] = useState<
    ChatMessageAttachmentInput[]
  >([])
  const messagesEndRef = useRef<HTMLDivElement | null>(null)
  const messagesContainerRef = useRef<HTMLDivElement | null>(null)
  const [autoScrollEnabled, setAutoScrollEnabled] = useState(true)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const editFileInputRef = useRef<HTMLInputElement | null>(null)
  const [pendingAttachments, setPendingAttachments] = useState<
    ChatMessageAttachmentInput[]
  >([])
  const [menuChatId, setMenuChatId] = useState<string | null>(null)
  const [previewAttachment, setPreviewAttachment] =
    useState<ChatMessageAttachmentInput | null>(null)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const { locale, t } = useI18n()
  const codeTheme = useMemo(() => {
    return theme === "dark" ? oneDark : oneLight
  }, [theme])

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
      (a, b) => parseChatDate(b.created_at).getTime() - parseChatDate(a.created_at).getTime()
    )
    const groups: { label: string; items: Chat[] }[] = []
    for (const chat of sorted) {
      const createdAt = parseChatDate(chat.created_at)
      const dayDiff = Math.floor(
        (startOfToday.getTime() - new Date(createdAt.getFullYear(), createdAt.getMonth(), createdAt.getDate()).getTime()) /
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
  }, [chats, parseChatDate, t])

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
    if (!orgId) {
      orgApi
        .list()
        .then((orgs) => {
          if (orgs.length > 0) {
            orgStore.set(orgs[0].id)
            setOrgId(orgs[0].id)
            return
          }
          navigate("/settings")
        })
        .catch(() => navigate("/settings"))
      return
    }
    modelApi
      .list(orgId)
      .then((data) => {
        setModels(data)
        if (data.length === 0) return
        const stored = modelStore.get()
        if (stored && data.some((model) => model.id === stored)) {
          setSelectedModel(stored)
          return
        }
        if (!selectedModel) {
          setSelectedModel(data[0].id)
        }
      })
      .catch(() => null)
    chatApi.list(orgId).then((data) => {
      setChats(data)
      if (chatId) {
        const match = data.find((item) => item.id === chatId)
        if (match) {
          setActiveChat(match)
        }
      }
    })
    authApi
      .me()
      .then((me) => setIsAdmin(me.is_admin))
      .catch(() => setIsAdmin(false))
  }, [navigate, orgId, selectedModel, chatId])

  useEffect(() => {
    if (selectedModel) {
      modelStore.set(selectedModel)
    }
  }, [selectedModel])

  useEffect(() => {
    if (activeChat) {
      setToolEvents([])
      chatApi
        .messages(activeChat.id)
        .then((data) => setMessages((prev) => mergeToolEvents(prev, data)))
    }
  }, [activeChat, mergeToolEvents])

  const visibleMessages = useMemo(
    () => mergeToolEvents(messages, toolEvents),
    [mergeToolEvents, messages, toolEvents]
  )

  useEffect(() => {
    if (!autoScrollEnabled) return
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages, editingMessageId, autoScrollEnabled])

  useEffect(() => {
    if (!previewAttachment) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setPreviewAttachment(null)
      }
    }
    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [previewAttachment])

  const handleMessagesScroll = () => {
    const container = messagesContainerRef.current
    if (!container) return
    const threshold = 80
    const distanceFromBottom =
      container.scrollHeight - container.scrollTop - container.clientHeight
    setAutoScrollEnabled(distanceFromBottom <= threshold)
  }


  const createChat = async (): Promise<Chat | null> => {
    if (!orgId) return null
    const chat = await chatApi.create({
      org_id: orgId,
      model_id: selectedModel,
      title: t("chat_new_title"),
    })
    setChats((prev) => [chat, ...prev])
    setActiveChat(chat)
    navigate(`/chat/${chat.id}`, { replace: true })
    setMessages([])
    return chat
  }

  const deleteChat = async (chatIdToDelete: string) => {
    await chatApi.deleteChat(chatIdToDelete)
    setChats((prev) => prev.filter((chat) => chat.id !== chatIdToDelete))
    if (activeChat?.id === chatIdToDelete) {
      setActiveChat(null)
      setMessages([])
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
      const tempUserId = `temp-user-${Date.now()}`
      const userMessage: ChatMessage = {
        id: tempUserId,
        role: "user",
        content: trimmed,
        created_at: new Date().toISOString(),
        attachments: pendingAttachments,
      }
      const assistantId = `temp-assistant-${Date.now()}`
      const assistantMessage: ChatMessage = {
        id: assistantId,
        role: "assistant",
        content: "",
        created_at: new Date().toISOString(),
        model_id: selectedModel ?? null,
        model_name: selectedModel ? modelNameById[selectedModel] ?? null : null,
      }
      setMessages((prev) => [...prev, userMessage, assistantMessage])
      setMessage("")
      setPendingAttachments([])
      const { promise, cancel } = chatApi.sendMessageStream(
        chat.id,
        trimmed,
        selectedModel,
        pendingAttachments,
        locale,
        (event) => {
        if ("delta" in event) {
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantId
                ? { ...msg, content: msg.content + event.delta }
                : msg
            )
          )
        } else if ("user_message_id" in event) {
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === tempUserId
                ? { ...msg, id: event.user_message_id }
                : msg
            )
          )
        } else if ("activity" in event) {
          const isStep = /^Step \d+\/\d+$/.test(event.activity.label)
          setMessages((prev) =>
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
          // error already handled in api layer (redirect on auth)
          return
        } else if ("done" in event && event.done) {
          setMessages((prev) =>
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
      } catch {
        if (chat.id) {
          chatApi
            .messages(chat.id)
            .then((data) => setMessages((prev) => mergeToolEvents(prev, data)))
            .catch(() => null)
        }
      }
    } finally {
      currentCancelRef.current = null
      setLoading(false)
    }
  }

  const handlePickFiles = () => {
    fileInputRef.current?.click()
  }

  const handleFilesSelected = async (
    event: React.ChangeEvent<HTMLInputElement>
  ) => {
    const files = Array.from(event.target.files ?? [])
    if (files.length === 0) return
    const next: ChatMessageAttachmentInput[] = []
    for (const file of files) {
      const dataUrl = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader()
        reader.onload = () => resolve(String(reader.result))
        reader.onerror = () => reject(reader.error)
        reader.readAsDataURL(file)
      })
      const [, base64] = dataUrl.split(",", 2)
      if (!base64) continue
      next.push({
        file_name: file.name,
        content_type: file.type || "application/octet-stream",
        data_base64: base64,
      })
    }
    setPendingAttachments((prev) => [...prev, ...next])
    event.target.value = ""
  }

  const handlePasteAttachments = async (
    event: React.ClipboardEvent<HTMLTextAreaElement>
  ) => {
    const items = Array.from(event.clipboardData.items)
    const imageItems = items.filter((item) => item.type.startsWith("image/"))
    if (imageItems.length === 0) return
    event.preventDefault()
    const next: ChatMessageAttachmentInput[] = []
    for (const item of imageItems) {
      const file = item.getAsFile()
      if (!file) continue
      const dataUrl = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader()
        reader.onload = () => resolve(String(reader.result))
        reader.onerror = () => reject(reader.error)
        reader.readAsDataURL(file)
      })
      const [, base64] = dataUrl.split(",", 2)
      if (!base64) continue
      next.push({
        file_name: file.name || "pasted-image",
        content_type: file.type,
        data_base64: base64,
      })
    }
    if (next.length > 0) {
      setPendingAttachments((prev) => [...prev, ...next])
    }
  }

  const removePendingAttachment = (index: number) => {
    setPendingAttachments((prev) => prev.filter((_, idx) => idx !== index))
  }

  const handleEditPickFiles = () => {
    editFileInputRef.current?.click()
  }

  const handleEditFilesSelected = async (
    event: React.ChangeEvent<HTMLInputElement>
  ) => {
    const files = Array.from(event.target.files ?? [])
    if (files.length === 0) return
    const next: ChatMessageAttachmentInput[] = []
    for (const file of files) {
      const dataUrl = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader()
        reader.onload = () => resolve(String(reader.result))
        reader.onerror = () => reject(reader.error)
        reader.readAsDataURL(file)
      })
      const [, base64] = dataUrl.split(",", 2)
      if (!base64) continue
      next.push({
        file_name: file.name,
        content_type: file.type || "application/octet-stream",
        data_base64: base64,
      })
    }
    setEditingAttachments((prev) => [...prev, ...next])
    event.target.value = ""
  }

  const handleEditPasteAttachments = async (
    event: React.ClipboardEvent<HTMLTextAreaElement>
  ) => {
    const items = Array.from(event.clipboardData.items)
    const imageItems = items.filter((item) => item.type.startsWith("image/"))
    if (imageItems.length === 0) return
    event.preventDefault()
    const next: ChatMessageAttachmentInput[] = []
    for (const item of imageItems) {
      const file = item.getAsFile()
      if (!file) continue
      const dataUrl = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader()
        reader.onload = () => resolve(String(reader.result))
        reader.onerror = () => reject(reader.error)
        reader.readAsDataURL(file)
      })
      const [, base64] = dataUrl.split(",", 2)
      if (!base64) continue
      next.push({
        file_name: file.name || "pasted-image",
        content_type: file.type,
        data_base64: base64,
      })
    }
    if (next.length > 0) {
      setEditingAttachments((prev) => [...prev, ...next])
    }
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
    const tempAssistantId = `temp-assistant-edit-${Date.now()}`
    let updatedUserId = msg.id
    setMessages((prev) => {
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
        created_at: new Date().toISOString(),
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
          setMessages((prev) =>
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
          setMessages((prev) =>
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
          setMessages((prev) =>
            prev.map((item) =>
              item.id === msg.id ? { ...item, id: updatedUserId } : item
            )
          )
          return
        }
        if ("error" in event) {
          setMessages((prev) =>
            prev.map((item) =>
              item.id === tempAssistantId
                ? { ...item, content: `${item.content}\n\nError: ${event.error}` }
                : item
            )
          )
          return
        }
        if (event.done) {
          setMessages((prev) => {
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
    } catch {
      chatApi
        .messages(activeChat.id)
        .then((data) => setMessages((prev) => mergeToolEvents(prev, data)))
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
    setMessages((prev) => {
      const index = prev.findIndex((item) => item.id === msg.id)
      if (index === -1) return prev
      return prev.slice(0, index)
    })
  }

  const renderChatList = (onSelect?: () => void) => (
    <div className="flex flex-col h-full min-h-0 gap-4">
      <div className="flex items-center justify-between">
        <h2 className="font-semibold text-base">{t("chat_title")}</h2>
        <Button size="sm" onClick={createChat}>
          {t("chat_new")}
        </Button>
      </div>
      <div className="flex-1 space-y-3 pr-1 min-h-0 overflow-y-auto">
        {groupedChats.map((group) => (
          <div key={group.label} className="space-y-2">
            <p className="text-muted-foreground text-xs uppercase tracking-wide">
              {group.label}
            </p>
            {group.items.map((chat) => (
              <Card
                key={chat.id}
                className={`group relative cursor-pointer p-3 ${
                  activeChat?.id === chat.id ? "border-primary" : ""
                }`}
                onClick={() => {
                  setActiveChat(chat)
                  navigate(`/chat/${chat.id}`)
                  onSelect?.()
                }}
              >
                <div className="flex items-center justify-between gap-2">
                  <p className="font-medium text-sm truncate">
                    {chat.title || t("chat_untitled")}
                  </p>
                  <div className="flex items-center gap-2">
                    <span className="text-muted-foreground text-xs">
                      {formatRelativeAge(chat.created_at)}
                    </span>
                    <button
                      type="button"
                      className="opacity-0 group-hover:opacity-100 transition"
                      onClick={(event) => {
                        event.stopPropagation()
                        setMenuChatId((prev) => (prev === chat.id ? null : chat.id))
                      }}
                    >
                      <MoreHorizontal className="w-4 h-4 text-muted-foreground" />
                    </button>
                  </div>
                </div>
                {menuChatId === chat.id ? (
                  <div className="top-10 right-2 z-10 absolute bg-background shadow p-1 border rounded-md w-32">
                    <button
                      type="button"
                      className="hover:bg-muted px-2 py-1 rounded-sm w-full text-sm text-left"
                      onClick={(event) => {
                        event.stopPropagation()
                        setMenuChatId(null)
                        deleteChat(chat.id)
                        onSelect?.()
                      }}
                    >
                      {t("chat_delete")}
                    </button>
                  </div>
                ) : null}
              </Card>
            ))}
          </div>
        ))}
      </div>
      <div className="pt-3 border-t">
        <div className="flex flex-col gap-2">
          <Button variant="outline" onClick={() => navigate("/settings/me")}>
            {t("common_settings")}
          </Button>
          {isAdmin ? (
            <Button variant="outline" onClick={() => navigate("/usage")}>
              {t("usage_title")}
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  )

  return (
    <div className="flex bg-background h-screen">
      <aside className="hidden md:flex flex-col bg-background p-4 border-r w-72 min-h-0">
        {renderChatList()}
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
                {renderChatList(() => setSidebarOpen(false))}
              </div>
            </SheetContent>
          </Sheet>
          <Select value={selectedModel} onValueChange={setSelectedModel}>
            <SelectTrigger className="w-56">
              <SelectValue placeholder={t("chat_select_model")} />
            </SelectTrigger>
            <SelectContent>
              {models.map((model) => (
                <SelectItem key={model.id} value={model.id}>
                  {model.display_name} ({model.provider})
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div
          ref={messagesContainerRef}
          className="flex-1 space-y-4 p-6 min-h-0 overflow-y-auto"
          onScroll={handleMessagesScroll}
        >
          {visibleMessages.map((msg) => {
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
              <div
                key={msg.id}
                className={`flex ${isUser ? "justify-end" : "justify-start"}`}
              >
                <div className="group max-w-[75%]">
                  <div className="bg-muted px-4 py-2 rounded-lg overflow-hidden text-foreground text-sm leading-relaxed wrap-break-word whitespace-pre-wrap">
                    <div className="flex justify-between items-center gap-2">
                    <p
                      className={`opacity-70 mb-1 text-xs uppercase ${
                        isUser ? "ml-auto text-right" : ""
                      }`}
                    >
                        {isCodeEvent
                          ? t("chat_executing_code")
                          : isUser
                            ? t("chat_you")
                            : msg.model_name || t("chat_assistant")}
                      </p>
                    </div>
                    {isCodeEvent ? (
                      <details className="space-y-3">
                        <summary className="text-xs uppercase tracking-wide cursor-pointer">
                          {t("chat_execution_details")}
                        </summary>
                        <div>
                          <p className="opacity-70 mb-1 text-xs uppercase">
                            {t("chat_execution_code")}
                          </p>
                          <pre className="bg-background/40 p-2 rounded overflow-x-auto text-xs">
                            {msg.tool_event?.code ?? ""}
                          </pre>
                        </div>
                        <div>
                          <p className="opacity-70 mb-1 text-xs uppercase">
                            {t("chat_execution_output")}
                          </p>
                          <pre className="bg-background/40 p-2 rounded overflow-x-auto text-xs">
                            {[
                              msg.tool_event?.output?.stdout,
                              msg.tool_event?.output?.stderr,
                              msg.tool_event?.output?.error
                                ? `Error: ${msg.tool_event.output.error}`
                                : null,
                              msg.tool_event?.output?.requires_approval
                                ? t("chat_execution_requires_approval")
                                : null,
                              msg.tool_event?.output?.timed_out
                                ? t("chat_execution_timed_out")
                                : null,
                              typeof msg.tool_event?.output?.exit_code === "number"
                                ? t("chat_execution_exit_code", {
                                    code: msg.tool_event.output.exit_code,
                                  })
                                : null,
                            ]
                              .filter(Boolean)
                              .join("\n") || t("chat_execution_no_output")}
                          </pre>
                        </div>
                        {msg.tool_event?.output?.output_files &&
                        msg.tool_event.output.output_files.length > 0 ? (
                          <div className="space-y-2">
                            <p className="opacity-70 text-xs uppercase">
                              {t("chat_execution_outputs")}
                            </p>
                            <div className="flex flex-wrap gap-2">
                              {msg.tool_event.output.output_files
                                .filter((file) => file.content_type.startsWith("image/"))
                                .map((file, index) => (
                                  <button
                                    key={`${file.file_name}-${index}`}
                                    type="button"
                                    className="rounded-md overflow-hidden"
                                    onClick={() =>
                                      setPreviewAttachment({
                                        file_name: file.file_name,
                                        content_type: file.content_type,
                                        data_base64: file.data_base64,
                                      })
                                    }
                                  >
                                    <img
                                      src={`data:${file.content_type};base64,${file.data_base64}`}
                                      alt={file.file_name}
                                      className="rounded-md w-20 h-20 object-cover"
                                    />
                                  </button>
                                ))}
                              {msg.tool_event.output.output_files
                                .filter((file) => !file.content_type.startsWith("image/"))
                                .map((file, index) => (
                                  <a
                                    key={`${file.file_name}-${index}`}
                                    className="hover:bg-muted px-3 py-2 border rounded-md text-xs"
                                    href={`data:${file.content_type};base64,${file.data_base64}`}
                                    download={file.file_name}
                                  >
                                    {file.file_name}
                                  </a>
                                ))}
                            </div>
                          </div>
                        ) : null}
                      </details>
                    ) : editingMessageId === msg.id ? (
                      <div className="space-y-2">
                        <Textarea
                          value={editingContent}
                          onChange={(event) => setEditingContent(event.target.value)}
                          onPaste={handleEditPasteAttachments}
                          rows={3}
                          className="bg-muted text-foreground"
                        />
                        {editingAttachments.length > 0 ? (
                          <div className="flex flex-wrap gap-2">
                            {editingAttachments.map((attachment, index) => {
                              const isImage = attachment.content_type.startsWith("image/")
                              return (
                                <div key={`${attachment.file_name}-${index}`} className="relative">
                                  {isImage ? (
                                    <button
                                      type="button"
                                      className="rounded-md overflow-hidden"
                                      onClick={() => setPreviewAttachment(attachment)}
                                    >
                                      <img
                                        src={`data:${attachment.content_type};base64,${attachment.data_base64}`}
                                        alt={attachment.file_name}
                                        className="rounded-md w-16 h-16 object-cover"
                                      />
                                    </button>
                                  ) : (
                                    <div className="px-3 py-2 border rounded-md text-xs">
                                      {attachment.file_name}
                                    </div>
                                  )}
                                  <button
                                    type="button"
                                    className="-top-2 -right-2 absolute bg-background shadow p-1 rounded-full cursor-pointer"
                                    onClick={() => removeEditingAttachment(index)}
                                  >
                                    <X className="w-3 h-3" />
                                  </button>
                                </div>
                              )
                            })}
                          </div>
                        ) : null}
                        <div className="flex justify-between items-center">
                          <div className="flex items-center gap-2">
                            <input
                              ref={editFileInputRef}
                              type="file"
                              multiple
                              className="hidden"
                              onChange={handleEditFilesSelected}
                            />
                            <Button variant="ghost" size="icon" onClick={handleEditPickFiles}>
                              <Plus className="w-5 h-5" />
                            </Button>
                          </div>
                        </div>
                        <div className="flex gap-2">
                          <Button size="sm" onClick={() => saveEditedMessage(msg)}>
                            {t("chat_save")}
                          </Button>
                          <Button size="sm" variant="outline" onClick={cancelEditMessage}>
                            {t("chat_cancel")}
                          </Button>
                        </div>
                      </div>
                    ) : (
                      <>
                        {isThinking ? (
                          <div className="space-y-2 py-2">
                            <div className="flex items-center gap-1">
                              <span
                                className="bg-muted-foreground/60 rounded-full w-2 h-2 animate-bounce"
                                style={{ animationDelay: "0ms" }}
                              />
                              <span
                                className="bg-muted-foreground/60 rounded-full w-2 h-2 animate-bounce"
                                style={{ animationDelay: "150ms" }}
                              />
                              <span
                                className="bg-muted-foreground/60 rounded-full w-2 h-2 animate-bounce"
                                style={{ animationDelay: "300ms" }}
                              />
                            </div>
                            <div className="flex flex-wrap gap-2">
                              {thinkingLabels.map((label) => (
                                <span
                                  key={label}
                                  className="px-2 py-0.5 border border-muted-foreground/30 rounded-full text-[10px] text-muted-foreground uppercase tracking-wide"
                                >
                                  {label}
                                </span>
                              ))}
                            </div>
                          </div>
                        ) : (
                          <ReactMarkdown
                            remarkPlugins={[remarkGfm]}
                            components={{
                              code(props) {
                                const { className, children, ref: refProp, ...rest } = props
                                const inline = (props as { inline?: boolean }).inline
                                void refProp
                                const match = /language-(\w+)/.exec(className || "")
                                const content = String(children).replace(/\n$/, "")
                                if (!inline && match) {
                                  return (
                                    <div className="relative">
                                      <button
                                        type="button"
                                        className="top-2 right-2 absolute bg-background/80 px-2 py-1 border border-muted-foreground/30 rounded text-[10px] text-muted-foreground hover:text-foreground uppercase tracking-wide"
                                        onClick={() =>
                                          navigator.clipboard.writeText(content)
                                        }
                                      >
                                        {t("common_copy")}
                                      </button>
                                      <SyntaxHighlighter
                                        {...rest}
                                        style={codeTheme}
                                        language={match[1]}
                                        PreTag="div"
                                      >
                                        {content}
                                      </SyntaxHighlighter>
                                    </div>
                                  )
                                }
                                return (
                                  <code className={className} {...rest}>
                                    {children}
                                  </code>
                                )
                              },
                            }}
                          >
                            {msg.content}
                          </ReactMarkdown>
                        )}
                        {msg.attachments && msg.attachments.length > 0 ? (
                          <div className="flex flex-wrap gap-2 mt-3">
                            {msg.attachments.map((attachment, index) => {
                              const isImage = attachment.content_type.startsWith("image/")
                              if (isImage) {
                                return (
                                  <button
                                    key={`${attachment.file_name}-${index}`}
                                    type="button"
                                    className="rounded-md overflow-hidden"
                                    onClick={() => setPreviewAttachment(attachment)}
                                  >
                                    <img
                                      src={`data:${attachment.content_type};base64,${attachment.data_base64}`}
                                      alt={attachment.file_name}
                                      className="rounded-md w-20 h-20 object-cover"
                                    />
                                  </button>
                                )
                              }
                              return (
                                <a
                                  key={`${attachment.file_name}-${index}`}
                                  className="hover:bg-muted px-3 py-2 border rounded-md text-xs"
                                  href={`data:${attachment.content_type};base64,${attachment.data_base64}`}
                                  download={attachment.file_name}
                                >
                                  {attachment.file_name}
                                </a>
                              )
                            })}
                          </div>
                        ) : null}
                        {msg.sources && msg.sources.length > 0 ? (
                          <div className="z-10 relative mt-3 overflow-hidden text-muted-foreground text-xs pointer-events-auto">
                            <span className="uppercase tracking-wide">
                              {t("chat_sources")}
                            </span>{" "}
                            <div className="flex flex-wrap gap-2 mt-2 max-w-full">
                            {msg.sources.map((source, index) => (
                              <a
                                key={`${source.url}-${index}`}
                                href={source.url}
                                target="_blank"
                                rel="noreferrer"
                                title={source.title ?? source.url}
                                  className="inline-flex px-2 py-0.5 border border-muted-foreground/30 rounded-full max-w-[240px] overflow-hidden text-[10px] text-muted-foreground hover:text-foreground text-ellipsis uppercase tracking-wide whitespace-nowrap cursor-pointer"
                              >
                                {getSourceLabel(source)}
                              </a>
                            ))}
                            </div>
                          </div>
                        ) : null}
                      </>
                    )}
                  </div>
                  {isUser && editingMessageId !== msg.id ? (
                    <div className="flex justify-end gap-2 opacity-0 group-hover:opacity-100 mt-2 transition">
                      <button
                        type="button"
                        className="opacity-70 hover:opacity-100 p-1 border border-transparent rounded-full"
                        onClick={() => startEditMessage(msg)}
                      >
                        <Pencil className="w-3.5 h-3.5" />
                      </button>
                      <button
                        type="button"
                        className="opacity-70 hover:opacity-100 p-1 border border-transparent rounded-full"
                        onClick={() => deleteFromMessage(msg)}
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  ) : null}
                </div>
              </div>
            )
          })}
          {messages.length === 0 ? (
            <p className="text-muted-foreground text-sm">{t("chat_no_messages")}</p>
          ) : null}
          <div ref={messagesEndRef} />
        </div>
        <div className="p-4 border-t">
          <div className="space-y-3">
            <Textarea
              value={message}
              onChange={(event) => setMessage(event.target.value)}
              onPaste={handlePasteAttachments}
              onKeyDown={(event) => {
                if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
                  event.preventDefault()
                  if (!loading && message.trim()) {
                    sendMessage()
                  }
                }
              }}
              placeholder={t("chat_message_placeholder")}
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
                        <button
                          type="button"
                          className="rounded-md overflow-hidden"
                          onClick={() => setPreviewAttachment(attachment)}
                        >
                          <img
                            src={`data:${attachment.content_type};base64,${attachment.data_base64}`}
                            alt={attachment.file_name}
                            className="rounded-md w-16 h-16 object-cover"
                          />
                        </button>
                      ) : (
                        <div className="px-3 py-2 border rounded-md text-xs">
                          {attachment.file_name}
                        </div>
                      )}
                      <button
                        type="button"
                        className="-top-2 -right-2 absolute bg-background shadow p-1 rounded-full cursor-pointer"
                        onClick={() => removePendingAttachment(index)}
                      >
                        <X className="w-3 h-3" />
                      </button>
                    </div>
                  )
                })}
              </div>
            ) : null}
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <input
                  ref={fileInputRef}
                  type="file"
                  multiple
                  className="hidden"
                  onChange={handleFilesSelected}
                  disabled={loading}
                />
                <Button variant="ghost" size="icon" onClick={handlePickFiles} disabled={loading}>
                  <Plus className="w-5 h-5" />
                </Button>
              </div>
              {loading ? (
                <Button variant="destructive" onClick={stopGeneration}>
                  {t("common_stop")}
                </Button>
              ) : (
                <Button
                  onClick={sendMessage}
                  disabled={!message.trim() && pendingAttachments.length === 0}
                >
                  {t("common_send")}
                </Button>
              )}
            </div>
          </div>
        </div>
        {previewAttachment && previewAttachment.content_type.startsWith("image/") ? (
          <div
            className="z-50 fixed inset-0 flex justify-center items-center bg-black/70 p-6"
            onClick={() => setPreviewAttachment(null)}
          >
            <div
              className="relative bg-background rounded-lg max-w-[90vw] max-h-[90vh] overflow-hidden"
              onClick={(event) => event.stopPropagation()}
            >
              <button
                type="button"
                className="top-2 right-2 absolute bg-background/80 hover:bg-background p-1 rounded-full text-foreground"
                onClick={() => setPreviewAttachment(null)}
                aria-label="Close preview"
              >
                <X className="w-4 h-4" />
              </button>
              <img
                src={`data:${previewAttachment.content_type};base64,${previewAttachment.data_base64}`}
                alt={previewAttachment.file_name}
                className="max-w-[90vw] max-h-[90vh] object-contain"
              />
            </div>
          </div>
        ) : null}
      </main>
    </div>
  )
}
