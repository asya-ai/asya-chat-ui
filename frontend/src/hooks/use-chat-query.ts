import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { chatApi, modelApi, orgApi } from "@/lib/api"
import type { Chat, ChatMessage } from "@/lib/types"

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

export const useChatMessages = (chatId: string | null) =>
  useQuery({
    queryKey: chatId ? chatKeys.messages(chatId) : ["chatMessages"],
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

export const useCreateChat = (orgId: string | null) => {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: { model_id?: string; title?: string }) => {
      if (!orgId) {
        throw new Error("Missing org id")
      }
      return chatApi.create({ org_id: orgId, ...payload })
    },
    onSuccess: (chat) => {
      if (!orgId) return
      queryClient.setQueryData<Chat[]>(chatKeys.list(orgId), (prev) =>
        prev ? [chat, ...prev] : [chat]
      )
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
