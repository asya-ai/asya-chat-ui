import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query"

import { agentApi, authApi, chatApi, modelApi, orgApi, promptApi } from "@/lib/api"
import { useAuth } from "@/lib/auth-context"
import type { Agent, Chat, ChatMessage, ChatModel, Org, Prompt } from "@/lib/types"

const chatKeys = {
  all: ["chats"] as const,
  list: (orgId: string) => [...chatKeys.all, orgId] as const,
  messages: (chatId: string) => ["chatMessages", chatId] as const,
}

const modelKeys = {
  all: ["models"] as const,
  list: (orgId: string) => [...modelKeys.all, orgId] as const,
}

const orgKeys = {
  mine: ["orgs", "mine"] as const,
}

const meKeys = {
  me: ["auth", "me"] as const,
}

const agentKeys = {
  all: ["agents"] as const,
}

const promptKeys = {
  all: ["prompts"] as const,
  list: (contextAgentId: string | null) => [...promptKeys.all, contextAgentId ?? "none"] as const,
}

const upsertPromptInContextCache = (
  queryClient: QueryClient,
  contextAgentId: string | null,
  prompt: Prompt
) => {
  queryClient.setQueryData<Prompt[]>(promptKeys.list(contextAgentId), (prev) => {
    const matchesContext =
      !prompt.agent_id ||
      (contextAgentId != null && prompt.agent_id === contextAgentId)
    const without = (prev ?? []).filter((item) => item.id !== prompt.id)
    return matchesContext ? [prompt, ...without] : without
  })
}

export const patchModelInCache = (queryClient: QueryClient, updated: ChatModel) => {
  queryClient.setQueriesData<ChatModel[]>({ queryKey: modelKeys.all }, (prev) =>
    prev?.map((model) => (model.id === updated.id ? { ...model, ...updated } : model)) ?? prev
  )
}

export const invalidateModelCaches = (queryClient: QueryClient) => {
  void queryClient.invalidateQueries({ queryKey: modelKeys.all })
}

export const patchOrgInMineCache = (queryClient: QueryClient, updated: Org) => {
  queryClient.setQueryData<Org[]>(orgKeys.mine, (prev) =>
    prev?.map((org) => (org.id === updated.id ? { ...org, ...updated } : org)) ?? prev
  )
}

export const removeOrgFromMineCache = (queryClient: QueryClient, orgId: string) => {
  queryClient.setQueryData<Org[]>(orgKeys.mine, (prev) =>
    prev?.filter((org) => org.id !== orgId) ?? prev
  )
}

export const invalidateOrgsMine = (queryClient: QueryClient) => {
  void queryClient.invalidateQueries({ queryKey: orgKeys.mine })
}

export const useMe = () => {
  const { token } = useAuth()
  return useQuery({
    queryKey: [...meKeys.me, token],
    queryFn: () => authApi.me(),
    enabled: Boolean(token),
    staleTime: 60_000,
  })
}

export const useOrgsMine = () =>
  useQuery({
    queryKey: orgKeys.mine,
    queryFn: () => orgApi.mine(),
    staleTime: 60_000,
  })

export const useChats = (orgId: string | null) =>
  useQuery({
    queryKey: orgId ? chatKeys.list(orgId) : chatKeys.all,
    queryFn: () => {
      if (!orgId) return []
      return chatApi.list(orgId)
    },
    enabled: Boolean(orgId),
    staleTime: 15_000,
  })

export const useChatSearch = (orgId: string | null, query: string) =>
  useQuery({
    queryKey: orgId ? [...chatKeys.list(orgId), "search", query] : [...chatKeys.all, "search", query],
    queryFn: () => {
      if (!query.trim()) return []
      return chatApi.search(query.trim())
    },
    enabled: Boolean(query.trim()),
    staleTime: 10_000,
  })

export const useChatMessages = (chatId: string | null) =>
  useQuery({
    // Keep the key chat-scoped even when disabled so a bare ["chatMessages"]
    // prefix never collides with per-chat caches during /chat ↔ /chat/:id switches.
    queryKey: chatId ? chatKeys.messages(chatId) : ["chatMessages", "none"],
    queryFn: () => {
      if (!chatId) return []
      return chatApi.messages(chatId)
    },
    enabled: Boolean(chatId),
    staleTime: 10_000,
  })

export const useModels = (orgId: string | null) =>
  useQuery({
    queryKey: orgId ? modelKeys.list(orgId) : modelKeys.all,
    queryFn: () => {
      if (!orgId) return []
      return modelApi.list(orgId)
    },
    enabled: Boolean(orgId),
    staleTime: 30_000,
  })

export const useAgents = (orgId: string | null) =>
  useQuery({
    queryKey: agentKeys.all,
    queryFn: () => agentApi.list(),
    enabled: Boolean(orgId),
    staleTime: 15_000,
  })

export const useCreateAgent = (_orgId: string | null) => {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: {
      name: string
      description?: string | null
      preferred_model_id?: string | null
      master_prompt?: string | null
    }) => agentApi.create(payload),
    onSuccess: (created) => {
      queryClient.setQueryData<Agent[]>(agentKeys.all, (prev) =>
        prev ? [created, ...prev] : [created]
      )
    },
  })
}

export const useUpdateAgent = (_orgId: string | null) => {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      agentId,
      payload,
    }: {
      agentId: string
      payload: Parameters<typeof agentApi.update>[1]
    }) => agentApi.update(agentId, payload),
    onSuccess: (updated) => {
      queryClient.setQueryData<Agent[]>(agentKeys.all, (prev) =>
        prev
          ? prev.map((agent) => (agent.id === updated.id ? { ...agent, ...updated } : agent))
          : prev
      )
    },
  })
}

export const useDeleteAgent = (_orgId: string | null) => {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (agentId: string) => agentApi.remove(agentId),
    onSuccess: (_result, agentId) => {
      queryClient.setQueryData<Agent[]>(agentKeys.all, (prev) =>
        prev ? prev.filter((agent) => agent.id !== agentId) : prev
      )
    },
  })
}

export const usePrompts = (contextAgentId: string | null) =>
  useQuery({
    queryKey: promptKeys.list(contextAgentId),
    queryFn: () => promptApi.list({ context_agent_id: contextAgentId }),
    staleTime: 15_000,
  })

export const useSavePrompt = (contextAgentId: string | null) => {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      promptId,
      payload,
      clearAgent,
    }: {
      promptId?: string
      payload: Parameters<typeof promptApi.create>[0]
      clearAgent?: boolean
    }) =>
      promptId
        ? promptApi.update(promptId, { ...payload, clear_agent: clearAgent })
        : promptApi.create(payload),
    onSuccess: (saved) => {
      upsertPromptInContextCache(queryClient, contextAgentId, saved)
    },
  })
}

export const useDeletePrompt = () => {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (promptId: string) => promptApi.remove(promptId),
    onSuccess: (_result, promptId) => {
      queryClient.setQueriesData<Prompt[]>({ queryKey: promptKeys.all }, (prev) =>
        prev?.filter((prompt) => prompt.id !== promptId) ?? prev
      )
    },
  })
}

export const useCreateChat = (orgId: string | null) => {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: {
      model_id?: string
      title?: string
      agent_id?: string
      is_incognito?: boolean
    }) => {
      if (!orgId) {
        throw new Error("Missing org id")
      }
      return chatApi.create({ org_id: orgId, ...payload })
    },
    onSuccess: (chat) => {
      if (!orgId) return
      if (!chat.is_incognito) {
        queryClient.setQueryData<Chat[]>(chatKeys.list(orgId), (prev) =>
          prev ? [chat, ...prev] : [chat]
        )
      }
    },
  })
}

export const useDeleteChat = (orgId: string | null) => {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (chatId: string) => chatApi.deleteChat(chatId),
    onMutate: async (chatId) => {
      if (!orgId) return
      await queryClient.cancelQueries({ queryKey: chatKeys.list(orgId) })
      const previous = queryClient.getQueryData<Chat[]>(chatKeys.list(orgId))
      queryClient.setQueryData<Chat[]>(chatKeys.list(orgId), (prev) =>
        prev ? prev.filter((chat) => chat.id !== chatId) : prev
      )
      return { previous }
    },
    onError: (_err, _chatId, context) => {
      if (!orgId || !context?.previous) return
      queryClient.setQueryData(chatKeys.list(orgId), context.previous)
    },
    onSuccess: () => {
      if (!orgId) return
      queryClient.invalidateQueries({ queryKey: [...chatKeys.list(orgId), "search"] })
    },
  })
}

export const useRenameChat = (orgId: string | null) => {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ chatId, title }: { chatId: string; title: string }) =>
      chatApi.update(chatId, { title }),
    onSuccess: (updated) => {
      if (!orgId) return
      queryClient.setQueryData<Chat[]>(chatKeys.list(orgId), (prev) =>
        prev
          ? prev.map((chat) => (chat.id === updated.id ? { ...chat, ...updated } : chat))
          : prev
      )
      queryClient.invalidateQueries({ queryKey: [...chatKeys.list(orgId), "search"] })
    },
  })
}

export const usePinChat = (orgId: string | null) => {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ chatId, is_pinned }: { chatId: string; is_pinned: boolean }) =>
      chatApi.update(chatId, { is_pinned }),
    onSuccess: (updated) => {
      if (!orgId) return
      queryClient.setQueryData<Chat[]>(chatKeys.list(orgId), (prev) =>
        prev
          ? prev.map((chat) => (chat.id === updated.id ? { ...chat, ...updated } : chat))
          : prev
      )
      queryClient.invalidateQueries({ queryKey: [...chatKeys.list(orgId), "search"] })
    },
  })
}

export const useUpdateChatMessages = (chatId: string | null) => {
  const queryClient = useQueryClient()
  return (updater: (prev: ChatMessage[]) => ChatMessage[]) => {
    if (!chatId) return
    queryClient.setQueryData<ChatMessage[]>(
      chatKeys.messages(chatId),
      (prev) => updater(prev ?? [])
    )
  }
}

export const useReplaceChatMessages = (chatId: string | null) => {
  const queryClient = useQueryClient()
  return (messages: ChatMessage[]) => {
    if (!chatId) return
    queryClient.setQueryData(chatKeys.messages(chatId), messages)
  }
}
