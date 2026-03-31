import type { KeyboardEvent } from "react"

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
