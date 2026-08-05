import type { MouseEvent } from "react"

import type { SourceItem } from "@/lib/types"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { X } from "lucide-react"
import { cn } from "@/lib/utils"

type SourcesPanelProps = {
  sources: SourceItem[]
  title: string
  emptyLabel: string
  closeLabel: string
  onClose: () => void
  onNavigateInternal?: (path: string) => void
}

const sourceUrl = (source: SourceItem) =>
  typeof source.url === "string" ? source.url : ""

const hostFromSource = (source: SourceItem) => {
  if (source.host) return source.host
  const url = sourceUrl(source)
  if (!url) return source.title?.trim() || "source"
  try {
    return new URL(url).hostname.replace(/^www\./, "")
  } catch {
    return url || "source"
  }
}

const faviconUrl = (host: string) =>
  `https://www.google.com/s2/favicons?domain=${encodeURIComponent(host)}&sz=32`

export const SourcesPanel = ({
  sources,
  title,
  emptyLabel,
  closeLabel,
  onClose,
  onNavigateInternal,
}: SourcesPanelProps) => (
  <aside className="flex h-full w-full max-w-[28.5rem] shrink-0 flex-col border-l border-border bg-background">
    <div className="flex h-15 shrink-0 items-center justify-between gap-3 px-3 py-2.5">
      <h2 className="truncate font-heading text-4xl leading-10">{title}</h2>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        onClick={onClose}
        aria-label={closeLabel}
      >
        <X aria-hidden="true" />
      </Button>
    </div>
    <ScrollArea className="min-h-0 flex-1">
      <div className="flex flex-col gap-1 px-2 pb-4">
        {sources.length === 0 ? (
          <p className="p-6 text-center text-sm text-muted-foreground">{emptyLabel}</p>
        ) : (
          sources.map((source, index) => {
            const url = sourceUrl(source)
            const host = hostFromSource(source)
            const isInternal = url.startsWith("/chat/")
            const label = source.title?.trim() || url || host
            const hasLink = Boolean(url)
            const Wrapper = hasLink ? "a" : "div"
            return (
              <Wrapper
                key={`${url || label}-${index}`}
                {...(hasLink
                  ? {
                      href: url,
                      ...(isInternal ? {} : { target: "_blank", rel: "noreferrer" }),
                    }
                  : {})}
                className={cn(
                  "flex flex-col gap-1.5 rounded-lg p-2 transition-colors",
                  "hover:bg-secondary focus-visible:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  !hasLink && "cursor-default"
                )}
                onClick={
                  hasLink
                    ? (event: MouseEvent) => {
                        if (isInternal) {
                          event.preventDefault()
                          onNavigateInternal?.(url)
                        }
                      }
                    : undefined
                }
              >
                <div className="flex items-center gap-1">
                  <span className="relative size-5 shrink-0 overflow-hidden rounded-full bg-secondary">
                    {url && !isInternal ? (
                      <img
                        src={faviconUrl(host)}
                        alt=""
                        className="size-full object-cover"
                        loading="lazy"
                      />
                    ) : (
                      <span
                        aria-hidden="true"
                        className="figma-icon m-1 size-3"
                        style={{ maskImage: "url('/icon-attachment.svg')" }}
                      />
                    )}
                  </span>
                  <span className="truncate text-xs leading-4 text-foreground/80">
                    {host}
                  </span>
                </div>
                <p className="text-sm font-medium leading-4 text-foreground">{label}</p>
              </Wrapper>
            )
          })
        )}
      </div>
    </ScrollArea>
  </aside>
)

export default SourcesPanel
