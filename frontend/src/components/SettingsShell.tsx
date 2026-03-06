import type { ReactNode } from "react"
import { useNavigate } from "react-router-dom"

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
  const navigate = useNavigate()
  const visibleItems = items.filter((item) => item.visible !== false)

  return (
    <SidebarProvider>
      <Sidebar>
        <SidebarContent className="px-2 py-4">
          <SidebarMenu>
            {visibleItems.map((item) => (
              <SidebarMenuItem key={item.href}>
                <SidebarMenuButton
                  isActive={item.active}
                  onClick={() => navigate(item.href)}
                >
                  <span>{item.label}</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
            ))}
          </SidebarMenu>
        </SidebarContent>
      </Sidebar>
      <SidebarInset className="flex min-h-svh flex-col overflow-hidden">
        <header className="flex items-center gap-3 border-b px-6 py-4">
          <SidebarTrigger className="md:hidden" />
          <h1 className="text-xl font-semibold">{title}</h1>
          <div className="ml-auto flex items-center gap-2">{actions}</div>
        </header>
        <div className="flex-1 min-h-0 overflow-y-auto p-6">{children}</div>
      </SidebarInset>
    </SidebarProvider>
  )
}
