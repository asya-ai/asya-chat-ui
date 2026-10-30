/** Short activity label for the thinking timer when detailed steps are hidden. */
export function compactThinkingActivity(label: string | null | undefined): string | null {
  const trimmed = (label ?? "").trim()
  if (!trimmed) return null
  if (trimmed.startsWith("Running code")) return "Running code"
  if (trimmed.startsWith("Looking up") || trimmed.startsWith("Exploring")) {
    return "Searching web"
  }
  if (trimmed.startsWith("Delegating")) return "Delegating"
  if (trimmed.startsWith("Generating image")) return "Generating image"
  if (trimmed.startsWith("Editing image")) return "Editing image"
  if (trimmed.startsWith("Extracting PDF")) return "Extracting PDF"
  if (trimmed.startsWith("Downloading attachments")) return "Downloading"
  if (
    trimmed.startsWith("Reading document") ||
    trimmed.startsWith("Writing document") ||
    trimmed.startsWith("Editing document") ||
    trimmed.startsWith("Opening co-editing")
  ) {
    return "Editing document"
  }
  if (trimmed.startsWith("Searching past chats")) return "Searching chats"
  if (trimmed.startsWith("Reading project source") || trimmed.startsWith("Reading source")) {
    return "Reading sources"
  }
  if (trimmed === "Thinking" || trimmed === "Answering") return null
  if (/^Step \d+\/\d+$/.test(trimmed)) return null
  if (trimmed.startsWith("Resuming after worker restart")) return "Resuming"
  return trimmed.length > 48 ? `${trimmed.slice(0, 45)}…` : trimmed
}
