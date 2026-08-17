import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react"
import {
  Braces,
  Check,
  ChevronDown,
  Download,
  FileCode2,
  FileText,
  FileType,
  Loader2,
  Presentation,
  Table2,
  Trash2,
  X,
  type LucideIcon,
} from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { CoworkEditor } from "@/pages/chat/CoworkEditor"
import { coworkPanelWidthStore } from "@/lib/storage"
import type { CoworkDocument } from "@/lib/types"
import { cn } from "@/lib/utils"

const coworkFormatIcon = (format?: string | null): LucideIcon => {
  switch ((format || "").toLowerCase()) {
    case "presentation":
      return Presentation
    case "code":
      return FileCode2
    case "json":
      return Braces
    case "csv":
      return Table2
    case "text":
      return FileType
    case "markdown":
    default:
      return FileText
  }
}

type CoworkPanelProps = {
  document: CoworkDocument | null
  documents: CoworkDocument[]
  open: boolean
  saving: boolean
  writing?: boolean
  conflict: boolean
  content: string
  resizable?: boolean
  className?: string
  onClose: () => void
  onContentChange: (value: string) => void
  onDownload: (options?: {
    presentationFormat?: "pdf" | "pptx"
    documentFormat?: "md" | "txt" | "pdf" | "docx"
    csvFormat?: "csv" | "xlsx"
  }) => void
  onActivateDocument: (documentId: string) => void
  onDeleteDocument: (documentId: string) => void
  onReloadLatest?: () => void
}

export const CoworkPanel = ({
  document,
  documents,
  open,
  saving,
  writing = false,
  conflict,
  content,
  resizable = false,
  className,
  onClose,
  onContentChange,
  onDownload,
  onActivateDocument,
  onDeleteDocument,
  onReloadLatest,
}: CoworkPanelProps) => {
  const panelRef = useRef<HTMLElement | null>(null)
  const widthPctRef = useRef(coworkPanelWidthStore.get())
  const [localTitle, setLocalTitle] = useState(document?.title ?? "Document")
  const [widthPct, setWidthPct] = useState(() => coworkPanelWidthStore.get())
  const [isResizing, setIsResizing] = useState(false)

  useEffect(() => {
    setLocalTitle(document?.title ?? "Document")
  }, [document?.title, document?.document_id])

  useEffect(() => {
    widthPctRef.current = widthPct
  }, [widthPct])

  useEffect(() => {
    if (!isResizing) return
    const previousCursor = globalThis.document.body.style.cursor
    const previousUserSelect = globalThis.document.body.style.userSelect
    globalThis.document.body.style.cursor = "col-resize"
    globalThis.document.body.style.userSelect = "none"
    return () => {
      globalThis.document.body.style.cursor = previousCursor
      globalThis.document.body.style.userSelect = previousUserSelect
    }
  }, [isResizing])

  const selectableDocs = useMemo(() => {
    const byId = new Map<string, CoworkDocument>()
    for (const doc of documents) byId.set(doc.document_id, doc)
    if (document) byId.set(document.document_id, document)
    return [...byId.values()].sort((a, b) => {
      const aCreated = a.created_at || ""
      const bCreated = b.created_at || ""
      if (aCreated !== bCreated) return aCreated.localeCompare(bCreated)
      return a.document_id.localeCompare(b.document_id)
    })
  }, [documents, document])

  const updateWidthFromClientX = (clientX: number) => {
    const parent = panelRef.current?.parentElement
    if (!parent) return
    const parentRect = parent.getBoundingClientRect()
    if (parentRect.width <= 0) return
    const nextPct =
      ((parentRect.right - clientX) / parentRect.width) * 100
    const clamped = Math.min(
      coworkPanelWidthStore.max,
      Math.max(coworkPanelWidthStore.min, nextPct)
    )
    widthPctRef.current = clamped
    setWidthPct(clamped)
  }

  const stopResizing = () => {
    setIsResizing(false)
    coworkPanelWidthStore.set(widthPctRef.current)
  }

  const handleResizePointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!resizable || event.button !== 0) return
    event.preventDefault()
    event.currentTarget.setPointerCapture(event.pointerId)
    setIsResizing(true)
    updateWidthFromClientX(event.clientX)
  }

  const handleResizePointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!isResizing) return
    updateWidthFromClientX(event.clientX)
  }

  const handleResizePointerUp = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!isResizing) return
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
    stopResizing()
  }

  const handleResizeDoubleClick = () => {
    if (!resizable) return
    setWidthPct(coworkPanelWidthStore.default)
    widthPctRef.current = coworkPanelWidthStore.default
    coworkPanelWidthStore.set(coworkPanelWidthStore.default)
  }

  if (!open || !document) return null

  const userEdited = document.version > document.last_assistant_version
  const FormatIcon = coworkFormatIcon(document.format)

  return (
    <aside
      ref={panelRef}
      style={resizable ? { width: `${widthPct}%` } : undefined}
      className={cn(
        "relative flex h-full min-h-0 shrink-0 flex-col border-l border-border bg-background",
        !resizable && "w-full",
        className
      )}
    >
      {resizable ? (
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize document panel"
          aria-valuemin={coworkPanelWidthStore.min}
          aria-valuemax={coworkPanelWidthStore.max}
          aria-valuenow={Math.round(widthPct)}
          title="Drag to resize · double-click to reset"
          className={cn(
            "absolute inset-y-0 -left-1 z-20 w-2 cursor-col-resize touch-none",
            "after:absolute after:inset-y-0 after:left-1/2 after:w-px after:-translate-x-1/2 after:bg-transparent after:transition-colors",
            "hover:after:bg-border",
            isResizing && "after:bg-primary"
          )}
          onPointerDown={handleResizePointerDown}
          onPointerMove={handleResizePointerMove}
          onPointerUp={handleResizePointerUp}
          onPointerCancel={handleResizePointerUp}
          onDoubleClick={handleResizeDoubleClick}
        />
      ) : null}
      <div className="flex h-15 shrink-0 items-center justify-between gap-2 border-b border-border px-3 py-2.5">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <FormatIcon className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
          <div className="min-w-0">
            <div className="flex min-w-0 items-center gap-2">
              <h2 className="truncate text-sm font-semibold leading-5">{localTitle}</h2>
              {writing ? (
                <span className="inline-flex shrink-0 items-center gap-1 rounded bg-primary/10 px-1.5 py-0.5 text-[11px] font-medium text-primary">
                  <Loader2 className="size-3 animate-spin" aria-hidden="true" />
                  Writing…
                </span>
              ) : null}
              {!writing && userEdited ? (
                <span className="shrink-0 rounded bg-amber-500/15 px-1.5 py-0.5 text-[11px] font-medium text-amber-700 dark:text-amber-400">
                  Edited
                </span>
              ) : null}
              {saving ? (
                <span className="shrink-0 text-[11px] text-muted-foreground">Saving…</span>
              ) : null}
            </div>
            <p className="truncate text-xs text-muted-foreground">
              {document.file_name}
              {document.format ? ` · ${document.format}` : ""}
              {document.language ? ` · ${document.language}` : ""}
            </p>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {selectableDocs.length > 0 ? (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="h-8 max-w-48 gap-1 px-2 text-xs"
                  aria-label="Switch document"
                >
                  <span className="truncate">{document.title}</span>
                  <ChevronDown className="size-3.5 shrink-0 opacity-60" aria-hidden="true" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="min-w-56 p-1">
                {selectableDocs.map((doc) => {
                  const DocIcon = coworkFormatIcon(doc.format)
                  const isCurrent = doc.document_id === document.document_id
                  return (
                    <div
                      key={doc.document_id}
                      className="flex items-center gap-0.5 rounded-sm pr-0.5 focus-within:bg-accent"
                    >
                      <DropdownMenuItem
                        className="min-w-0 flex-1 cursor-pointer"
                        onSelect={() => {
                          if (!isCurrent) onActivateDocument(doc.document_id)
                        }}
                      >
                        <DocIcon className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
                        <span className="min-w-0 flex-1 truncate">{doc.title}</span>
                        {isCurrent ? (
                          <Check className="size-3.5 shrink-0 text-primary" aria-hidden="true" />
                        ) : null}
                      </DropdownMenuItem>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="size-7 shrink-0 text-muted-foreground hover:text-destructive"
                        aria-label={`Delete ${doc.title}`}
                        disabled={writing || doc.document_id.startsWith("pending-")}
                        onClick={(event) => {
                          event.preventDefault()
                          event.stopPropagation()
                          onDeleteDocument(doc.document_id)
                        }}
                      >
                        <Trash2 className="size-3.5" aria-hidden="true" />
                      </Button>
                    </div>
                  )
                })}
              </DropdownMenuContent>
            </DropdownMenu>
          ) : null}
          {document.format === "presentation" ? (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label="Download presentation"
                  disabled={writing}
                >
                  <Download aria-hidden="true" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem onClick={() => onDownload({ presentationFormat: "pdf" })}>
                  Download PDF
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => onDownload({ presentationFormat: "pptx" })}>
                  Download PowerPoint (.pptx)
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          ) : document.format === "markdown" || document.format === "text" ? (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label="Download document"
                  disabled={writing}
                >
                  <Download aria-hidden="true" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem
                  onClick={() =>
                    onDownload({
                      documentFormat: document.format === "text" ? "txt" : "md",
                    })
                  }
                >
                  {document.format === "text"
                    ? "Download Text (.txt)"
                    : "Download Markdown (.md)"}
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => onDownload({ documentFormat: "pdf" })}>
                  Download PDF
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => onDownload({ documentFormat: "docx" })}>
                  Download Word (.docx)
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          ) : document.format === "csv" ? (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label="Download spreadsheet"
                  disabled={writing}
                >
                  <Download aria-hidden="true" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem onClick={() => onDownload({ csvFormat: "csv" })}>
                  Download CSV (.csv)
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => onDownload({ csvFormat: "xlsx" })}>
                  Download Excel (.xlsx)
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          ) : (
            <Button type="button" variant="ghost" size="icon" onClick={() => onDownload()} aria-label="Download">
              <Download aria-hidden="true" />
            </Button>
          )}
          <Button type="button" variant="ghost" size="icon" onClick={onClose} aria-label="Close">
            <X aria-hidden="true" />
          </Button>
        </div>
      </div>
      {conflict ? (
        <div className="flex items-center justify-between gap-2 border-b border-border bg-amber-500/10 px-3 py-2 text-xs">
          <span>Document changed elsewhere. Reload to continue editing.</span>
          {onReloadLatest ? (
            <Button type="button" size="sm" variant="outline" onClick={onReloadLatest}>
              Reload
            </Button>
          ) : null}
        </div>
      ) : null}
      <CoworkEditor
        key={document.document_id}
        value={content}
        format={document.format}
        language={document.language}
        readOnly={writing}
        onChange={onContentChange}
      />
    </aside>
  )
}
