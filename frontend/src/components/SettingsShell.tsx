import { useState, type CSSProperties, type ReactNode } from "react"
import { Link, useNavigate } from "@tanstack/react-router"
import { ArrowLeft, Menu, PanelLeftOpen } from "lucide-react"

import { useSettingsAuth, useSettingsNav } from "@/lib/settings-nav"
import { orgStore } from "@/lib/storage"
import { useI18n } from "@/lib/i18n-context"
import { useMe } from "@/hooks/use-chat-query"
import { UsageLimitBanners } from "@/components/UsageLimitBanners"
import { Button } from "@/components/ui/button"
import { Sheet, SheetContent, SheetTrigger } from "@/components/ui/sheet"
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
      <div className="flex flex-col gap-4 px-1">
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

const SettingsSidebar = ({ onClose }: { onClose?: () => void }) => {
  const { t } = useI18n()
  const navigate = useNavigate()
  const { data: currentUser } = useMe()

  const profileLabel = currentUser
    ? currentUser.display_name || currentUser.username || currentUser.email || t("me_settings")
    : null

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <div className="flex items-center gap-2 px-1.5 py-2">
        <img src="/favicon.svg" alt="Eldigen" className="size-9 object-contain" />
        <span className="font-heading text-4xl leading-9">{t("chat_title")}</span>
      </div>

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
    </div>
  )
}

export const SettingsShell = ({ title, actions, children }: SettingsShellProps) => {
  const { t } = useI18n()
  const [mobileOpen, setMobileOpen] = useState(false)
  const [desktopOpen, setDesktopOpen] = useState(true)
  const orgId = orgStore.get()

  const sidebar = (onClose?: () => void) => <SettingsSidebar onClose={onClose} />

  const controls = (
    <>
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
        <SheetContent side="left" className="w-57.25 bg-sidebar p-2" showCloseButton={false}>
          {sidebar(() => setMobileOpen(false))}
        </SheetContent>
      </Sheet>
      <Button
        variant="ghost"
        size="icon"
        className="hidden md:inline-flex"
        onClick={() => setDesktopOpen((open) => !open)}
        aria-label={t("sidebar_toggle")}
      >
        {desktopOpen ? (
          <span
            aria-hidden="true"
            className="figma-icon size-4"
            style={{ maskImage: "url('/icon-panel.svg')" } as CSSProperties}
          />
        ) : (
          <PanelLeftOpen aria-hidden="true" />
        )}
      </Button>
    </>
  )

  return (
    <div className="flex h-svh overflow-hidden bg-background">
      <aside
        className={`hidden min-h-0 w-57.25 shrink-0 flex-col bg-sidebar p-2 text-sidebar-foreground ${
          desktopOpen ? "md:flex" : ""
        }`}
      >
        {sidebar()}
      </aside>
      <main
        id="main-content"
        className={`relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-background md:m-2 md:rounded-card md:border md:border-border ${
          desktopOpen ? "md:ml-0" : "md:ml-2"
        }`}
      >
        <div className="flex h-15 shrink-0 items-center justify-between gap-3 border-b border-border px-3">
          <div className="flex min-w-0 items-center gap-2">
            {controls}
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
