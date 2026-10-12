import { useRive } from "@rive-app/react-canvas"
import type { Rive } from "@rive-app/react-canvas"
import { memo, useEffect, useMemo, useRef } from "react"

import { cssVarColor, useDocumentTheme } from "@/lib/theme"
import { cn } from "@/lib/utils"

type ThinkingFruitRiveProps = {
  className?: string
  /** Playback rate; `0.5` = half speed. */
  speed?: number
}

const THINKING_SRC = "/rive/eldigen_thinking.riv"

const RIVE_OPTIONS = {
  // Layout shifts in the chat were stopping/starting the renderer via IO,
  // which made the swing jump back to the start on every stream update.
  shouldUseIntersectionObserver: false,
} as const

/** Shapes bind to the artboard VM `fruit`, not the global `theme` slot. */
const applyFruitColor = (rive: Rive) => {
  const fruit = cssVarColor("--switch-primary-bg")
  if (!fruit) return
  const color = rive.viewModelInstance?.color("fruit")
  if (!color) return
  color.rgba(fruit.r, fruit.g, fruit.b, 255)
}

/** Swinging fruit while the assistant is generating. */
export const ThinkingFruitRive = memo(function ThinkingFruitRive({
  className,
  speed = 0.5,
}: ThinkingFruitRiveProps) {
  const themeMode = useDocumentTheme()
  const speedRef = useRef(speed)
  speedRef.current = speed

  const params = useMemo(
    () => ({
      src: THINKING_SRC,
      artboard: "eldigen_thinking",
      stateMachines: "state_machine_swing",
      autoplay: true,
      autoBind: true,
      onRiveReady: (instance: Rive) => {
        applyFruitColor(instance)
      },
    }),
    [],
  )

  const { rive, RiveComponent } = useRive(params, RIVE_OPTIONS)

  useEffect(() => {
    if (!rive) return
    applyFruitColor(rive)
  }, [rive, themeMode])

  useEffect(() => {
    if (!rive) return
    type AdvanceFn = (elapsed: number) => void
    const target = rive as unknown as { advanceAndReportChanges: AdvanceFn }
    const original = target.advanceAndReportChanges.bind(rive) as AdvanceFn
    target.advanceAndReportChanges = (elapsed: number) =>
      original(elapsed * speedRef.current)
    return () => {
      target.advanceAndReportChanges = original
    }
  }, [rive])

  return (
    <span
      aria-hidden="true"
      className={cn("inline-block size-4 shrink-0 overflow-hidden", className)}
    >
      <RiveComponent className="size-full" />
    </span>
  )
})
