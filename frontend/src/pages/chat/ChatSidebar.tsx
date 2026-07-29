import type { ReactNode } from "react"

import { Button } from "@/components/ui/button"
import { Plus, X } from "lucide-react"

type ChatSidebarProps = {
  title: string
  labels: {
    newChat: string
    history: string
    projects: string
    close?: string
  }
  activeSection?: "history" | "projects" | null
  onNewChat: () => void
  onOpenHistory: () => void
  onOpenProjects: () => void
  footer?: ReactNode
  onRequestClose?: () => void
}

export const ChatSidebar = ({
  title,
  labels,
  activeSection,
  onNewChat,
  onOpenHistory,
  onOpenProjects,
  footer,
  onRequestClose,
}: ChatSidebarProps) => {
  return (
    <nav aria-label={title} className="flex h-full min-h-0 flex-col">
      <div className="flex h-15 shrink-0 items-center justify-between gap-2 px-1.5 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className="flex size-11 shrink-0 items-center justify-center">
            <img
              src="/favicon.svg"
              alt="Eldigen"
              className="h-11 w-[39.286px] object-contain"
            />
          </span>
          <p className="truncate font-heading text-4xl leading-9">{title}</p>
        </div>
        {onRequestClose ? (
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            onClick={onRequestClose}
            aria-label={labels.close}
          >
            <X aria-hidden="true" />
          </Button>
        ) : null}
      </div>

      <div className="mt-3 flex shrink-0 flex-col gap-3">
        <Button className="w-full" onClick={onNewChat}>
          <Plus aria-hidden="true" />
          {labels.newChat}
        </Button>

        <div className="h-0 border-t border-border" />

        <div className="flex flex-col gap-0.5">
          <Button
            variant="ghost"
            className="h-9 w-full justify-start gap-0.5 p-0 data-[active=true]:bg-sidebar-accent"
            data-active={activeSection === "history"}
            onClick={onOpenHistory}
          >
            <span className="flex size-9 shrink-0 items-center justify-center">
              <span
                aria-hidden="true"
                className="figma-icon size-5"
                style={{ maskImage: "url('/icon-history.svg')" }}
              />
            </span>
            <span className="text-sm font-semibold">{labels.history}</span>
          </Button>
          <Button
            variant="ghost"
            className="h-9 w-full justify-start gap-0.5 p-0 data-[active=true]:bg-sidebar-accent"
            data-active={activeSection === "projects"}
            onClick={onOpenProjects}
          >
            <span className="flex size-9 shrink-0 items-center justify-center">
              <span
                aria-hidden="true"
                className="figma-icon size-5"
                style={{ maskImage: "url('/icon-spaces.svg')" }}
              />
            </span>
            <span className="text-sm font-semibold">{labels.projects}</span>
          </Button>
        </div>
      </div>

      <div className="min-h-0 flex-1" />
      {footer ? <div className="flex flex-col gap-0.5">{footer}</div> : null}
    </nav>
  )
}

export default ChatSidebar
