import type { ChatMessageAttachmentInput } from "@/lib/types"

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
    content_type: file.type || "application/octet-stream",
    data_base64: base64,
  }
}

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
