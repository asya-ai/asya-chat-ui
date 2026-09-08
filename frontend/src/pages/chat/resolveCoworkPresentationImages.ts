/** Rewrite bare chat-attachment filenames in Marp markdown to fetchable URLs. */

import type { ChatMessage } from "@/lib/types"

export type CoworkImageUrlMap = Record<string, string>

const BARE_FILE_RE = /^[^/?#]+$/

const basename = (src: string) => {
  const cleaned = src.trim().replace(/^['"]|['"]$/g, "")
  try {
    const path = cleaned.includes("://")
      ? new URL(cleaned).pathname
      : cleaned.split("?")[0] || cleaned
    return decodeURIComponent(path.split("/").filter(Boolean).pop() || cleaned)
  } catch {
    return decodeURIComponent(cleaned.split(/[\\/]/).pop() || cleaned)
  }
}

export const collectChatImageUrlMap = (
  messages: ChatMessage[] | null | undefined
): CoworkImageUrlMap => {
  const map: CoworkImageUrlMap = {}
  if (!messages?.length) return map

  const add = (fileName?: string | null, url?: string | null, id?: string | null) => {
    if (!url) return
    if (fileName) {
      map[fileName] = url
      map[basename(fileName)] = url
    }
    if (id) map[id] = url
  }

  for (const msg of messages) {
    for (const att of msg.attachments || []) {
      if (att.content_type && !att.content_type.startsWith("image/")) continue
      const attId = "id" in att ? att.id : undefined
      add(att.file_name, att.content_url, attId)
      if (att.data_base64 && att.file_name && !att.content_url) {
        const ctype = att.content_type || "image/png"
        add(att.file_name, `data:${ctype};base64,${att.data_base64}`, attId)
      }
    }

    const considerToolAttachments = (rawAttachments: unknown) => {
      if (!Array.isArray(rawAttachments)) return
      for (const item of rawAttachments) {
        if (!item || typeof item !== "object") continue
        const att = item as {
          file_name?: string
          content_type?: string
          content_url?: string
          data_base64?: string
          id?: string
        }
        if (att.content_type && !att.content_type.startsWith("image/")) continue
        if (att.content_url) {
          add(att.file_name, att.content_url, att.id)
        } else if (att.data_base64 && att.file_name) {
          const ctype = att.content_type || "image/png"
          add(att.file_name, `data:${ctype};base64,${att.data_base64}`, att.id)
        }
      }
    }

    if (msg.tool_event && "output" in msg.tool_event && msg.tool_event.output) {
      considerToolAttachments(
        (msg.tool_event.output as { attachments?: unknown }).attachments
      )
    }

    for (const part of msg.stream_parts || []) {
      if (part.type !== "action") continue
      if (part.attachments?.length) {
        for (const att of part.attachments) {
          if (att.content_type && !att.content_type.startsWith("image/")) continue
          add(att.file_name, att.content_url, "id" in att ? att.id : undefined)
          if (att.data_base64 && att.file_name && !att.content_url) {
            const ctype = att.content_type || "image/png"
            add(att.file_name, `data:${ctype};base64,${att.data_base64}`, "id" in att ? att.id : undefined)
          }
        }
      }
      const rawAttachments =
        part.tool_event &&
        "output" in part.tool_event &&
        part.tool_event.output &&
        typeof part.tool_event.output === "object"
          ? (part.tool_event.output as { attachments?: unknown }).attachments
          : null
      considerToolAttachments(rawAttachments)
    }
  }
  return map
}

export const resolveCoworkPresentationMarkdown = (
  markdown: string,
  imageUrls: CoworkImageUrlMap | null | undefined
): string => {
  if (!markdown || !imageUrls || Object.keys(imageUrls).length === 0) return markdown

  return markdown.replace(
    /(!\[[^\]]*\]\()([^)\s]+)(\))/g,
    (full, prefix: string, src: string, suffix: string) => {
      const key = basename(src)
      const resolved =
        imageUrls[src] ||
        imageUrls[key] ||
        (BARE_FILE_RE.test(src.trim()) ? imageUrls[src.trim()] : undefined)
      if (!resolved || resolved === src) return full
      return `${prefix}${resolved}${suffix}`
    }
  )
}

export const rewriteMarpHtmlImageUrls = (
  html: string,
  imageUrls: CoworkImageUrlMap | null | undefined
): string => {
  if (!html || !imageUrls || Object.keys(imageUrls).length === 0) return html

  const resolveSrc = (src: string) => {
    const key = basename(src)
    return imageUrls[src] || imageUrls[key] || src
  }

  return html
    .replace(
      /\b(src|data-background-image)=("|&quot;)([^"&]+)\2/gi,
      (full, attr: string, quote: string, src: string) => {
        const resolved = resolveSrc(src.replace(/^url\((.*)\)$/i, "$1"))
        if (resolved === src) return full
        return `${attr}=${quote}${resolved}${quote}`
      }
    )
    .replace(/url\((['"]?)([^'")]+)\1\)/gi, (full, quote: string, src: string) => {
      const resolved = resolveSrc(src)
      if (resolved === src) return full
      const q = quote || '"'
      return `url(${q}${resolved}${q})`
    })
}
