import { useCallback, useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react"
import { Link, useNavigate } from "@tanstack/react-router"
import { ArrowLeft, Menu, PanelLeftOpen } from "lucide-react"

import { AppNavRail } from "@/components/AppNavRail"
import { BrandLogo } from "@/components/BrandLogo"
import { UsageLimitBanners } from "@/components/UsageLimitBanners"
import { Button } from "@/components/ui/button"
import { Sheet, SheetContent, SheetTrigger } from "@/components/ui/sheet"
import { useMe } from "@/hooks/use-chat-query"
import { useBranding } from "@/lib/branding"
import { useI18n } from "@/lib/i18n-context"
import { useSettingsAuth, useSettingsNav } from "@/lib/settings-nav"
import {
  orgStore,
  sidebarPinnedStore,
  sidebarWidthStore,
} from "@/lib/storage"
import { useSidebarHover, useSidebarPinnedMount } from "@/lib/sidebar-hover"
import { cn } from "@/lib/utils"

type SettingsShellProps = {
  title: string
  actions?: ReactNode
  children: ReactNode
}

const SettingsSidebarNav = ({
  onNavigate,
  className,
}: {
  onNavigate?: () => void
  className?: string
}) => {
  const { t } = useI18n()
  const { isAdmin, isSuperAdmin } = useSettingsAuth()
  const groups = useSettingsNav({ isAdmin, isSuperAdmin })

  return (
    <nav aria-label={t("settings_navigation")} className={cn("flex min-h-0 flex-1 flex-col", className)}>
      <div className="flex flex-col gap-4">
        {groups.map((group) => (
          <div key={group.label} className="space-y-0.5">
            <p className="text-muted-foreground px-2 text-xs font-semibold uppercase tracking-wide">
              {group.label}
            </p>
            <div className="flex flex-col gap-0.5">
              {group.items.map((item) => (
                <Button
                  key={item.href}
                  asChild
                  variant="ghost"
                  className={cn(
                    "h-9 w-full justify-start px-2 text-sm font-medium",
                    item.active && "bg-sidebar-accent"
                  )}
                >
                  <Link to={item.href} onClick={onNavigate}>
                    {item.label}
                  </Link>
                </Button>
              ))}
            </div>
          </div>
        ))}
      </div>
    </nav>
  )
}

const SettingsSidebar = ({
  onClose,
  hideBrandHeader = false,
}: {
  onClose?: () => void
  hideBrandHeader?: boolean
}) => {
  const { t } = useI18n()
  const navigate = useNavigate()
  const { data: currentUser } = useMe()
  const { appTitle: brandingTitle } = useBranding()

  const profileLabel = currentUser
    ? currentUser.display_name || currentUser.username || currentUser.email || t("me_settings")
    : null

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      {hideBrandHeader ? null : (
        <div className="flex items-center gap-2 px-1.5 py-2">
          <BrandLogo className="size-9" />
          <span className="font-heading text-4xl leading-9">
            {brandingTitle || t("chat_title")}
          </span>
        </div>
      )}

      <Button
        className="w-full shrink-0"
        variant="outline"
        onClick={() => {
          onClose?.()
          navigate({ to: "/chat/{-$chatId}" })
        }}
      >
        <ArrowLeft className="size-4" aria-hidden="true" />
        {t("common_back_to_chat")}
      </Button>

      <div className="h-0 shrink-0 border-t border-border" />

      <SettingsSidebarNav onNavigate={onClose} className="min-h-0 flex-1 overflow-y-auto" />

      {hideBrandHeader ? null : (
        <div className="mt-auto shrink-0 border-t border-border pt-2">
          <Button
            variant="ghost"
            className="h-14 w-full justify-start gap-1.5 p-1.5 text-left"
            asChild
          >
            <Link to="/settings/me" onClick={onClose}>
              {currentUser?.avatar_url ? (
                <img
                  src={currentUser.avatar_url}
                  alt=""
                  className="size-11 shrink-0 rounded-lg object-cover"
                />
              ) : (
                <span className="flex size-11 shrink-0 items-center justify-center rounded-lg bg-secondary text-sm font-semibold">
                  {profileLabel ? profileLabel.slice(0, 1).toUpperCase() : null}
                </span>
              )}
              <span className="min-w-0">
                {profileLabel ? (
                  <span className="block truncate text-sm font-semibold leading-4">{profileLabel}</span>
                ) : (
                  <span className="block h-4 w-24 animate-pulse rounded bg-secondary" />
                )}
                <span className="text-muted-foreground block truncate text-xs font-medium leading-4">
                  {t("common_settings")}
                </span>
              </span>
            </Link>
          </Button>
        </div>
      )}
    </div>
  )
}

export const SettingsShell = ({ title, actions, children }: SettingsShellProps) => {
  const { t } = useI18n()
  const [mobileOpen, setMobileOpen] = useState(false)
  const [sidebarPinned, setSidebarPinned] = useState(() => sidebarPinnedStore.get())
  const [sidebarWidthPx, setSidebarWidthPx] = useState(() => sidebarWidthStore.get())
  const [sidebarResizing, setSidebarResizing] = useState(false)
  const { hoverOpen: sidebarHoverOpen, openHover: openSidebarHover, closeHover: closeSidebarHover } =
    useSidebarHover(sidebarPinned)
  const sidebarPinnedMounted = useSidebarPinnedMount(sidebarPinned)
  const sidebarResizeStartXRef = useRef(0)
  const sidebarResizeStartWidthRef = useRef(sidebarWidthStore.default)
  const sidebarWidthPxRef = useRef(sidebarWidthPx)
  const orgId = orgStore.get()
  const { data: currentUser } = useMe()

  useEffect(() => {
    sidebarWidthPxRef.current = sidebarWidthPx
  }, [sidebarWidthPx])

  const profileLabel = currentUser
    ? currentUser.display_name || currentUser.username || currentUser.email || t("me_settings")
    : null

  const toggleSidebarPinned = useCallback(() => {
    setSidebarPinned((prev) => {
      const next = !prev
      sidebarPinnedStore.set(next)
      return next
    })
  }, [])

  useEffect(() => {
    if (!sidebarResizing) return
    const previousCursor = document.body.style.cursor
    const previousUserSelect = document.body.style.userSelect
    document.body.style.cursor = "col-resize"
    document.body.style.userSelect = "none"
    const onMove = (event: PointerEvent) => {
      const delta = event.clientX - sidebarResizeStartXRef.current
      const next = Math.min(
        sidebarWidthStore.max,
        Math.max(sidebarWidthStore.min, sidebarResizeStartWidthRef.current + delta)
      )
      setSidebarWidthPx(next)
    }
    const onUp = () => {
      setSidebarResizing(false)
      sidebarWidthStore.set(sidebarWidthPxRef.current)
    }
    window.addEventListener("pointermove", onMove)
    window.addEventListener("pointerup", onUp)
    return () => {
      document.body.style.cursor = previousCursor
      document.body.style.userSelect = previousUserSelect
      window.removeEventListener("pointermove", onMove)
      window.removeEventListener("pointerup", onUp)
    }
  }, [sidebarResizing])

  const panel = (onClose?: () => void, hideBrandHeader = false) => (
    <SettingsSidebar onClose={onClose} hideBrandHeader={hideBrandHeader} />
  )

  return (
    <div className="relative flex h-svh overflow-hidden bg-background">
      <AppNavRail
        profileLabel={profileLabel}
        avatarUrl={currentUser?.avatar_url}
        settingsLabel={t("me_settings")}
        onLogoClick={() => {
          openSidebarHover()
        }}
        onMouseEnter={openSidebarHover}
        onMouseLeave={closeSidebarHover}
      />

      <aside
        className={cn(
          "relative my-2 hidden min-h-0 shrink-0 flex-col overflow-hidden rounded-card md:flex",
          "transition-[width,opacity,transform] duration-600 ease-in-out",
          sidebarPinned
            ? "border border-sidebar-border bg-sidebar text-sidebar-foreground opacity-100 translate-x-0"
            : "pointer-events-none border-transparent opacity-0 -translate-x-1"
        )}
        style={{ width: sidebarPinned ? sidebarWidthPx : 0 }}
        aria-hidden={!sidebarPinned}
      >
        {sidebarPinnedMounted ? (
          <div
            className="relative flex h-full min-h-0 flex-col"
            style={{ width: sidebarWidthPx }}
          >
            {panel(undefined, true)}
            {sidebarPinned ? (
              <div
                role="separator"
                aria-orientation="vertical"
                aria-label={t("sidebar_toggle")}
                title="Drag to resize"
                className={cn(
                  "absolute inset-y-0 -right-1 z-20 w-2 cursor-col-resize touch-none",
                  sidebarResizing && "bg-primary/20"
                )}
                onPointerDown={(event) => {
                  event.preventDefault()
                  sidebarResizeStartXRef.current = event.clientX
                  sidebarResizeStartWidthRef.current = sidebarWidthPx
                  setSidebarResizing(true)
                }}
                onDoubleClick={() => {
                  setSidebarWidthPx(sidebarWidthStore.default)
                  sidebarWidthStore.set(sidebarWidthStore.default)
                }}
              />
            ) : null}
          </div>
        ) : null}
      </aside>

      {!sidebarPinned && sidebarHoverOpen ? (
        <aside
          className="absolute top-2 bottom-2 left-18 z-40 hidden min-h-0 animate-in fade-in slide-in-from-left-2 flex-col overflow-hidden rounded-card border border-sidebar-border bg-sidebar text-sidebar-foreground shadow-[0_25px_50px_-12px_rgba(7,12,28,0.21)] duration-600 ease-in-out md:flex"
          style={{ width: sidebarWidthPx }}
          onMouseEnter={openSidebarHover}
          onMouseLeave={closeSidebarHover}
        >
          {panel(undefined, true)}
        </aside>
      ) : null}

      <main
        id="main-content"
        className="relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-background md:m-2 md:rounded-card md:border md:border-border"
      >
        <div className="flex h-15 shrink-0 items-center justify-between gap-3 border-b border-border px-3">
          <div className="flex min-w-0 items-center gap-2">
            <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
              <SheetTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="md:hidden"
                  aria-label={t("sidebar_toggle")}
                >
                  <Menu aria-hidden="true" />
                </Button>
              </SheetTrigger>
              <SheetContent side="left" className="w-96 bg-sidebar p-0" showCloseButton={false}>
                {panel(() => setMobileOpen(false))}
              </SheetContent>
            </Sheet>
            <Button
              variant="ghost"
              size="icon"
              className="hidden md:inline-flex"
              onClick={toggleSidebarPinned}
              aria-label={t("sidebar_toggle")}
              aria-pressed={sidebarPinned}
            >
              {sidebarPinned ? (
                <span
                  aria-hidden="true"
                  className="figma-icon size-4"
                  style={{ maskImage: "url('/icon-panel.svg')" } as CSSProperties}
                />
              ) : (
                <PanelLeftOpen aria-hidden="true" />
              )}
            </Button>
            <h1 className="font-heading truncate text-3xl font-normal leading-9">{title}</h1>
          </div>
          {actions ? (
            <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">{actions}</div>
          ) : null}
        </div>
        <UsageLimitBanners orgId={orgId} />
        <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
      </main>
    </div>
  )
}
