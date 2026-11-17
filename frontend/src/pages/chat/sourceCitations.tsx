import { Children, Fragment, cloneElement, isValidElement } from "react"
import type { ReactNode } from "react"

import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import type { SourceItem } from "@/lib/types"
import { cn } from "@/lib/utils"

export type SourceCitationMaps = {
  byCatalog: Map<number, SourceItem>
  byTitle: Map<string, SourceItem>
}

export const buildSourceCitationMaps = (sources: SourceItem[]): SourceCitationMaps => {
  const byCatalog = new Map<number, SourceItem>()
  const byTitle = new Map<string, SourceItem>()
  for (const source of sources) {
    const catalogId = source.catalog_id
    if (typeof catalogId === "number" && Number.isFinite(catalogId)) {
      byCatalog.set(catalogId, source)
    }
    const title = source.title?.trim()
    if (title) {
      byTitle.set(title.toLowerCase(), source)
    }
  }
  return { byCatalog, byTitle }
}

const NUMERIC_CITE_RE = /\[(\d{1,4})\](?!\()/
const TITLE_GROUP_RE =
  /\[((?:"[^"]+"|'[^']+'|[\u201c\u201d][^\u201d]+[\u201c\u201d])(?:\s*;\s*(?:"[^"]+"|'[^']+'|[\u201c\u201d][^\u201d]+[\u201c\u201d]))*)\]/g

const parseQuotedTitles = (inner: string): string[] => {
  const titles: string[] = []
  const re = /"([^"]+)"|'([^']+)'|[\u201c]([^\u201d]+)[\u201d]/g
  let match: RegExpExecArray | null
  while ((match = re.exec(inner))) {
    const title = (match[1] ?? match[2] ?? match[3] ?? "").trim()
    if (title) titles.push(title)
  }
  return titles
}

type CitationChipProps = {
  label: string
  source: SourceItem
  onOpen?: (source: SourceItem) => void
}

const CitationChip = ({ label, source, onOpen }: CitationChipProps) => {
  const title = source.title?.trim() || label
  const snippet = source.snippet?.trim()
  return (
    <TooltipProvider delayDuration={250}>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            className={cn(
              "mx-0.5 inline-flex -translate-y-0.5 items-center rounded px-1 py-0 align-baseline",
              "text-[0.7em] font-semibold leading-none tabular-nums",
              "bg-secondary text-foreground hover:bg-secondary/80"
            )}
            onClick={(event) => {
              event.preventDefault()
              event.stopPropagation()
              onOpen?.(source)
            }}
          >
            {label}
          </button>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-sm px-3 py-2 text-xs">
          <p className="font-medium leading-snug">{title}</p>
          {snippet ? (
            <p className="mt-1 line-clamp-4 text-muted-foreground">{snippet}</p>
          ) : null}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}

const renderTextWithCitations = (
  text: string,
  maps: SourceCitationMaps,
  onOpenSource?: (source: SourceItem) => void
): ReactNode[] => {
  const out: ReactNode[] = []
  let cursor = 0
  const titleRegions: { start: number; end: number }[] = []
  for (const match of text.matchAll(TITLE_GROUP_RE)) {
    if (match.index == null) continue
    const titles = parseQuotedTitles(match[1] ?? "")
    if (titles.length === 0) continue
    titleRegions.push({ start: match.index, end: match.index + match[0].length })
  }

  const inTitleRegion = (index: number) =>
    titleRegions.some((r) => index >= r.start && index < r.end)

  TITLE_GROUP_RE.lastIndex = 0
  let match: RegExpExecArray | null
  while ((match = TITLE_GROUP_RE.exec(text))) {
    if (match.index == null) continue
    if (match.index > cursor) {
      out.push(...splitNumericCitations(text.slice(cursor, match.index), maps, onOpenSource))
    }
    const titles = parseQuotedTitles(match[1] ?? "")
    const matchIndex = match.index
    const chips = titles
      .map((title, index) => {
        const source =
          maps.byTitle.get(title.toLowerCase()) ??
          ({
            title,
            host: "project",
          } satisfies SourceItem)
        const label =
          titles.length === 1
            ? `[${title.length > 18 ? `${title.slice(0, 16)}…` : title}]`
            : `[${index + 1}]`
        return (
          <CitationChip
            key={`t-${matchIndex}-${index}`}
            label={label}
            source={source}
            onOpen={onOpenSource}
          />
        )
      })
      .filter(Boolean)
    if (chips.length > 0) {
      out.push(<Fragment key={`g-${match.index}`}>{chips}</Fragment>)
    } else {
      out.push(match[0])
    }
    cursor = match.index + match[0].length
  }
  if (cursor < text.length) {
    out.push(...splitNumericCitations(text.slice(cursor), maps, onOpenSource, inTitleRegion))
  }
  return out.length > 0 ? out : [text]
}

const splitNumericCitations = (
  text: string,
  maps: SourceCitationMaps,
  onOpenSource?: (source: SourceItem) => void,
  inTitleRegion?: (index: number) => boolean
): ReactNode[] => {
  const parts: ReactNode[] = []
  let cursor = 0
  const re = new RegExp(NUMERIC_CITE_RE.source, "g")
  let match: RegExpExecArray | null
  while ((match = re.exec(text))) {
    if (match.index == null) continue
    if (inTitleRegion?.(cursor + match.index)) continue
    if (match.index > cursor) {
      parts.push(text.slice(cursor, match.index))
    }
    const id = Number(match[1])
    const source = maps.byCatalog.get(id)
    if (source) {
      parts.push(
        <CitationChip
          key={`n-${cursor + match.index}-${id}`}
          label={`[${id}]`}
          source={source}
          onOpen={onOpenSource}
        />
      )
    } else {
      parts.push(match[0])
    }
    cursor = match.index + match[0].length
  }
  if (cursor < text.length) {
    parts.push(text.slice(cursor))
  }
  return parts
}

export const withSourceCitations = (
  children: ReactNode,
  maps: SourceCitationMaps,
  onOpenSource?: (source: SourceItem) => void
): ReactNode =>
  Children.map(children, (child, index) => {
    if (typeof child === "string") {
      return (
        <Fragment key={`cite-s-${index}`}>
          {renderTextWithCitations(child, maps, onOpenSource)}
        </Fragment>
      )
    }
    if (!isValidElement(child)) return child
    const props = child.props as { children?: ReactNode }
    if (props.children == null) return child
    return cloneElement(
      child,
      { key: child.key ?? `cite-n-${index}` },
      withSourceCitations(props.children, maps, onOpenSource)
    )
  })