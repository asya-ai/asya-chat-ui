import type { TtsStatus } from "@/lib/types"

/** Instance TTS is configured and this browser can play it. */
export const canUseTtsPlayback = (status: TtsStatus | null | undefined): boolean => {
  if (!status?.enabled) return false
  if (!(status.voice_id ?? "").trim()) return false
  if (status.provider === "browser") {
    return typeof window !== "undefined" && Boolean(window.speechSynthesis)
  }
  return status.provider !== "browser"
}
