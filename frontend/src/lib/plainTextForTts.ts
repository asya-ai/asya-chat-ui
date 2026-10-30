const MD_LINK = /\[([^\]]+)\]\([^)]+\)/g
const MD_IMAGE = /!\[([^\]]*)\]\([^)]+\)/g
const MD_CODE_FENCE = /```[\s\S]*?```/g
const MD_INLINE_CODE = /`([^`]+)`/g
const MD_BOLD_ITALIC = /[*_~]{1,3}/g
const MD_HEADING = /^#{1,6}\s+/gm
const MD_LIST = /^[\s]*[-*+]\s+/gm
const MD_QUOTE = /^>\s?/gm
const WS = /[ \t]+\n/g
const MULTI_NL = /\n{3,}/g

export const MAX_TTS_CHARS = 4000

export const plainTextForTts = (value: string, maxChars = MAX_TTS_CHARS): string => {
  let text = (value || "").trim()
  if (!text) return ""
  text = text.replace(MD_CODE_FENCE, " ")
  text = text.replace(MD_IMAGE, "$1")
  text = text.replace(MD_LINK, "$1")
  text = text.replace(MD_INLINE_CODE, "$1")
  text = text.replace(MD_HEADING, "")
  text = text.replace(MD_LIST, "")
  text = text.replace(MD_QUOTE, "")
  text = text.replace(MD_BOLD_ITALIC, "")
  text = text.replace(WS, "\n")
  text = text.replace(MULTI_NL, "\n\n")
  text = text.split(/\s+/).join(" ")
  if (maxChars > 0 && text.length > maxChars) {
    text = `${text.slice(0, maxChars - 1).trimEnd()}…`
  }
  return text
}
