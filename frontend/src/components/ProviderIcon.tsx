import { useEffect, useState } from "react"

import { getProviderIconCandidates } from "@/lib/providerIcons"
import { cn } from "@/lib/utils"

type ProviderIconProps = {
  provider: string | null | undefined
  modelName?: string | null
  className?: string
}

/**
 * Renders a themed provider mark. Tries remote author/provider logos first
 * (e.g. models.dev for OpenRouter upstreams), then local fallbacks.
 */
export function ProviderIcon({ provider, modelName, className }: ProviderIconProps) {
  const candidates = getProviderIconCandidates(provider, modelName)
  const [src, setSrc] = useState<string | null>(candidates[0] ?? null)

  useEffect(() => {
    let cancelled = false
    const list = getProviderIconCandidates(provider, modelName)
    if (list.length === 0) {
      setSrc(null)
      return
    }

    setSrc(list[0] ?? null)

    let index = 0
    const tryNext = () => {
      if (cancelled || index >= list.length) return
      const url = list[index]
      const img = new Image()
      img.onload = () => {
        if (!cancelled) setSrc(url)
      }
      img.onerror = () => {
        index += 1
        if (index < list.length) {
          tryNext()
        } else if (!cancelled) {
          setSrc(null)
        }
      }
      img.src = url
    }
    tryNext()

    return () => {
      cancelled = true
    }
  }, [provider, modelName])

  if (!src) return null

  return (
    <span
      aria-hidden="true"
      className={cn("figma-icon shrink-0", className)}
      style={{ maskImage: `url('${src}')` }}
    />
  )
}
