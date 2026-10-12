import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ClipboardEvent,
  type CSSProperties,
  type DragEvent,
} from "react"
import { ExternalLink } from "lucide-react"
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism"
import { toast } from "sonner"

import { EldigenTreeRive } from "@/components/EldigenTreeRive"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Sheet, SheetContent, SheetTrigger } from "@/components/ui/sheet"
import { chatApi, integrationsApi, modelApi, orgApi } from "@/lib/api"
import { useAuth } from "@/lib/auth-context"
import {
  EMBED_SESSION_MESSAGE,
  ackEmbedSessionToPopup,
  openEmbedAuthPopup,
  requestEmbedSessionFromPopup,
} from "@/lib/embed-auth"
import { useI18n } from "@/lib/i18n-context"
import {
  actionInfoLevelStore,
  loginOrgStore,
  modelStore,
  orgStore,
  tokenStore,
  type ActionInfoLevel,
} from "@/lib/storage"
import type { Chat, ChatMessage, ChatMessageAttachmentInput, ChatModel } from "@/lib/types"
import {
  originAllowedByEmbedList,
  parseEmbedOrigins,
  ppConnectApiKeyFromMessage,
} from "@/lib/embed-origins"
import { ChatComposer, type ChatComposerHandle } from "@/pages/chat/ChatComposer"
import { ChatModelSelect } from "@/pages/chat/ChatModelSelect"
import { ChatSidebar } from "@/pages/chat/ChatSidebar"
import { MessageBubble } from "@/pages/chat/MessageBubble"
import { MessageList } from "@/pages/chat/MessageList"

const isTerminalStatus = (status?: string | null) =>
  status === "completed" || status === "failed" || status === "cancelled"

const extractAttachmentIdFromContentUrl = (url?: string): string | undefined => {
  if (!url) return undefined
  const match = url.match(/\/attachments\/([0-9a-fA-F-]{36})\/content/)
  return match?.[1]
}

const toEditableAttachments = (
  attachments: ChatMessage["attachments"]
): ChatMessageAttachmentInput[] =>
  (attachments ?? []).map((attachment) => ({
    upload_id:
      ("id" in attachment ? attachment.id : undefined) ||
      extractAttachmentIdFromContentUrl(attachment.content_url),
    file_name: attachment.file_name,
    content_type: attachment.content_type,
    data_base64: attachment.data_base64,
    content_url: attachment.content_url,
  }))

const noopClipboard = (_event: ClipboardEvent<HTMLTextAreaElement>) => undefined
const noopDrag = (_event: DragEvent<HTMLDivElement>) => undefined
const noopFiles = (_files: File[]) => undefined
const noopAttachment = (_attachment: ChatMessageAttachmentInput) => undefined

const CATALOG_ID = "pitchpatterns"
const PROTOCOL_VERSION = 1
const EMBED_CHAT_STORAGE_KEY = "chatui_embed_pp_chat_id"

const readStoredEmbedChatId = (): string | null => {
  try {
    return sessionStorage.getItem(EMBED_CHAT_STORAGE_KEY)
  } catch {
    return null
  }
}

const writeStoredEmbedChatId = (chatId: string) => {
  try {
    sessionStorage.setItem(EMBED_CHAT_STORAGE_KEY, chatId)
  } catch {
    // ignore
  }
}

const clearStoredEmbedChatId = () => {
  try {
    sessionStorage.removeItem(EMBED_CHAT_STORAGE_KEY)
  } catch {
    // ignore
  }
}

type PpConnectStatus = "unknown" | "connected" | "requesting" | "missing" | "error"

const postToParent = (data: Record<string, unknown>, targetOrigin: string) => {
  if (!window.parent || window.parent === window) return
  window.parent.postMessage(data, targetOrigin === "*" ? "*" : targetOrigin)
}

/** Merge parent postMessage context with embed host (from event.origin). */
const buildEmbedPageContext = (
  fromParent: unknown,
  parentOrigin: string | null
): Record<string, unknown> | undefined => {
  const out: Record<string, unknown> = {}
  if (parentOrigin) {
    try {
      const parsed = new URL(parentOrigin)
      out.base_origin = parentOrigin
      out.base_host = parsed.host
    } catch {
      out.base_origin = parentOrigin
    }
  }
  if (fromParent && typeof fromParent === "object" && !Array.isArray(fromParent)) {
    Object.assign(out, fromParent as Record<string, unknown>)
  } else if (typeof fromParent === "string" && fromParent.trim()) {
    out.page_url = fromParent.trim()
  }
  // Normalize common aliases so the model always sees page_url when provided
  if (typeof out.url === "string" && !out.page_url) {
    out.page_url = out.url
  }
  if (typeof out.href === "string" && !out.page_url) {
    out.page_url = out.href
  }
  return Object.keys(out).length ? out : undefined
}

export const PitchPatternsEmbedPage = () => {
  const { t, locale } = useI18n()
  const { token, setToken, clearToken } = useAuth()
  const [org, setOrg] = useState(() => loginOrgStore.get() ?? "")
  const [loginError, setLoginError] = useState<string | null>(null)
  const [loginLoading, setLoginLoading] = useState(false)
  const [bootError, setBootError] = useState<string | null>(null)
  const [ready, setReady] = useState(false)
  const [allowedOrigins, setAllowedOrigins] = useState<string[]>([])
  const [chatId, setChatId] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [sending, setSending] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [models, setModels] = useState<ChatModel[]>([])
  const [selectedModel, setSelectedModel] = useState<string | undefined>(
    () => modelStore.get() ?? undefined
  )
  const [modelMenuOpen, setModelMenuOpen] = useState(false)
  const [ppConnectStatus, setPpConnectStatus] = useState<PpConnectStatus>("unknown")
  const [ppConnectError, setPpConnectError] = useState<string | null>(null)
  const [actionInfoLevel, setActionInfoLevel] = useState<ActionInfoLevel>(() =>
    actionInfoLevelStore.get()
  )
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null)
  const [editingAttachments, setEditingAttachments] = useState<
    ChatMessageAttachmentInput[]
  >([])
  const [deleteConfirmMessage, setDeleteConfirmMessage] = useState<ChatMessage | null>(
    null
  )
  const pageContextRef = useRef<unknown>(null)
  const parentOriginRef = useRef<string | null>(null)
  const readySentRef = useRef(false)
  const popupRef = useRef<Window | null>(null)
  const ppConnectInFlightRef = useRef(false)
  const applyPpConnectRef = useRef<(apiKey: string) => Promise<void>>(async () => {})
  const startNewChatRef = useRef<() => Promise<void>>(async () => {})
  const composerRef = useRef<ChatComposerHandle>(null)
  const messagesEndRef = useRef<HTMLDivElement | null>(null)
  const messagesContainerRef = useRef<HTMLDivElement | null>(null)
  const chatIdRef = useRef<string | null>(null)
  const bootInFlightRef = useRef(false)
  const bootGenerationRef = useRef(0)
  const ensureChatInFlightRef = useRef<Promise<string> | null>(null)
  const emptyAttachments = useMemo<ChatMessageAttachmentInput[]>(() => [], [])
  const codeTheme = useMemo<Record<string, CSSProperties>>(() => oneDark, [])

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

  const parentOriginAllowed = useCallback(
    (origin: string) => originAllowedByEmbedList(origin, allowedOrigins),
    [allowedOrigins]
  )

  const requestPpConnect = useCallback(() => {
    setPpConnectStatus("requesting")
    setPpConnectError(null)
    postToParent(
      { type: "chatui:requestPpConnect", version: PROTOCOL_VERSION },
      parentOriginRef.current ?? "*"
    )
  }, [])

  const applyPpConnect = useCallback(
    async (apiKey: string) => {
      if (ppConnectInFlightRef.current) return
      const orgId = orgStore.get()
      if (!orgId) {
        setPpConnectStatus("error")
        setPpConnectError(t("embed_pp_connect_failed"))
        return
      }
      ppConnectInFlightRef.current = true
      setPpConnectStatus("requesting")
      setPpConnectError(null)
      try {
        await integrationsApi.connect(CATALOG_ID, orgId, apiKey)
        setPpConnectStatus("connected")
        postToParent(
          { type: "chatui:ppConnected", version: PROTOCOL_VERSION },
          parentOriginRef.current ?? "*"
        )
      } catch (err) {
        setPpConnectStatus("error")
        setPpConnectError(
          err instanceof Error ? err.message : t("embed_pp_connect_failed")
        )
      } finally {
        ppConnectInFlightRef.current = false
      }
    },
    [t]
  )
  applyPpConnectRef.current = applyPpConnect

  const applyEmbedSession = useCallback(
    (accessToken: string) => {
      const next = accessToken.trim()
      if (!next || next.split(".").length < 3) return
      // Cancel any in-flight boot from a previous/stale token.
      bootGenerationRef.current += 1
      bootInFlightRef.current = false
      setLoginLoading(false)
      setLoginError(null)
      setBootError(null)
      setReady(false)
      orgStore.clear()
      setToken(next)
      ackEmbedSessionToPopup(popupRef.current)
      // Let the popup close itself after ACK — do not race-close it here.
    },
    [setToken]
  )

  useEffect(() => {
    if (readySentRef.current) return
    readySentRef.current = true
    // Prefer referrer as early parent origin (allowlist checked once catalog loads).
    try {
      if (document.referrer) {
        parentOriginRef.current = new URL(document.referrer).origin
      }
    } catch {
      // ignore
    }
    postToParent({ type: "chatui:ready", version: PROTOCOL_VERSION }, "*")
  }, [])

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      const data = event.data as {
        type?: string
        accessToken?: string
        apiKey?: string
        context?: unknown
      } | null
      if (!data || typeof data !== "object" || typeof data.type !== "string") return

      if (data.type === EMBED_SESSION_MESSAGE) {
        if (event.origin !== window.location.origin) return
        const accessToken =
          typeof data.accessToken === "string" ? data.accessToken.trim() : ""
        if (!accessToken) return
        applyEmbedSession(accessToken)
        return
      }

      if (data.type === "chatui:pageContext") {
        if (allowedOrigins.length && !parentOriginAllowed(event.origin)) {
          setPpConnectStatus("error")
          setPpConnectError(t("embed_origin_denied"))
          return
        }
        parentOriginRef.current = event.origin
        pageContextRef.current = data.context ?? null
        return
      }

      if (data.type === "chatui:ppConnect") {
        if (allowedOrigins.length && !parentOriginAllowed(event.origin)) {
          setPpConnectStatus("error")
          setPpConnectError(t("embed_origin_denied"))
          return
        }
        parentOriginRef.current = event.origin
        const apiKey = ppConnectApiKeyFromMessage(
          data as unknown as Record<string, unknown>
        )
        if (!apiKey) {
          setPpConnectStatus("missing")
          setPpConnectError(t("embed_pp_connect_missing_key"))
          return
        }
        void applyPpConnectRef.current(apiKey)
        return
      }

      if (data.type === "chatui:newChat") {
        if (allowedOrigins.length && !parentOriginAllowed(event.origin)) return
        parentOriginRef.current = event.origin
        void startNewChatRef.current()
      }
    }
    window.addEventListener("message", onMessage)
    return () => window.removeEventListener("message", onMessage)
  }, [allowedOrigins.length, applyEmbedSession, parentOriginAllowed, t])

  // IdP COOP often nulls window.opener — ask the popup we opened for the session.
  useEffect(() => {
    if (!loginLoading) return
    requestEmbedSessionFromPopup(popupRef.current)
    const timer = window.setInterval(() => {
      requestEmbedSessionFromPopup(popupRef.current)
    }, 250)
    return () => window.clearInterval(timer)
  }, [loginLoading])

  const finishBoot = useCallback(
    (orgId: string, nextChatId: string | null, nextMessages: ChatMessage[]) => {
      if (nextChatId) {
        writeStoredEmbedChatId(nextChatId)
        chatIdRef.current = nextChatId
      }
      setChatId(nextChatId)
      setMessages(nextMessages)
      setReady(true)
      postToParent(
        {
          type: "chatui:authed",
          version: PROTOCOL_VERSION,
          orgId,
          ...(nextChatId ? { chatId: nextChatId } : {}),
        },
        parentOriginRef.current ?? "*"
      )
      postToParent(
        { type: "chatui:requestPageContext", version: PROTOCOL_VERSION },
        parentOriginRef.current ?? "*"
      )
    },
    []
  )

  const bootChat = useCallback(async () => {
    const generation = bootGenerationRef.current
    if (bootInFlightRef.current) return
    bootInFlightRef.current = true
    setBootError(null)
    const isStale = () => generation !== bootGenerationRef.current
    try {
      let orgId = orgStore.get()
      if (!orgId) {
        const orgs = await orgApi.mine()
        if (isStale()) return
        orgId = orgs[0]?.id ?? null
        if (orgId) orgStore.set(orgId)
      }
      if (!orgId) throw new Error("No organisation")

      const apps = await integrationsApi.listCatalog(orgId)
      if (isStale()) return
      const pp = apps.find((app) => app.catalog_id === CATALOG_ID)
      const origins = parseEmbedOrigins(pp?.allowed_embed_origins)
      setAllowedOrigins(origins)
      if (
        parentOriginRef.current &&
        !originAllowedByEmbedList(parentOriginRef.current, origins)
      ) {
        throw new Error("Parent origin is not allowed")
      }

      if (pp?.connected) {
        setPpConnectStatus("connected")
      } else {
        requestPpConnect()
      }

      const modelsList = await modelApi.list(orgId, { scope: "allowed" })
      if (isStale()) return
      setModels(modelsList)
      const modelId = modelStore.get() ?? modelsList[0]?.id
      if (modelId) {
        modelStore.set(modelId)
        setSelectedModel(modelId)
      }

      // Resume existing embed session only — never create empty chats on boot.
      const storedId = readStoredEmbedChatId()
      if (storedId) {
        try {
          await chatApi.get(storedId)
          if (isStale()) return
          const existingMessages = await chatApi.messages(storedId)
          if (isStale()) return
          finishBoot(orgId, storedId, existingMessages)
          return
        } catch {
          clearStoredEmbedChatId()
        }
      }

      if (isStale()) return
      finishBoot(orgId, null, [])
    } catch (err) {
      if (isStale()) return
      const message = err instanceof Error ? err.message : t("common_error")
      if (/invalid authentication credentials/i.test(message)) {
        // Only drop the session if this boot still owns it.
        clearToken()
        orgStore.clear()
        setBootError(null)
        setLoginError(t("embed_auth_failed"))
        setReady(false)
        return
      }
      setBootError(message)
      setReady(false)
    } finally {
      if (generation === bootGenerationRef.current) {
        bootInFlightRef.current = false
      }
    }
  }, [clearToken, finishBoot, requestPpConnect, t])

  const ensureChatId = useCallback(async (): Promise<string> => {
    if (chatIdRef.current) return chatIdRef.current
    if (ensureChatInFlightRef.current) return ensureChatInFlightRef.current

    const run = (async () => {
      const storedId = readStoredEmbedChatId()
      if (storedId) {
        try {
          await chatApi.get(storedId)
          chatIdRef.current = storedId
          setChatId(storedId)
          return storedId
        } catch {
          clearStoredEmbedChatId()
        }
      }

      const orgId = orgStore.get()
      if (!orgId) throw new Error("No organisation")
      const modelsList = models.length
        ? models
        : await modelApi.list(orgId, { scope: "allowed" })
      if (!models.length) setModels(modelsList)
      const modelId = selectedModel ?? modelStore.get() ?? modelsList[0]?.id
      if (modelId) {
        modelStore.set(modelId)
        setSelectedModel(modelId)
      }
      const chat = await chatApi.create({
        org_id: orgId,
        model_id: modelId ?? undefined,
      })
      writeStoredEmbedChatId(chat.id)
      chatIdRef.current = chat.id
      setChatId(chat.id)
      postToParent(
        { type: "chatui:chat", version: PROTOCOL_VERSION, chatId: chat.id },
        parentOriginRef.current ?? "*"
      )
      return chat.id
    })()

    ensureChatInFlightRef.current = run
    try {
      return await run
    } finally {
      ensureChatInFlightRef.current = null
    }
  }, [models, selectedModel])

  const startNewChat = useCallback(async () => {
    setSending(false)
    composerRef.current?.setValue("")
    setBootError(null)
    clearStoredEmbedChatId()
    chatIdRef.current = null
    setChatId(null)
    setMessages([])
    setSidebarOpen(false)
    postToParent(
      { type: "chatui:chat", version: PROTOCOL_VERSION, chatId: null },
      parentOriginRef.current ?? "*"
    )
    postToParent(
      { type: "chatui:requestPageContext", version: PROTOCOL_VERSION },
      parentOriginRef.current ?? "*"
    )
  }, [])

  startNewChatRef.current = startNewChat

  const selectChat = useCallback(
    async (chat: Chat) => {
      if (chat.id === chatId) {
        setSidebarOpen(false)
        return
      }
      setSending(false)
      composerRef.current?.setValue("")
      setBootError(null)
      setSidebarOpen(false)
      try {
        const nextMessages = await chatApi.messages(chat.id)
        writeStoredEmbedChatId(chat.id)
        chatIdRef.current = chat.id
        setChatId(chat.id)
        setMessages(nextMessages)
        postToParent(
          { type: "chatui:chat", version: PROTOCOL_VERSION, chatId: chat.id },
          parentOriginRef.current ?? "*"
        )
        postToParent(
          { type: "chatui:requestPageContext", version: PROTOCOL_VERSION },
          parentOriginRef.current ?? "*"
        )
      } catch (err) {
        setBootError(err instanceof Error ? err.message : t("common_error"))
      }
    },
    [chatId, t]
  )

  const openFullPage = useCallback(() => {
    if (!chatId) return
    window.open(
      `${window.location.origin}/chat/${encodeURIComponent(chatId)}`,
      "_blank",
      "noopener,noreferrer"
    )
  }, [chatId])

  const openMainApp = useCallback((path: string) => {
    window.open(`${window.location.origin}${path}`, "_blank", "noopener,noreferrer")
  }, [])

  useEffect(() => {
    if (!token || ready) return
    void bootChat()
  }, [token, ready, bootChat])

  const startSignIn = () => {
    setLoginError(null)
    setBootError(null)
    setLoginLoading(true)
    setReady(false)
    // Invalidate any in-flight boot so a late 401 cannot wipe the new session.
    bootGenerationRef.current += 1
    bootInFlightRef.current = false
    const orgValue = org.trim().toLowerCase()
    if (orgValue) loginOrgStore.set(orgValue)
    const popup = openEmbedAuthPopup(orgValue || null)
    popupRef.current = popup
    if (!popup) {
      setLoginLoading(false)
      setLoginError(t("embed_popup_blocked"))
      return
    }
    requestEmbedSessionFromPopup(popup)
    const timer = window.setInterval(() => {
      if (!popup.closed) {
        requestEmbedSessionFromPopup(popup)
        return
      }
      window.clearInterval(timer)
      if (!tokenStore.get()) setLoginLoading(false)
    }, 250)
  }

  const conversationMessages = useMemo(
    () => messages.filter((msg) => msg.role === "user" || msg.role === "assistant"),
    [messages]
  )
  const visibleMessages = useMemo(() => {
    if (actionInfoLevel === "detailed") {
      return messages.filter(
        (msg) =>
          msg.role === "user" ||
          msg.role === "assistant" ||
          Boolean(msg.tool_event)
      )
    }
    return conversationMessages
  }, [actionInfoLevel, conversationMessages, messages])
  const isEmptyChat = conversationMessages.length === 0
  const selectableChatModels = useMemo(
    () =>
      models.filter((model) => {
        const name = `${model.display_name} ${model.model_name}`.toLowerCase()
        return !/(^|[\s/_-])(embedding|embeddings|text-embedding|embed)([\s/_-]|$)/.test(
          name
        )
      }),
    [models]
  )

  const send = async (overrideText?: string) => {
    if (sending) return
    const content = (overrideText ?? composerRef.current?.getValue() ?? "").trim()
    if (!content) return
    setSending(true)
    composerRef.current?.setValue("")
    const pageContext = buildEmbedPageContext(
      pageContextRef.current,
      parentOriginRef.current
    )
    try {
      const activeChatId = await ensureChatId()
      const { promise } = chatApi.sendMessageStream(
        activeChatId,
        content,
        selectedModel ?? modelStore.get() ?? undefined,
        undefined,
        undefined,
        undefined,
        undefined,
        undefined,
        (event) => {
          if ("user_message_id" in event && event.user_message_id) {
            setMessages((prev) => [
              ...prev,
              {
                id: event.user_message_id,
                role: "user",
                content,
                created_at: new Date().toISOString(),
              } as ChatMessage,
            ])
          }
          if ("task_id" in event && event.assistant_message_id) {
            setMessages((prev) => {
              if (prev.some((m) => m.id === event.assistant_message_id)) return prev
              return [
                ...prev,
                {
                  id: event.assistant_message_id!,
                  role: "assistant",
                  content: "",
                  generation_status: "streaming",
                  created_at: new Date().toISOString(),
                } as ChatMessage,
              ]
            })
          }
          if ("delta" in event && event.delta) {
            setMessages((prev) => {
              const next = [...prev]
              for (let i = next.length - 1; i >= 0; i -= 1) {
                if (next[i].role === "assistant") {
                  next[i] = { ...next[i], content: `${next[i].content}${event.delta}` }
                  break
                }
              }
              return next
            })
          }
          if ("done" in event && event.done) {
            void chatApi.messages(activeChatId).then(setMessages).catch(() => null)
          }
        },
        pageContext ?? undefined
      )
      await promise
    } catch (err) {
      setBootError(err instanceof Error ? err.message : t("common_error"))
    } finally {
      setSending(false)
    }
  }

  const reloadMessages = useCallback(async (activeChatId: string) => {
    try {
      setMessages(await chatApi.messages(activeChatId))
    } catch {
      // ignore
    }
  }, [])

  const startEditMessage = useCallback((msg: ChatMessage) => {
    if (msg.role !== "user") return
    setEditingMessageId(msg.id)
    setEditingAttachments(toEditableAttachments(msg.attachments))
  }, [])

  const cancelEditMessage = useCallback(() => {
    setEditingMessageId(null)
    setEditingAttachments([])
  }, [])

  const removeEditingAttachment = useCallback((index: number) => {
    setEditingAttachments((prev) => prev.filter((_, idx) => idx !== index))
  }, [])

  const saveEditedMessage = useCallback(
    async (msg: ChatMessage, content: string) => {
      if (!chatId || msg.id.startsWith("temp-") || sending) return
      const trimmed = content.trim()
      if (!trimmed && editingAttachments.length === 0) return
      setSending(true)
      cancelEditMessage()
      const activityAt = new Date().toISOString()
      const tempAssistantId = `temp-assistant-edit-${Date.now()}`
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
          created_at: activityAt,
          model_id: selectedModel ?? null,
          generation_status: "queued",
        }
        return [...prev.slice(0, index), updated, placeholder]
      })
      try {
        const { promise } = chatApi.editMessageStream(
          chatId,
          msg.id,
          trimmed,
          selectedModel ?? modelStore.get() ?? undefined,
          editingAttachments
            .filter((item) => item.upload_id)
            .map((item) => ({
              upload_id: item.upload_id,
              file_name: item.file_name,
              content_type: item.content_type,
            })),
          undefined,
          undefined,
          undefined,
          locale,
          (event) => {
            if ("delta" in event && event.delta) {
              setMessages((prev) => {
                const next = [...prev]
                for (let i = next.length - 1; i >= 0; i -= 1) {
                  if (next[i].role === "assistant") {
                    next[i] = { ...next[i], content: `${next[i].content}${event.delta}` }
                    break
                  }
                }
                return next
              })
            }
            if ("done" in event && event.done) {
              void reloadMessages(chatId)
            }
          }
        )
        await promise
      } catch (err) {
        setBootError(err instanceof Error ? err.message : t("common_error"))
        void reloadMessages(chatId)
      } finally {
        setSending(false)
      }
    },
    [
      cancelEditMessage,
      chatId,
      editingAttachments,
      locale,
      reloadMessages,
      selectedModel,
      sending,
      t,
    ]
  )

  const deleteFromMessage = useCallback((msg: ChatMessage) => {
    setDeleteConfirmMessage(msg)
  }, [])

  const confirmDeleteFromMessage = useCallback(async () => {
    if (!chatId || !deleteConfirmMessage) return
    const msg = deleteConfirmMessage
    setDeleteConfirmMessage(null)
    try {
      await chatApi.deleteBranchFromMessage(chatId, msg.id)
      setMessages((prev) => {
        const index = prev.findIndex((item) => item.id === msg.id)
        if (index === -1) return prev
        return prev.slice(0, index)
      })
    } catch (err) {
      setBootError(err instanceof Error ? err.message : t("common_error"))
    }
  }, [chatId, deleteConfirmMessage, t])

  const retryFailedMessage = useCallback(
    async (failedMessage: ChatMessage) => {
      if (!chatId || sending) return
      const failedIndex = messages.findIndex((msg) => msg.id === failedMessage.id)
      if (failedIndex < 0) return
      const sourceUser = [...messages.slice(0, failedIndex)]
        .reverse()
        .find((msg) => msg.role === "user")
      if (!sourceUser || sourceUser.id.startsWith("temp-")) return
      setSending(true)
      const activityAt = new Date().toISOString()
      const tempAssistantId = `temp-assistant-retry-${Date.now()}`
      const retryAttachments = toEditableAttachments(sourceUser.attachments)
      setMessages((prev) => {
        const userIndex = prev.findIndex((msg) => msg.id === sourceUser.id)
        if (userIndex < 0) return prev
        return [
          ...prev.slice(0, userIndex + 1),
          {
            id: tempAssistantId,
            role: "assistant",
            content: "",
            created_at: activityAt,
            model_id: selectedModel ?? null,
            generation_status: "queued",
          } as ChatMessage,
        ]
      })
      try {
        const { promise } = chatApi.editMessageStream(
          chatId,
          sourceUser.id,
          sourceUser.content,
          selectedModel ?? modelStore.get() ?? undefined,
          retryAttachments
            .filter((item) => item.upload_id)
            .map((item) => ({
              upload_id: item.upload_id,
              file_name: item.file_name,
              content_type: item.content_type,
            })),
          undefined,
          undefined,
          undefined,
          locale,
          (event) => {
            if ("delta" in event && event.delta) {
              setMessages((prev) => {
                const next = [...prev]
                for (let i = next.length - 1; i >= 0; i -= 1) {
                  if (next[i].role === "assistant") {
                    next[i] = { ...next[i], content: `${next[i].content}${event.delta}` }
                    break
                  }
                }
                return next
              })
            }
            if ("done" in event && event.done) {
              void reloadMessages(chatId)
            }
          }
        )
        await promise
      } catch (err) {
        setBootError(err instanceof Error ? err.message : t("common_error"))
        void reloadMessages(chatId)
      } finally {
        setSending(false)
      }
    },
    [chatId, locale, messages, reloadMessages, selectedModel, sending, t]
  )

  const shareMessage = useCallback(
    async (msg: ChatMessage) => {
      if (!chatId || msg.id.startsWith("temp-")) return
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
    [chatId, t]
  )

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
        [...nonStepThinking].reverse().find((label) => Boolean(label)) ?? null
      const isThinking =
        msg.role === "assistant" &&
        (msg.generation_status === "queued" ||
          msg.generation_status === "running" ||
          msg.generation_status === "streaming")
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
          actionsEnabled
          isEditing={isEditing}
          isEditDragActive={false}
          editingAttachments={isEditing ? editingAttachments : []}
          editAttachmentError={null}
          codeTheme={codeTheme}
          t={t}
          exportQuestion={exportQuestion}
          onStartEdit={startEditMessage}
          onDeleteFromMessage={deleteFromMessage}
          onRetryMessage={(failed) => void retryFailedMessage(failed)}
          onShareMessage={(shared) => void shareMessage(shared)}
          onSaveEditedMessage={(edited, content) => void saveEditedMessage(edited, content)}
          onCancelEdit={cancelEditMessage}
          onEditPasteAttachments={noopClipboard}
          onEditFilesSelected={noopFiles}
          onEditDragEnter={noopDrag}
          onEditDragOver={noopDrag}
          onEditDragLeave={noopDrag}
          onEditDrop={noopDrag}
          onRemoveEditingAttachment={removeEditingAttachment}
          onPreviewAttachment={noopAttachment}
        />
      )
    },
    [
      actionInfoLevel,
      cancelEditMessage,
      codeTheme,
      deleteFromMessage,
      editingAttachments,
      editingMessageId,
      removeEditingAttachment,
      retryFailedMessage,
      saveEditedMessage,
      shareMessage,
      startEditMessage,
      t,
      visibleMessages,
    ]
  )

  const ignoreDrag = (event: DragEvent) => {
    event.preventDefault()
    event.stopPropagation()
  }

  const modelSelect = (
    <ChatModelSelect
      models={selectableChatModels}
      selectedModelId={selectedModel}
      open={modelMenuOpen}
      onOpenChange={setModelMenuOpen}
      onSelect={(modelId) => {
        setSelectedModel(modelId)
        modelStore.set(modelId)
      }}
      selectLabel={t("chat_select_model")}
      emptyLabel={t("chat_best_available_model")}
    />
  )

  if (!token) {
    return (
      <div className="flex min-h-screen items-center justify-center p-4">
        <div className="w-full max-w-sm space-y-3">
          <p className="text-center text-sm font-medium">{t("auth_sign_in")}</p>
          <Input
            value={org}
            onChange={(e) => setOrg(e.target.value)}
            placeholder={t("auth_org")}
            autoComplete="organization"
          />
          {loginError ? <p className="text-sm text-destructive">{loginError}</p> : null}
          <Button className="w-full" disabled={loginLoading} onClick={startSignIn}>
            {loginLoading ? t("auth_sign_in_loading") : t("auth_continue_sso")}
          </Button>
        </div>
      </div>
    )
  }

  if (!ready) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-3 p-6 text-center">
        <p className="text-sm text-muted-foreground">
          {bootError ?? t("common_loading")}
        </p>
        {bootError ? (
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              setBootError(null)
              void bootChat()
            }}
          >
            {t("common_try_again")}
          </Button>
        ) : null}
      </div>
    )
  }

  const showPpBanner = ppConnectStatus !== "connected" && ppConnectStatus !== "unknown"

  return (
    <div className="flex h-svh flex-col bg-background p-2">
      <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden rounded-card border border-border bg-background">
        <div className="grid h-13 shrink-0 grid-cols-[2.25rem_1fr_2.25rem] items-center gap-2 px-2">
        <Sheet open={sidebarOpen} onOpenChange={setSidebarOpen}>
          <SheetTrigger asChild>
            <Button variant="ghost" size="icon" aria-label={t("sidebar_toggle")}>
              <span
                aria-hidden="true"
                className="figma-icon size-4"
                style={{ maskImage: "url('/icon-panel.svg')" }}
              />
            </Button>
          </SheetTrigger>
          <SheetContent side="left" className="bg-sidebar w-57.25 p-2" showCloseButton={false}>
            <ChatSidebar
              title={t("chat_title")}
              labels={{
                newChat: t("chat_new"),
                history: t("chat_history"),
                projects: t("project_title"),
                prompts: t("prompt_library"),
                close: t("common_close"),
              }}
              activeChatId={chatId}
              onNewChat={() => void startNewChat()}
              onOpenHistory={() => {
                setSidebarOpen(false)
                openMainApp("/history")
              }}
              onOpenProjects={() => {
                setSidebarOpen(false)
                openMainApp("/projects")
              }}
              onSelectChat={(chat) => void selectChat(chat)}
              onRequestClose={() => setSidebarOpen(false)}
            />
          </SheetContent>
        </Sheet>
        <div className="flex justify-center">
          <Button
            size="sm"
            variant="ghost"
            disabled={!chatId}
            onClick={openFullPage}
            className="gap-1.5"
          >
            <ExternalLink className="size-3.5" aria-hidden="true" />
            {t("embed_open_full_page")}
          </Button>
        </div>
        <span aria-hidden="true" />
      </div>

      {showPpBanner ? (
        <div className="flex flex-wrap items-center gap-2 border-b px-3 py-2 text-sm">
          <span className="text-muted-foreground">
            {ppConnectStatus === "requesting"
              ? t("embed_pp_connect_requesting")
              : (ppConnectError ?? t("embed_pp_connect_needed"))}
          </span>
          {ppConnectStatus !== "requesting" ? (
            <Button size="sm" variant="outline" onClick={requestPpConnect}>
              {t("embed_pp_connect_retry")}
            </Button>
          ) : null}
        </div>
      ) : null}

      <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
        {!isEmptyChat ? (
          <MessageList
            messages={visibleMessages}
            onScroll={() => undefined}
            containerRef={messagesContainerRef}
            endRef={messagesEndRef}
            renderMessage={renderMessage}
          />
        ) : null}

        {bootError ? (
          <p className="relative z-10 px-4 text-center text-sm text-destructive">{bootError}</p>
        ) : null}

        <ChatComposer
          ref={composerRef}
          placeholder={t("chat_message_placeholder")}
          loading={sending}
          isDragActive={false}
          pendingAttachments={emptyAttachments}
          showModelSelect
          modelSelect={modelSelect}
          onSend={() => void send()}
          onStop={() => undefined}
          onFilesSelected={() => undefined}
          onRemoveAttachment={() => undefined}
          onPreviewAttachment={() => undefined}
          onPasteAttachments={() => undefined}
          onDragEnter={ignoreDrag}
          onDragOver={ignoreDrag}
          onDragLeave={ignoreDrag}
          onDrop={ignoreDrag}
          sendLabel={t("common_send")}
          stopLabel={t("common_stop")}
          welcomeTitle={t("embed_welcome_title")}
          centered={isEmptyChat}
          pinCentered={isEmptyChat}
            centeredLeading={
              isEmptyChat ? (
                <EldigenTreeRive className="aspect-[157/175] h-[min(32svh,20rem)] w-auto max-h-full" />
              ) : null
            }
            centeredExtras={null}
          />
      </div>
      </div>
      <Dialog
        open={Boolean(deleteConfirmMessage)}
        onOpenChange={(open) => {
          if (!open) setDeleteConfirmMessage(null)
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("chat_delete_message_confirm_title")}</DialogTitle>
            <DialogDescription>{t("chat_delete_message_confirm_desc")}</DialogDescription>
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
                void confirmDeleteFromMessage()
              }}
            >
              {t("chat_delete_message")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
