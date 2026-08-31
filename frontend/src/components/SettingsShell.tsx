import type { CSSProperties, ReactNode } from "react"
import { Link } from "@tanstack/react-router"
import { useI18n } from "@/lib/i18n-context"

import {
  Sidebar,
  SidebarContent,
  SidebarInset,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarTrigger,
} from "@/components/ui/sidebar"

type SettingsNavItem = {
  label: string
  href: string
  visible?: boolean
  active?: boolean
}

type SettingsShellProps = {
  title: string
  items: SettingsNavItem[]
  actions?: ReactNode
  children: ReactNode
}

export const SettingsShell = ({ title, items, actions, children }: SettingsShellProps) => {
  const { t } = useI18n()
  const visibleItems = items.filter((item) => item.visible !== false)

  return (
    <SidebarProvider
      className="h-svh overflow-hidden bg-background"
      style={{ "--sidebar-width": "229px" } as CSSProperties}
    >
      <Sidebar className="border-0 bg-sidebar p-2">
        <SidebarContent className="gap-3 px-0 py-2">
          <Link to="/chat/{-$chatId}" className="flex items-center gap-2 px-1.5 py-2">
            <img src="/favicon.svg" alt="Eldigen" className="size-9 object-contain" />
            <span className="font-heading text-4xl leading-9">Chat</span>
          </Link>
          <nav aria-label={t("settings_navigation")}>
            <SidebarMenu>
              {visibleItems.map((item) => (
                <SidebarMenuItem key={item.href}>
                  <SidebarMenuButton asChild isActive={item.active}>
                    <Link to={item.href}>{item.label}</Link>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </nav>
        </SidebarContent>
      </Sidebar>
      <SidebarInset className="m-2 ml-0 flex h-[calc(100svh-1rem)] min-h-0 flex-col overflow-hidden rounded-[var(--radius-card)] border border-border bg-background max-md:ml-2">
        <header className="flex h-[60px] shrink-0 items-center gap-3 px-4">
          <SidebarTrigger className="md:hidden" />
          <h1 className="font-heading text-4xl font-normal leading-10">{title}</h1>
          <div className="ml-auto flex items-center gap-2">{actions}</div>
        </header>
        <main id="main-content" className="flex-1 min-h-0 overflow-y-auto p-4 sm:p-6">
          {children}
        </main>
      </SidebarInset>
    </SidebarProvider>
  )
}
