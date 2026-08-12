import { useSyncExternalStore } from "react"

export type ChatGenerationIndicator = "generating" | "ready"

type IndicatorState = Record<string, ChatGenerationIndicator>

const STORAGE_KEY = "chatui_generation_indicators"

const readPersisted = (): IndicatorState => {
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw) as unknown
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {}
    const next: IndicatorState = {}
    for (const [chatId, value] of Object.entries(parsed as Record<string, unknown>)) {
      if (value === "generating" || value === "ready") next[chatId] = value
    }
    return next
  } catch {
    return {}
  }
}

const persist = (next: IndicatorState) => {
  try {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(next))
  } catch {
    // ignore quota / private mode failures
  }
}

let state: IndicatorState = typeof window !== "undefined" ? readPersisted() : {}
const listeners = new Set<() => void>()

const emit = () => {
  for (const listener of listeners) listener()
}

const setState = (next: IndicatorState) => {
  state = next
  persist(state)
  emit()
}

export const chatGenerationIndicatorsStore = {
  getSnapshot: (): IndicatorState => state,
  subscribe: (listener: () => void) => {
    listeners.add(listener)
    return () => {
      listeners.delete(listener)
    }
  },
  generatingChatIds: (): string[] =>
    Object.entries(state)
      .filter(([, status]) => status === "generating")
      .map(([chatId]) => chatId),
  start: (chatId: string) => {
    if (state[chatId] === "generating") return
    setState({ ...state, [chatId]: "generating" })
  },
  /** Generation finished. Mark ready/unread when the user is not viewing that chat. */
  finish: (chatId: string, unread: boolean) => {
    if (unread) {
      if (state[chatId] === "ready") return
      setState({ ...state, [chatId]: "ready" })
      return
    }
    if (!(chatId in state)) return
    const { [chatId]: _removed, ...rest } = state
    setState(rest)
  },
  /** Clear indicator (viewed, cancelled, or failed while watching). */
  clear: (chatId: string) => {
    if (!(chatId in state)) return
    const { [chatId]: _removed, ...rest } = state
    setState(rest)
  },
  /** Clear only the unread/ready ring when opening a chat; keep generating. */
  markRead: (chatId: string) => {
    if (state[chatId] !== "ready") return
    const { [chatId]: _removed, ...rest } = state
    setState(rest)
  },
}

export const useChatGenerationIndicators = (): IndicatorState =>
  useSyncExternalStore(
    chatGenerationIndicatorsStore.subscribe,
    chatGenerationIndicatorsStore.getSnapshot,
    () => ({})
  )
