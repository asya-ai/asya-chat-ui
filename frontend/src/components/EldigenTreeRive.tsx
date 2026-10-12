import { useRive } from "@rive-app/react-canvas"
import type { Rive } from "@rive-app/react-canvas"
import { useEffect, useMemo, useRef } from "react"

import { BrandLogo } from "@/components/BrandLogo"
import { useBranding } from "@/lib/branding"
import { cssVarColor, useDocumentTheme, type ThemeMode } from "@/lib/theme"
import { cn } from "@/lib/utils"

type EldigenTreeRiveProps = {
  className?: string
  alt?: string
}

const TREE_SRC = "/rive/eldigen_tree.riv"
const REVEAL_DELAY_MS = 300

const RIVE_OPTIONS = {
  shouldUseIntersectionObserver: false,
} as const

/** Tree fills bind through the global `theme` ViewModel (not the artboard VM). */
const applyTreeTheme = (rive: Rive, themeMode: ThemeMode) => {
  const themeVm = rive.viewModelByName("theme")
  if (!themeVm) return

  const themeInstance =
    themeVm.instanceByName(themeMode) ?? themeVm.defaultInstance() ?? null
  if (!themeInstance) return

  // Designer tokens: tree→bg, background→ring, fruits→switch-primary-bg.
  // Always opaque — never forward CSS alpha.
  const tree = cssVarColor("--background")
  const background = cssVarColor("--ring")
  const fruits = cssVarColor("--switch-primary-bg")
  const treeProp = themeInstance.color("tree")
  const backgroundProp = themeInstance.color("background")
  const fruitsProp = themeInstance.color("fruits")
  if (tree && treeProp) treeProp.rgba(tree.r, tree.g, tree.b, 255)
  if (background && backgroundProp) {
    backgroundProp.rgba(background.r, background.g, background.b, 255)
  }
  if (fruits && fruitsProp) fruitsProp.rgba(fruits.r, fruits.g, fruits.b, 255)

  rive.setGlobalViewModelInstance("theme", themeInstance)

  const revealVm = rive.viewModelByName("reveal")
  const revealInstance =
    revealVm?.instanceByName("Instance") ?? revealVm?.defaultInstance() ?? null
  if (revealInstance) {
    rive.setGlobalViewModelInstance("reveal", revealInstance)
  }

  rive.bind()
}

/**
 * Empty-chat hero tree. Binds light/dark theme, maps product colors onto the
 * Rive `theme` ViewModel, then fires `reveal.start`.
 */
export const EldigenTreeRive = ({ className, alt = "Eldigen" }: EldigenTreeRiveProps) => {
  const { logoUrl } = useBranding()
  if (logoUrl) {
    return <BrandLogo className={className} alt={alt} />
  }
  return <EldigenTreeRiveCanvas className={className} alt={alt} />
}

const EldigenTreeRiveCanvas = ({ className, alt = "Eldigen" }: EldigenTreeRiveProps) => {
  const themeMode = useDocumentTheme()
  const themeModeRef = useRef(themeMode)
  themeModeRef.current = themeMode
  const revealedRef = useRef(false)

  const params = useMemo(
    () => ({
      src: TREE_SRC,
      artboard: "tree",
      stateMachines: "State Machine 1",
      autoplay: true,
      autoBind: false,
      onRiveReady: (instance: Rive) => {
        applyTreeTheme(instance, themeModeRef.current)
      },
    }),
    [],
  )

  const { rive, RiveComponent } = useRive(params, RIVE_OPTIONS)

  useEffect(() => {
    if (!rive) return
    applyTreeTheme(rive, themeMode)
  }, [rive, themeMode])

  useEffect(() => {
    if (!rive || revealedRef.current) return

    const revealInstance = rive.globalViewModelInstance("reveal")
    const start = revealInstance?.trigger("start")
    if (!start) return

    const reduced =
      typeof window !== "undefined" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    const delay = reduced ? 0 : REVEAL_DELAY_MS
    const timer = window.setTimeout(() => {
      start.trigger()
      revealedRef.current = true
    }, delay)
    return () => window.clearTimeout(timer)
  }, [rive])

  return (
    <div role="img" aria-label={alt} className={cn("overflow-hidden", className)}>
      <RiveComponent className="size-full" />
    </div>
  )
}
