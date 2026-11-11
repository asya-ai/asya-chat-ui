/** Rewrite bare chat-attachment filenames in Marp markdown to fetchable URLs. */

import type { AgentSource, ChatMessage } from "@/lib/types"

export type CoworkImageUrlMap = Record<string, string>

const BARE_FILE_RE = /^[^/?#]+$/
const IMAGE_FILE_RE = /\.(png|jpe?g|gif|webp|svg|bmp|ico)$/i

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

type AttachmentUrlParts = {
  file_name?: string | null
  content_type?: string | null
  content_url?: string | null
  preview_url?: string | null
  data_base64?: string | null
  id?: string | null
}

const isImageAttachment = (attachment: AttachmentUrlParts) => {
  const type = (attachment.content_type ?? "").toLowerCase()
  if (type.startsWith("image/")) return true
  if (type && !type.startsWith("image/")) return false
  return IMAGE_FILE_RE.test(attachment.file_name ?? "")
}

export const attachmentImageUrl = (attachment: AttachmentUrlParts): string | undefined => {
  if (attachment.preview_url?.trim()) return attachment.preview_url.trim()
  if (attachment.data_base64?.trim() && attachment.file_name) {
    const ctype = attachment.content_type || "image/png"
    return `data:${ctype};base64,${attachment.data_base64}`
  }
  const url = attachment.content_url?.trim()
  return url || undefined
}

const registerImageUrl = (
  map: CoworkImageUrlMap,
  attachment: AttachmentUrlParts,
  url: string
) => {
  const fileName = attachment.file_name?.trim()
  if (fileName) {
    map[fileName] = url
    map[basename(fileName)] = url
  }
  const id = attachment.id?.trim()
  if (id) map[id] = url
}

export const mergeAttachmentListIntoImageUrlMap = (
  map: CoworkImageUrlMap,
  attachments: AttachmentUrlParts[] | null | undefined
): CoworkImageUrlMap => {
  if (!attachments?.length) return map
  const next = { ...map }
  for (const attachment of attachments) {
    if (!isImageAttachment(attachment)) continue
    const url = attachmentImageUrl(attachment)
    if (!url) continue
    registerImageUrl(next, attachment, url)
  }
  return next
}

export const looksLikeFetchableImageUrl = (src: string): boolean => {
  const trimmed = src.trim()
  if (!trimmed) return false
  if (trimmed.startsWith("data:image/")) return true
  if (trimmed.startsWith("blob:")) return true
  if (trimmed.includes("/attachments/") && trimmed.includes("/content")) return true
  if (/^https?:\/\//i.test(trimmed)) return true
  return false
}

export const resolveMarkdownImageSrc = (
  src: string | undefined,
  imageUrls: CoworkImageUrlMap | null | undefined,
  options?: { fallbackUrl?: string | null }
): string | undefined => {
  if (!src?.trim()) return src
  const trimmed = src.trim()
  if (looksLikeFetchableImageUrl(trimmed)) return trimmed
  const key = basename(trimmed)
  const resolved =
    imageUrls?.[trimmed] ||
    imageUrls?.[key] ||
    (BARE_FILE_RE.test(trimmed) ? imageUrls?.[trimmed] : undefined)
  if (resolved) return resolved
  const fallback = options?.fallbackUrl?.trim()
  if (!fallback) return trimmed
  // Models often use ![caption](caption) or a wrong bare path while the real file
  // is already on the message — use the latest generated attachment when unresolved.
  const captionAsUrl =
    /[^\x00-\x7f]/.test(trimmed) ||
    trimmed.includes(" ") ||
    !IMAGE_FILE_RE.test(trimmed)
  if (captionAsUrl || BARE_FILE_RE.test(trimmed)) return fallback
  return trimmed
}

export const collectChatImageUrlMap = (
  messages: ChatMessage[] | null | undefined
): CoworkImageUrlMap => {
  const map: CoworkImageUrlMap = {}
  if (!messages?.length) return map

  const add = (attachment: AttachmentUrlParts) => {
    if (!isImageAttachment(attachment)) return
    const url = attachmentImageUrl(attachment)
    if (!url) return
    registerImageUrl(map, attachment, url)
  }

  for (const msg of messages) {
    for (const att of msg.attachments || []) {
      const attId = "id" in att ? att.id : undefined
      add({
        file_name: att.file_name,
        content_type: att.content_type,
        content_url: att.content_url,
        data_base64: att.data_base64,
        preview_url: "preview_url" in att ? att.preview_url : undefined,
        id: typeof attId === "string" ? attId : undefined,
      })
    }

    const considerToolAttachments = (rawAttachments: unknown) => {
      if (!Array.isArray(rawAttachments)) return
      for (const item of rawAttachments) {
        if (!item || typeof item !== "object") continue
        const att = item as AttachmentUrlParts & { preview_url?: string }
        add(att)
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
          add({
            file_name: att.file_name,
            content_type: att.content_type,
            content_url: att.content_url,
            data_base64: att.data_base64,
            preview_url: "preview_url" in att ? att.preview_url : undefined,
            id: "id" in att && typeof att.id === "string" ? att.id : undefined,
          })
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

export const collectProjectImageUrlMap = (
  sources: AgentSource[] | null | undefined
): CoworkImageUrlMap => {
  const map: CoworkImageUrlMap = {}
  if (!sources?.length) return map
  for (const source of sources) {
    if (!source.is_image && !source.content_type?.startsWith("image/")) continue
    const url = source.content_url
    if (!url) continue
    if (source.file_name) {
      map[source.file_name] = url
      map[basename(source.file_name)] = url
    }
    map[source.id] = url
  }
  return map
}

export const resolveCoworkPresentationMarkdown = (
  markdown: string,
  imageUrls: CoworkImageUrlMap | null | undefined,
  options?: { fallbackUrl?: string | null }
): string => {
  if (!markdown) return markdown
  const hasMap = Boolean(imageUrls && Object.keys(imageUrls).length > 0)
  if (!hasMap && !options?.soleFallbackUrl) return markdown

  let next = markdown.replace(
    /(!\[[^\]]*\]\()([^)\s]+)(\))/g,
    (full, prefix: string, src: string, suffix: string) => {
      const resolved = resolveMarkdownImageSrc(src, imageUrls, options)
      if (!resolved || resolved === src) return full
      return `${prefix}${resolved}${suffix}`
    }
  )
  next = next.replace(
    /<img([^>]*?)\ssrc=["']([^"']+)["']([^>]*)>/gi,
    (full, before: string, src: string, after: string) => {
      const resolved = resolveMarkdownImageSrc(src, imageUrls, options)
      if (!resolved || resolved === src) return full
      return `<img${before} src="${resolved}"${after}>`
    }
  )
  return next
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
