import { useState } from "react"
import type { ReactNode } from "react"
import { useNavigate } from "react-router"
import { Menu, PanelLeftOpen } from "lucide-react"

import { useI18n } from "@/lib/i18n-context"
import { orgStore } from "@/lib/storage"
import { useMe, useOrgsMine } from "@/hooks/use-chat-query"
import { Button } from "@/components/ui/button"
import { Sheet, SheetContent, SheetTrigger } from "@/components/ui/sheet"
import ChatSidebar from "@/pages/chat/ChatSidebar"

type AppShellProps = {
  activeSection: "history" | "projects" | null
  children: (sidebarControls: ReactNode) => ReactNode
}

export const AppShell = ({ activeSection, children }: AppShellProps) => {
  const navigate = useNavigate()
  const { t } = useI18n()
  const { data: orgs = [] } = useOrgsMine()
  const { data: currentUser } = useMe()
  const [mobileOpen, setMobileOpen] = useState(false)
  const [desktopOpen, setDesktopOpen] = useState(true)

  const profileLabel = currentUser
    ? currentUser.display_name || currentUser.username || currentUser.email || t("me_settings")
    : null
  const activeOrgName = orgs.find((org) => org.id === orgStore.get())?.name
  const footer = (
    <Button
      variant="ghost"
      className="h-14 w-full justify-start gap-1.5 p-1.5 text-left"
      onClick={() => navigate("/settings/me")}
    >
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
        {activeOrgName ? (
          <span className="block truncate text-xs font-medium leading-4 text-muted-foreground">
            {activeOrgName}
          </span>
        ) : null}
      </span>
    </Button>
  )

  const sidebar = (onClose?: () => void) => (
    <ChatSidebar
      title={t("chat_title")}
      labels={{
        newChat: t("chat_new"),
        history: t("chat_history"),
        projects: t("project_title"),
        close: t("common_close"),
      }}
      activeSection={activeSection}
      onNewChat={() => {
        onClose?.()
        navigate("/chat")
      }}
      onOpenHistory={() => {
        onClose?.()
        navigate("/history")
      }}
      onOpenProjects={() => {
        onClose?.()
        navigate("/projects")
      }}
      onRequestClose={onClose}
      footer={footer}
    />
  )

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
            style={{ maskImage: "url('/icon-panel.svg')" }}
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
        className={`relative min-h-0 min-w-0 flex-1 overflow-hidden bg-background md:m-2 md:rounded-card md:border md:border-border ${
          desktopOpen ? "md:ml-0" : "md:ml-2"
        }`}
      >
        {children(controls)}
      </main>
    </div>
  )
}

export default AppShell
