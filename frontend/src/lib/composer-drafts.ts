import { useSyncExternalStore } from "react"

type DraftState = Record<string, string>

const STORAGE_KEY = "chatui_composer_drafts"

const readPersisted = (): DraftState => {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw) as unknown
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {}
    const next: DraftState = {}
    for (const [chatId, value] of Object.entries(parsed as Record<string, unknown>)) {
      if (typeof value === "string" && value.length > 0) next[chatId] = value
    }
    return next
  } catch {
    return {}
  }
}

const persist = (next: DraftState) => {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
  } catch {
    // ignore quota / private mode failures
  }
}

let state: DraftState = typeof window !== "undefined" ? readPersisted() : {}
const listeners = new Set<() => void>()

const emit = () => {
  for (const listener of listeners) listener()
}

const setState = (next: DraftState) => {
  state = next
  persist(state)
  emit()
}

export const draftTitleFromText = (text: string, maxLen = 60): string | null => {
  const line = text
    .split("\n")
    .map((part) => part.trim())
    .find(Boolean)
  if (!line) return null
  if (line.length <= maxLen) return line
  return `${line.slice(0, Math.max(1, maxLen - 1)).trimEnd()}…`
}

export const composerDraftStore = {
  getSnapshot: (): DraftState => state,
  subscribe: (listener: () => void) => {
    listeners.add(listener)
    return () => {
      listeners.delete(listener)
    }
  },
  get: (chatId: string): string => state[chatId] ?? "",
  set: (chatId: string, text: string) => {
    const trimmedStore = text
    if (!trimmedStore) {
      if (!(chatId in state)) return
      const { [chatId]: _removed, ...rest } = state
      setState(rest)
      return
    }
    if (state[chatId] === trimmedStore) return
    setState({ ...state, [chatId]: trimmedStore })
  },
  clear: (chatId: string) => {
    if (!(chatId in state)) return
    const { [chatId]: _removed, ...rest } = state
    setState(rest)
  },
}

export const useComposerDrafts = (): DraftState =>
  useSyncExternalStore(
    composerDraftStore.subscribe,
    composerDraftStore.getSnapshot,
    () => ({})
  )
