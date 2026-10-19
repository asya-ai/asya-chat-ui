import type { ChatMessageAttachmentInput } from "@/lib/types"

const APPLE_IMAGE_EXT = /\.(heic|heif|heics)$/i

const inferContentType = (file: File): string => {
  if (file.type) return file.type
  if (APPLE_IMAGE_EXT.test(file.name || "")) return "image/heic"
  return "application/octet-stream"
}

const readFileAsDataUrl = (file: File) =>
  new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })

const toAttachment = async (
  file: File,
  fallbackName: string
): Promise<ChatMessageAttachmentInput | null> => {
  const dataUrl = await readFileAsDataUrl(file)
  const [, base64] = dataUrl.split(",", 2)
  if (!base64) return null
  return {
    file_name: file.name || fallbackName,
    content_type: inferContentType(file),
    data_base64: base64,
  }
}

/** Chat composer pending chips — keep File locally; upload as multipart. */
export const filesToPendingAttachments = (
  files: File[],
  fallbackName = "attachment"
): ChatMessageAttachmentInput[] =>
  files.map((file) => {
    const contentType = inferContentType(file)
    const isImage = contentType.startsWith("image/")
    return {
      local_id: crypto.randomUUID(),
      file_name: file.name || fallbackName,
      content_type: contentType,
      file,
      preview_url: isImage ? URL.createObjectURL(file) : undefined,
      upload_status: "pending" as const,
      upload_progress: 0,
    }
  })

export const revokePendingAttachmentPreview = (
  attachment: ChatMessageAttachmentInput
) => {
  if (attachment.preview_url?.startsWith("blob:")) {
    URL.revokeObjectURL(attachment.preview_url)
  }
}

/** Still used by project/agent source uploads (JSON base64). */
export const readFilesAsAttachments = async (
  files: File[]
): Promise<ChatMessageAttachmentInput[]> => {
  const results: ChatMessageAttachmentInput[] = []
  for (const file of files) {
    const attachment = await toAttachment(file, "attachment")
    if (attachment) results.push(attachment)
  }
  return results
}

export const readClipboardImagesAsPending = async (
  items: DataTransferItemList | DataTransferItem[]
): Promise<ChatMessageAttachmentInput[]> => {
  const list = Array.from(items)
  const imageItems = list.filter((item) => item.type.startsWith("image/"))
  if (imageItems.length === 0) return []
  const files: File[] = []
  for (const item of imageItems) {
    const file = item.getAsFile()
    if (file) files.push(file)
  }
  return filesToPendingAttachments(files, "pasted-image")
}

export const readClipboardImagesAsAttachments = async (
  items: DataTransferItemList | DataTransferItem[]
): Promise<ChatMessageAttachmentInput[]> => {
  const list = Array.from(items)
  const imageItems = list.filter((item) => item.type.startsWith("image/"))
  if (imageItems.length === 0) return []
  const results: ChatMessageAttachmentInput[] = []
  for (const item of imageItems) {
    const file = item.getAsFile()
    if (!file) continue
    const attachment = await toAttachment(file, "pasted-image")
    if (attachment) results.push(attachment)
  }
  return results
}
