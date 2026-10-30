import { useEffect, useRef, useState } from "react"
import { Loader2, Square, Volume2 } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { ApiError, ttsApi } from "@/lib/api"
import { useI18n } from "@/lib/i18n-context"
import { canUseTtsPlayback } from "@/lib/ttsPlayback"
import type { TtsStatus } from "@/lib/types"

type SpeakMessageButtonProps = {
  text: string
  ttsStatus: TtsStatus | null
}

type PlayState = "idle" | "loading" | "playing"

let activeStop: (() => void) | null = null

export const SpeakMessageButton = ({ text, ttsStatus }: SpeakMessageButtonProps) => {
  const { t } = useI18n()
  const [playState, setPlayState] = useState<PlayState>("idle")
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const objectUrlRef = useRef<string | null>(null)
  const utteranceRef = useRef<SpeechSynthesisUtterance | null>(null)

  useEffect(() => {
    return () => {
      stopPlayback()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const stopPlayback = () => {
    if (activeStop === stopPlayback) activeStop = null
    if (audioRef.current) {
      audioRef.current.pause()
      audioRef.current = null
    }
    if (objectUrlRef.current) {
      URL.revokeObjectURL(objectUrlRef.current)
      objectUrlRef.current = null
    }
    if (utteranceRef.current && typeof window !== "undefined" && window.speechSynthesis) {
      window.speechSynthesis.cancel()
      utteranceRef.current = null
    }
    setPlayState("idle")
  }

  if (!canUseTtsPlayback(ttsStatus) || !text.trim() || !ttsStatus) {
    return null
  }

  const status = ttsStatus
  const label = playState === "playing" ? t("chat_speak_stop") : t("chat_speak")

  const reportError = (err: unknown) => {
    const message =
      err instanceof ApiError
        ? err.message
        : err instanceof Error
          ? err.message
          : t("chat_speak_error")
    toast.error(message)
  }

  const speakBrowser = () => {
    if (!window.speechSynthesis) {
      throw new Error(t("chat_speak_error"))
    }

    const start = () => {
      window.speechSynthesis.cancel()
      const utterance = new SpeechSynthesisUtterance(text)
      const locked = (status.voice_id || "").trim()
      if (locked) {
        const match = window.speechSynthesis
          .getVoices()
          .find((voice) => voice.voiceURI === locked || voice.name === locked)
        if (match) utterance.voice = match
      }
      utterance.onend = () => {
        utteranceRef.current = null
        if (activeStop === stopPlayback) activeStop = null
        setPlayState("idle")
      }
      utterance.onerror = () => {
        utteranceRef.current = null
        if (activeStop === stopPlayback) activeStop = null
        setPlayState("idle")
        toast.error(t("chat_speak_error"))
      }
      utteranceRef.current = utterance
      activeStop = stopPlayback
      setPlayState("playing")
      window.speechSynthesis.speak(utterance)
    }

    if (window.speechSynthesis.getVoices().length === 0) {
      const onVoices = () => {
        window.speechSynthesis.removeEventListener("voiceschanged", onVoices)
        start()
      }
      window.speechSynthesis.addEventListener("voiceschanged", onVoices)
      window.speechSynthesis.getVoices()
      return
    }
    start()
  }

  const speakCloud = async () => {
    setPlayState("loading")
    const blob = await ttsApi.synthesize(text)
    const url = URL.createObjectURL(blob)
    objectUrlRef.current = url
    const audio = new Audio(url)
    audioRef.current = audio
    activeStop = stopPlayback
    await new Promise<void>((resolve, reject) => {
      audio.onended = () => {
        stopPlayback()
        resolve()
      }
      audio.onerror = () => {
        stopPlayback()
        reject(new Error(t("chat_speak_error")))
      }
      setPlayState("playing")
      void audio.play().catch(reject)
    })
  }

  const onClick = () => {
    if (playState === "playing" || playState === "loading") {
      stopPlayback()
      return
    }
    if (activeStop) activeStop()
    void (async () => {
      try {
        if (status.provider === "browser") {
          speakBrowser()
          return
        }
        await speakCloud()
      } catch (err) {
        stopPlayback()
        reportError(err)
      }
    })()
  }

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="opacity-70 hover:opacity-100 size-7"
          onClick={onClick}
          aria-label={label}
          disabled={playState === "loading"}
        >
          {playState === "loading" ? (
            <Loader2 aria-hidden="true" className="size-4 animate-spin" />
          ) : playState === "playing" ? (
            <Square aria-hidden="true" className="size-4" />
          ) : (
            <Volume2 aria-hidden="true" className="size-4" />
          )}
        </Button>
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  )
}
