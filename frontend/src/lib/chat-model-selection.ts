import type { ChatModel } from "@/lib/types"

const EMBEDDING_MODEL_RE =
  /(^|[\s/_-])(embedding|embeddings|text-embedding|embed)([\s/_-]|$)/

export function isEmbeddingChatModel(model: ChatModel): boolean {
  const name = `${model.display_name} ${model.model_name}`.toLowerCase()
  return EMBEDDING_MODEL_RE.test(name)
}

export function selectableChatModelsFrom(models: ChatModel[]): ChatModel[] {
  return models.filter((model) => !isEmbeddingChatModel(model))
}

/** Pick a sendable model id from the chat picker pool (matches ChatPage behaviour). */
export function resolveChatModelSelection(
  selectableChatModels: ChatModel[],
  selectedModel: string | undefined,
  storedModelId: string | null | undefined
): string | undefined {
  if (selectableChatModels.length === 0) return selectedModel
  const availableModels = selectableChatModels.filter(
    (model) => model.is_available !== false
  )
  const pool = availableModels.length > 0 ? availableModels : selectableChatModels
  if (selectedModel && pool.some((model) => model.id === selectedModel)) {
    return selectedModel
  }
  if (storedModelId && pool.some((model) => model.id === storedModelId)) {
    return storedModelId
  }
  return pool[0]?.id
}