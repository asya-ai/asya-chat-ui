import type { ReactNode } from "react"
import { Link } from "react-router-dom"
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
    <SidebarProvider className="h-svh overflow-hidden">
      <Sidebar>
        <SidebarContent className="px-2 py-4">
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
      <SidebarInset className="flex h-svh min-h-0 flex-col overflow-hidden">
        <header className="flex items-center gap-3 border-b px-6 py-4">
          <SidebarTrigger className="md:hidden" />
          <h1 className="text-xl font-semibold">{title}</h1>
          <div className="ml-auto flex items-center gap-2">{actions}</div>
        </header>
        <main id="main-content" className="flex-1 min-h-0 overflow-y-auto p-6">
          {children}
        </main>
      </SidebarInset>
    </SidebarProvider>
  )
}
