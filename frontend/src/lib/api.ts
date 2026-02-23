import { orgStore, tokenStore } from "@/lib/storage"
import type {
  Chat,
  ChatMessage,
  ChatModel,
  ChatMessageAttachmentInput,
  Invite,
  ModelSuggestionProvider,
  Org,
  OrgMember,
  OrgWebSettings,
  SourceItem,
  ToolEvent,
  ProviderConfig,
  ProviderConfigUpdate,
  UsageSlice,
} from "@/lib/types"

const API_BASE = import.meta.env.VITE_API_URL || "/api"

type RequestOptions = RequestInit & { skipAuth?: boolean }

type StreamEvent =
  | { delta: string }
  | { user_message_id: string; edited_message_id?: string }
  | { activity: { label: string; state: "start" | "end" } }
  | { tool_event: ToolEvent }
  | { error: string; status?: number }
  | {
      done: true
      message_id?: string
      content?: string
      model_name?: string
      model_id?: string
      attachments?: ChatMessageAttachmentInput[]
      sources?: SourceItem[]
    }

const getWsBase = () => {
  if (API_BASE.startsWith("http")) {
    return API_BASE.replace(/^http/, "ws")
  }
  const protocol = window.location.protocol === "https:" ? "wss" : "ws"
  return `${protocol}://${window.location.host}${API_BASE}`
}

const apiWebSocket = (
  path: string,
  payload: Record<string, unknown>,
  onEvent: (event: StreamEvent) => void,
  messageType: "send" | "edit" = "send"
) => {
  let ws: WebSocket | null = null
  let cancelled = false
  const promise = new Promise<void>((resolve, reject) => {
    const token = tokenStore.get()
    const wsBase = getWsBase()
    const protocols = token ? ["chatui", `token.${token}`] : ["chatui"]
    const socket = new WebSocket(`${wsBase}${path}`, protocols)
    ws = socket
    let settled = false
    socket.onopen = () => {
      socket.send(JSON.stringify({ type: messageType, payload }))
    }
    socket.onmessage = (event) => {
      try {
        const parsed = JSON.parse(event.data) as StreamEvent
        onEvent(parsed)
        if ("error" in parsed) {
          settled = true
          socket.close()
          reject(new Error(parsed.error))
          return
        }
        if ("done" in parsed && parsed.done) {
          settled = true
          socket.close()
          resolve()
        }
      } catch {
        // ignore invalid chunks
      }
    }
    socket.onerror = () => {
      if (settled) return
      settled = true
      if (cancelled) {
        resolve()
      } else {
        reject(new Error("WebSocket error"))
      }
    }
    socket.onclose = (event) => {
      if (event.code === 4401 || event.code === 4403) {
        tokenStore.clear()
        orgStore.clear()
        window.location.href = "/login"
        return
      }
      if (!settled) {
        settled = true
        if (cancelled) {
          resolve()
        } else {
          reject(new Error("WebSocket closed"))
        }
      }
    }
  })
  const cancel = () => {
    cancelled = true
    try {
      ws?.close(1000, "client_stop")
    } catch {
      // ignore close errors
    }
  }
  return { promise, cancel }
}

const apiFetch = async <T>(path: string, options: RequestOptions = {}): Promise<T> => {
  const headers = new Headers(options.headers)
  if (!options.skipAuth) {
    const token = tokenStore.get()
    if (token) {
      headers.set("Authorization", `Bearer ${token}`)
    }
  }
  if (!(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json")
  }
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
  })
  const refreshedToken = response.headers.get("x-access-token")
  if (refreshedToken) {
    tokenStore.set(refreshedToken)
  }
  if (!response.ok) {
    if (response.status === 401 && !options.skipAuth) {
      tokenStore.clear()
      orgStore.clear()
      window.location.href = "/login"
    }
    const contentType = response.headers.get("content-type") || ""
    let message = "Request failed"
    if (contentType.includes("application/json")) {
      try {
        const data = (await response.json()) as
          | { detail?: unknown; message?: unknown; error?: unknown }
          | unknown
        if (typeof data === "string") {
          message = data
        } else if (data && typeof data === "object") {
          const detail = (data as { detail?: unknown }).detail
          if (typeof detail === "string") {
            message = detail
          } else if (Array.isArray(detail)) {
            message = detail
              .map((item) =>
                typeof item === "string"
                  ? item
                  : typeof item?.msg === "string"
                    ? item.msg
                    : null
              )
              .filter(Boolean)
              .join("\n")
          } else if (typeof (data as { message?: unknown }).message === "string") {
            message = (data as { message?: string }).message as string
          } else if (typeof (data as { error?: unknown }).error === "string") {
            message = (data as { error?: string }).error as string
          }
        }
      } catch {
        // fall back to text
      }
    }
    if (message === "Request failed") {
      try {
        const text = await response.text()
        if (text) {
          try {
            const parsed = JSON.parse(text) as { detail?: unknown; message?: unknown }
            if (typeof parsed?.detail === "string") {
              message = parsed.detail
            } else if (typeof parsed?.message === "string") {
              message = parsed.message
            } else {
              message = text
            }
          } catch {
            message = text
          }
        }
      } catch {
        // ignore
      }
    }
    throw new Error(message)
  }
  if (response.status === 204) {
    return {} as T
  }
  return response.json() as Promise<T>
}

export const authApi = {
  register: (email: string, password: string) =>
    apiFetch<{ access_token: string }>("/auth/register", {
      method: "POST",
      skipAuth: true,
      body: JSON.stringify({ email, password }),
    }),
  login: (email: string, password: string) =>
    apiFetch<{ access_token: string }>("/auth/login", {
      method: "POST",
      skipAuth: true,
      body: JSON.stringify({ email, password }),
    }),
  acceptInvite: (token: string, password?: string) =>
    apiFetch<{ access_token: string }>("/auth/invites/accept", {
      method: "POST",
      skipAuth: true,
      body: JSON.stringify({ token, password }),
    }),
  invitePreview: (token: string) =>
    apiFetch<{ email: string; org_name?: string | null; expires_at: string }>(
      `/auth/invites/preview?token=${encodeURIComponent(token)}`,
      { skipAuth: true }
    ),
  requestPasswordReset: (email: string) =>
    apiFetch("/auth/password-reset", {
      method: "POST",
      skipAuth: true,
      body: JSON.stringify({ email }),
    }),
  confirmPasswordReset: (token: string, newPassword: string) =>
    apiFetch("/auth/password-reset/confirm", {
      method: "POST",
      skipAuth: true,
      body: JSON.stringify({ token, new_password: newPassword }),
    }),
  createInvite: (orgId: string, email: string) =>
    apiFetch("/auth/invites", {
      method: "POST",
      body: JSON.stringify({ org_id: orgId, email }),
    }),
  me: () =>
    apiFetch<{ id: string; email: string; is_super_admin: boolean; is_admin: boolean }>(
      "/auth/me"
    ),
  changePassword: (currentPassword: string, newPassword: string) =>
    apiFetch("/auth/me/password", {
      method: "PATCH",
      body: JSON.stringify({
        current_password: currentPassword,
        new_password: newPassword,
      }),
    }),
  updateSuperAdmin: (userId: string, isSuperAdmin: boolean) =>
    apiFetch<{ id: string; email: string; is_super_admin: boolean; is_admin: boolean }>(
      `/auth/users/${userId}/super-admin`,
      { method: "PATCH", body: JSON.stringify({ is_super_admin: isSuperAdmin }) }
    ),
  registrationEnabled: () => apiFetch<{ enabled: boolean }>("/auth/registration-enabled", { skipAuth: true }),
  invites: (orgId: string) => apiFetch<Invite[]>(`/auth/invites?org_id=${orgId}`),
  resendInvite: (inviteId: string) => apiFetch<Invite>(`/auth/invites/${inviteId}/resend`, { method: "POST" }),
  cancelInvite: (inviteId: string) => apiFetch(`/auth/invites/${inviteId}`, { method: "DELETE" }),
}

export const orgApi = {
  list: () => apiFetch<Org[]>("/orgs"),
  mine: () => apiFetch<Org[]>("/orgs/mine"),
  create: (name: string) =>
    apiFetch<Org>("/orgs", { method: "POST", body: JSON.stringify({ name }) }),
  members: (orgId: string) => apiFetch<OrgMember[]>(`/orgs/${orgId}/members`),
  updateMemberRole: (orgId: string, userId: string, role: string) =>
    apiFetch<OrgMember>(`/orgs/${orgId}/members/${userId}`, {
      method: "PATCH",
      body: JSON.stringify({ role }),
    }),
  providers: (orgId: string) => apiFetch<ProviderConfig[]>(`/orgs/${orgId}/providers`),
  updateProviders: (orgId: string, payload: ProviderConfigUpdate[]) =>
    apiFetch<ProviderConfig[]>(`/orgs/${orgId}/providers`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  update: (orgId: string, payload: { name?: string; is_active?: boolean; is_frozen?: boolean }) =>
    apiFetch<Org>(`/orgs/${orgId}`, { method: "PATCH", body: JSON.stringify(payload) }),
  remove: (orgId: string) => apiFetch(`/orgs/${orgId}`, { method: "DELETE" }),
  webSettings: (orgId: string) => apiFetch<OrgWebSettings>(`/orgs/${orgId}/web-settings`),
  updateWebSettings: (orgId: string, payload: Partial<OrgWebSettings>) =>
    apiFetch<OrgWebSettings>(`/orgs/${orgId}/web-settings`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
}

export const usageApi = {
  summary: (orgId: string | null, groupBy: string, month?: string) => {
    const params = new URLSearchParams()
    if (orgId) params.set("org_id", orgId)
    if (groupBy) params.set("group_by", groupBy)
    if (month) params.set("month", month)
    const query = params.toString()
    return apiFetch<UsageSlice[]>(`/usage${query ? `?${query}` : ""}`)
  },
  months: (orgId: string | null) => {
    const params = new URLSearchParams()
    if (orgId) params.set("org_id", orgId)
    const query = params.toString()
    return apiFetch<string[]>(`/usage/months${query ? `?${query}` : ""}`)
  },
}

export const modelApi = {
  list: (orgId?: string) =>
    apiFetch<ChatModel[]>(orgId ? `/models?org_id=${orgId}` : "/models"),
  create: (payload: {
    org_id: string
    provider: string
    model_name: string
    display_name: string
    is_active?: boolean
    context_length?: number | null
    supports_image_input?: boolean | null
    supports_image_output?: boolean | null
    reasoning_effort?: string | null
  }) => apiFetch<ChatModel>("/models", { method: "POST", body: JSON.stringify(payload) }),
  suggestions: () => apiFetch<ModelSuggestionProvider[]>("/models/suggestions"),
  remove: (modelId: string) =>
    apiFetch(`/models/${modelId}`, { method: "DELETE" }),
  rename: (modelId: string, displayName: string) =>
    apiFetch<ChatModel>(`/models/${modelId}`, {
      method: "PATCH",
      body: JSON.stringify({ display_name: displayName }),
    }),
  update: (modelId: string, payload: { reasoning_effort?: string | null }) =>
    apiFetch<ChatModel>(`/models/${modelId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  updateOrder: (payload: { model_id: string; display_order: number }[]) =>
    apiFetch<ChatModel[]>("/models/order", {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  setOrgModels: (
    orgId: string,
    payload: { model_id: string; is_enabled: boolean }[]
  ) => apiFetch<ChatModel[]>(`/models/orgs/${orgId}`, { method: "PUT", body: JSON.stringify(payload) }),
}

export const chatApi = {
  list: (orgId: string) => apiFetch<Chat[]>(`/chats?org_id=${orgId}`),
  create: (payload: { org_id: string; model_id?: string; title?: string }) =>
    apiFetch<Chat>("/chats", { method: "POST", body: JSON.stringify(payload) }),
  deleteChat: (chatId: string) =>
    apiFetch(`/chats/${chatId}`, { method: "DELETE" }),
  messages: (chatId: string) => apiFetch<ChatMessage[]>(`/chats/${chatId}/messages`),
  sendMessage: (
    chatId: string,
    content: string,
    model_id?: string,
    attachments?: ChatMessageAttachmentInput[],
    reasoning_effort?: string | null,
    locale?: string
  ) =>
    apiFetch<ChatMessage[]>(`/chats/${chatId}/messages`, {
      method: "POST",
      body: JSON.stringify({ content, model_id, attachments, reasoning_effort, locale }),
    }),
  sendMessageStream: (
    chatId: string,
    content: string,
    model_id: string | undefined,
    attachments: ChatMessageAttachmentInput[] | undefined,
    reasoning_effort: string | null | undefined,
    locale: string | undefined,
    onEvent: (event: StreamEvent) => void
  ) =>
    apiWebSocket(
      `/chats/${chatId}/ws`,
      { content, model_id, attachments, reasoning_effort, locale },
      onEvent
    ),
  editMessageStream: (
    chatId: string,
    messageId: string,
    content: string,
    attachments: ChatMessageAttachmentInput[] | null | undefined,
    locale: string | undefined,
    onEvent: (event: StreamEvent) => void
  ) =>
    apiWebSocket(
      `/chats/${chatId}/ws`,
      { message_id: messageId, content, attachments, locale },
      onEvent,
      "edit"
    ),
  editMessage: (
    chatId: string,
    messageId: string,
    content: string,
    attachments?: ChatMessageAttachmentInput[] | null,
    locale?: string
  ) =>
    apiFetch<{ user_message: ChatMessage; assistant_message: ChatMessage }>(
      `/chats/${chatId}/messages/${messageId}`,
      {
      method: "PATCH",
        body: JSON.stringify({ content, attachments, locale }),
      }
    ),
  deleteBranchFromMessage: (chatId: string, messageId: string) =>
    apiFetch(`/chats/${chatId}/messages/${messageId}/branch`, {
      method: "DELETE",
    }),
}

