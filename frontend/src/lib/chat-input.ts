import type { KeyboardEvent } from "react"

export type SlashPromptTrigger = {
  start: number
  query: string
}

export const getSlashPromptTrigger = (
  value: string,
  cursor: number
): SlashPromptTrigger | null => {
  const before = value.slice(0, cursor)
  const match = before.match(/(?:^|\s)\/([^\s]*)$/)
  if (!match) return null
  const query = match[1] ?? ""
  return { start: cursor - query.length - 1, query }
}

export const shouldSubmitOnEnter = (
  event: KeyboardEvent<HTMLTextAreaElement>
): boolean => {
  if (event.key !== "Enter") return false
  if (event.nativeEvent.isComposing) return false

  const isTouchLikeDevice =
    typeof window !== "undefined" &&
    window.matchMedia("(hover: none), (pointer: coarse)").matches

  if (isTouchLikeDevice) {
    return event.ctrlKey || event.metaKey
  }
  return !(event.shiftKey || event.metaKey)
}
