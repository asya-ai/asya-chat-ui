import { useQuery, useQueryClient } from "@tanstack/react-query"
import { createContext, useContext, useEffect, type ReactNode } from "react"

import { brandingLogoUrl, configApi } from "@/lib/api"
import type { Branding } from "@/lib/types"

export const BRANDING_QUERY_KEY = ["branding"] as const

type BrandingContextValue = {
  branding: Branding | undefined
  appTitle: string | null
  logoUrl: string | null
  isLoading: boolean
  invalidate: () => Promise<void>
}

const BrandingContext = createContext<BrandingContextValue | null>(null)

const DEFAULT_FAVICON = "/favicon.svg"
const DEFAULT_APPLE_TOUCH = "/apple-touch-icon.png"

const setLinkHref = (rel: string, href: string, type?: string) => {
  let link = document.querySelector(`link[rel='${rel}']`) as HTMLLinkElement | null
  if (!link) {
    link = document.createElement("link")
    link.rel = rel
    document.head.appendChild(link)
  }
  if (type) link.type = type
  link.href = href
}

export const BrandingProvider = ({ children }: { children: ReactNode }) => {
  const queryClient = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: BRANDING_QUERY_KEY,
    queryFn: () => configApi.branding(),
    staleTime: 60_000,
  })

  const logoUrl =
    data?.has_logo ? brandingLogoUrl(data.logo_updated_at) : null
  const appTitle = data?.app_title?.trim() || null

  useEffect(() => {
    if (logoUrl) {
      setLinkHref("icon", logoUrl)
      setLinkHref("apple-touch-icon", logoUrl)
      return
    }
    setLinkHref("icon", DEFAULT_FAVICON, "image/svg+xml")
    setLinkHref("apple-touch-icon", DEFAULT_APPLE_TOUCH)
  }, [logoUrl])

  useEffect(() => {
    if (!appTitle) return
    const meta = document.querySelector(
      "meta[name='apple-mobile-web-app-title']"
    ) as HTMLMetaElement | null
    if (meta) meta.content = appTitle
  }, [appTitle])

  const value: BrandingContextValue = {
    branding: data,
    appTitle,
    logoUrl,
    isLoading,
    invalidate: async () => {
      await queryClient.invalidateQueries({ queryKey: BRANDING_QUERY_KEY })
    },
  }

  return <BrandingContext.Provider value={value}>{children}</BrandingContext.Provider>
}

export const useBranding = () => {
  const ctx = useContext(BrandingContext)
  if (!ctx) {
    return {
      branding: undefined,
      appTitle: null,
      logoUrl: null,
      isLoading: false,
      invalidate: async () => undefined,
    } satisfies BrandingContextValue
  }
  return ctx
}
