import { memo, useEffect, useMemo, useRef, useState } from "react"
import type { ComponentProps, CSSProperties, HTMLAttributes, ReactNode } from "react"
import { useNavigate } from "@tanstack/react-router"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import remarkBreaks from "remark-breaks"
import remarkMath from "remark-math"
import rehypeKatex from "rehype-katex"
import { PrismLight as SyntaxHighlighter } from "react-syntax-highlighter"
import bash from "react-syntax-highlighter/dist/esm/languages/prism/bash"
import css from "react-syntax-highlighter/dist/esm/languages/prism/css"
import javascript from "react-syntax-highlighter/dist/esm/languages/prism/javascript"
import json from "react-syntax-highlighter/dist/esm/languages/prism/json"
import jsx from "react-syntax-highlighter/dist/esm/languages/prism/jsx"
import markdown from "react-syntax-highlighter/dist/esm/languages/prism/markdown"
import python from "react-syntax-highlighter/dist/esm/languages/prism/python"
import sql from "react-syntax-highlighter/dist/esm/languages/prism/sql"
import tsx from "react-syntax-highlighter/dist/esm/languages/prism/tsx"
import typescript from "react-syntax-highlighter/dist/esm/languages/prism/typescript"
import yaml from "react-syntax-highlighter/dist/esm/languages/prism/yaml"
import {
  Copy,
  Check,
  Download,
  GitFork,
  Pencil,
  RotateCcw,
  Share,
  Trash2,
  Plus,
  X,
} from "lucide-react"
import {
  canvasToA4PdfBlob,
  exportCoworkMarkdown,
  markdownExportBodyCss,
  markdownToExportHtml,
  materializeExportMarkdownImages,
  rasterizePdfExportHtml,
} from "@/pages/chat/exportCoworkMarkdown"
import { collectChatImageUrlMap } from "@/pages/chat/resolveCoworkPresentationImages"

import type { I18nContextValue } from "@/lib/i18n-context"
import { ThinkingFruitRive } from "@/components/ThinkingFruitRive"
import { useMe } from "@/hooks/use-chat-query"
import { useBranding } from "@/lib/branding"
import { normalizeMathContent } from "@/lib/normalizeMathContent"
import {
  isIncompleteMermaidPrefix,
  MERMAID_DIAGRAM_START,
} from "@/lib/sanitizeMermaid"
import { MermaidDiagram } from "@/pages/chat/MermaidDiagram"
import { plainTextForTts } from "@/lib/plainTextForTts"
import { compactThinkingActivity } from "@/lib/thinkingActivityHint"
import { canUseTtsPlayback } from "@/lib/ttsPlayback"
import { type ActionInfoLevel } from "@/lib/storage"
import type {
  ChatMessage,
  ChatMessageAttachmentInput,
  MessageUsage,
  SourceItem,
  ToolEvent,
  TtsStatus,
} from "@/lib/types"
import { SpeakMessageButton } from "@/pages/chat/SpeakMessageButton"
import { dedupeSources, isInternalToolEvent } from "@/lib/types"
import { shouldSubmitOnEnter } from "@/lib/chat-input"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Textarea } from "@/components/ui/textarea"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"

SyntaxHighlighter.registerLanguage("bash", bash)
SyntaxHighlighter.registerLanguage("sh", bash)
SyntaxHighlighter.registerLanguage("shell", bash)
SyntaxHighlighter.registerLanguage("css", css)
SyntaxHighlighter.registerLanguage("javascript", javascript)
SyntaxHighlighter.registerLanguage("js", javascript)
SyntaxHighlighter.registerLanguage("json", json)
SyntaxHighlighter.registerLanguage("jsx", jsx)
SyntaxHighlighter.registerLanguage("markdown", markdown)
SyntaxHighlighter.registerLanguage("md", markdown)
SyntaxHighlighter.registerLanguage("python", python)
SyntaxHighlighter.registerLanguage("py", python)
SyntaxHighlighter.registerLanguage("sql", sql)
SyntaxHighlighter.registerLanguage("tsx", tsx)
SyntaxHighlighter.registerLanguage("typescript", typescript)
SyntaxHighlighter.registerLanguage("ts", typescript)
SyntaxHighlighter.registerLanguage("yaml", yaml)
SyntaxHighlighter.registerLanguage("yml", yaml)

type MessageBubbleProps = {
  msg: ChatMessage
  isUser: boolean
  isCodeEvent: boolean
  isThinking: boolean
  thinkingLabels: string[]
  currentStepLabel: string | null
  currentToolLabel: string | null
  actionInfoLevel: ActionInfoLevel
  actionsEnabled: boolean
  ttsStatus?: TtsStatus | null
  isEditing: boolean
  isEditDragActive: boolean
  editingAttachments: ChatMessageAttachmentInput[]
  editAttachmentError?: string | null
  codeTheme: Record<string, CSSProperties>
  t: I18nContextValue["t"]
  onOpenSources?: (sources: SourceItem[]) => void
  exportQuestion?: string | null
  onStartEdit: (msg: ChatMessage) => void
  onDeleteFromMessage: (msg: ChatMessage) => void
  onRetryMessage: (msg: ChatMessage) => void
  onShareMessage?: (msg: ChatMessage) => void
  onForkChat?: (msg: ChatMessage) => void
  onSaveEditedMessage: (msg: ChatMessage, content: string) => void
  onCancelEdit: () => void
  onEditPasteAttachments: (event: React.ClipboardEvent<HTMLTextAreaElement>) => void
  onEditFilesSelected: (files: File[]) => void
  onEditDragEnter: (event: React.DragEvent<HTMLDivElement>) => void
  onEditDragOver: (event: React.DragEvent<HTMLDivElement>) => void
  onEditDragLeave: (event: React.DragEvent<HTMLDivElement>) => void
  onEditDrop: (event: React.DragEvent<HTMLDivElement>) => void
  onRemoveEditingAttachment: (index: number) => void
  onPreviewAttachment: (attachment: ChatMessageAttachmentInput) => void
  onWriteApprovalDecision?: (
    decision: "once" | "session" | "always" | "deny",
    toolEvent: Extract<NonNullable<ChatMessage["tool_event"]>, { type: "write_approval" }>
  ) => void
  onSourceProposalDecision?: (
    decision: "accept" | "dismiss",
    toolEvent: Extract<NonNullable<ChatMessage["tool_event"]>, { type: "source_proposal" }>
  ) => void
}

const formatDurationMs = (ms: number): string => {
  if (!Number.isFinite(ms) || ms < 0) return "—"
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60_000) {
    const seconds = ms / 1000
    return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`
  }
  const minutes = Math.floor(ms / 60_000)
  const seconds = Math.round((ms % 60_000) / 1000)
  return `${minutes}m ${seconds}s`
}

const pad2 = (value: number) => String(Math.max(0, value)).padStart(2, "0")

/** Thinking / thought-for clock: MM:SS, or H:MM:SS when ≥ 1 hour. */
const formatThinkingClock = (ms: number): string => {
  const totalSec = Math.max(0, Math.floor(ms / 1000))
  const hours = Math.floor(totalSec / 3600)
  const minutes = Math.floor((totalSec % 3600) / 60)
  const seconds = totalSec % 60
  if (hours > 0) return `${hours}:${pad2(minutes)}:${pad2(seconds)}`
  return `${pad2(minutes)}:${pad2(seconds)}`
}

const resolveTotalTimeMs = (
  usage: Pick<MessageUsage, "queue_wait_ms" | "generation_time_ms"> | null | undefined
): number | null => {
  if (!usage) return null
  const generation = usage.generation_time_ms
  const wait = usage.queue_wait_ms
  if (generation == null && wait == null) return null
  return (generation ?? 0) + (wait ?? 0)
}

const parseTimestampMs = (value?: string | null): number | null => {
  if (!value) return null
  const trimmed = value.trim()
  // Backend stores naive UTC (`datetime.utcnow`); Date.parse treats bare ISO as local.
  const hasTz = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(trimmed)
  const ms = Date.parse(hasTz ? trimmed : `${trimmed}Z`)
  return Number.isFinite(ms) ? ms : null
}

/** Wall-clock start for the thinking timer (survives refresh via API timestamps). */
const resolveThoughtStartMs = (msg: ChatMessage): number =>
  parseTimestampMs(msg.started_at) ?? parseTimestampMs(msg.created_at) ?? Date.now()

/** Persisted thought duration after reload — generation time, else completed − started. */
const resolvePersistedThoughtMs = (msg: ChatMessage): number | null => {
  const generation = msg.usage?.generation_time_ms
  if (typeof generation === "number" && generation > 0) return generation
  const start = parseTimestampMs(msg.started_at) ?? parseTimestampMs(msg.created_at)
  const end = parseTimestampMs(msg.completed_at)
  if (start != null && end != null && end >= start) return end - start
  return null
}

const formatTokensPerSecond = (value: number): string => {
  if (!Number.isFinite(value) || value <= 0) return "—"
  if (value >= 100) return value.toFixed(0)
  if (value >= 10) return value.toFixed(1)
  return value.toFixed(2)
}

const OPEN_FENCE = /^( {0,3})(`{3,}|~{3,})(.*)$/

const markdownEndsInsideFence = (markdown: string): boolean => {
  const lines = markdown.split("\n")
  let inFence = false
  let fenceChar = ""
  let fenceLen = 0
  for (const line of lines) {
    const match = line.match(OPEN_FENCE)
    if (!inFence) {
      if (match) {
        inFence = true
        fenceChar = match[2][0] ?? "`"
        fenceLen = match[2].length
      }
      continue
    }
    if (
      match &&
      (match[2][0] ?? "") === fenceChar &&
      match[2].length >= fenceLen &&
      match[3].trim() === ""
    ) {
      inFence = false
    }
  }
  return inFence
}

const splitPipeCells = (line: string): string[] => {
  const trimmed = line.trim()
  const parts = trimmed.split("|")
  if (trimmed.startsWith("|")) parts.shift()
  if (trimmed.endsWith("|")) parts.pop()
  return parts
}

const looksLikeDelimiterRow = (line: string): boolean => {
  const cells = splitPipeCells(line)
  if (cells.length === 0) return false
  return (
    cells.some((cell) => cell.includes("-")) &&
    cells.every((cell) => /^[\s:-]*$/.test(cell))
  )
}

const formatDelimiterCell = (cell: string): string => {
  const trimmed = cell.trim()
  const left = trimmed.startsWith(":")
  const right = trimmed.endsWith(":")
  return ` ${left ? ":" : ""}---${right ? ":" : ""} `
}

const pipeRow = (cells: string[]): string => `|${cells.join("|")}|`

const padPipeRow = (line: string, columnCount: number): string => {
  const cells = splitPipeCells(line)
  while (cells.length < columnCount) cells.push(" ")
  return pipeRow(cells)
}

const delimiterRow = (columnCount: number, source?: string): string => {
  const cells = source ? splitPipeCells(source) : []
  while (cells.length < columnCount) cells.push(" --- ")
  return pipeRow(cells.slice(0, Math.max(columnCount, cells.length)).map(formatDelimiterCell))
}

/**
 * Markdown collapses 3+ newlines into one paragraph break. Keep extras as
 * empty paragraphs (nbsp) so user-typed blank lines stay visible.
 */
const preserveMarkdownBlankLines = (markdown: string): string => {
  const parts = markdown.split(/(```[\s\S]*?```)/g)
  return parts
    .map((part) => {
      if (part.startsWith("```")) return part
      return part.replace(/\n{3,}/g, (run) => {
        const extra = run.length - 2
        return `\n\n${"\u00A0\n\n".repeat(extra)}`
      })
    })
    .join("")
}

/** Close a trailing GFM table so remark-gfm can render it while the model is still streaming. */
const completeStreamingMarkdownTables = (markdown: string): string => {
  if (!markdown.includes("|") || markdownEndsInsideFence(markdown)) return markdown

  const lines = markdown.split("\n")
  let end = lines.length
  while (end > 0 && lines[end - 1].trim() === "") end--
  if (end === 0) return markdown

  const isPipeRow = (line: string) => /^\s*\|/.test(line)
  let start = end
  while (start > 0 && isPipeRow(lines[start - 1])) start--
  if (start === end) return markdown

  const table = lines.slice(start, end)
  const headerCells = splitPipeCells(table[0])
  if (headerCells.every((cell) => !cell.trim()) && table.length === 1) return markdown

  const columnCount = Math.max(1, headerCells.length)
  let delimiterIndex = table.findIndex((line, index) => index > 0 && looksLikeDelimiterRow(line))
  table[0] = padPipeRow(table[0], columnCount)
  if (delimiterIndex === -1) {
    table.splice(1, 0, delimiterRow(columnCount))
    delimiterIndex = 1
  } else {
    table[delimiterIndex] = delimiterRow(
      Math.max(columnCount, splitPipeCells(table[delimiterIndex]).length),
      table[delimiterIndex]
    )
  }

  const lastIndex = table.length - 1
  if (lastIndex > delimiterIndex) {
    table[lastIndex] = padPipeRow(table[lastIndex], columnCount)
  }

  return [...lines.slice(0, start), ...table, ...lines.slice(end)].join("\n")
}

const toMermaidChart = (content: string, language?: string | null): string | null => {
  const readyChart = (chart: string): string | null => {
    const trimmed = chart.trim()
    if (!trimmed) return null
    // Defer while the model is still streaming the diagram keyword ("flow" → "flowchart").
    if (isIncompleteMermaidPrefix(trimmed)) return null
    // Autofix runs async in MermaidDiagram via prepareMermaidChart.
    return trimmed
  }

  if (language?.toLowerCase() === "mermaid") {
    return readyChart(content)
  }

  const trimmed = content.trim()
  if (!trimmed) return null

  if (/^mermaid\s*[\r\n]+/i.test(trimmed)) {
    return readyChart(trimmed.replace(/^mermaid\s*[\r\n]+/i, ""))
  }

  if (MERMAID_DIAGRAM_START.test(trimmed)) {
    return readyChart(trimmed)
  }

  return null
}

const isBlockCode = (content: string, className?: string) =>
  Boolean(/language-\w+/.exec(className || "")) || content.includes("\n")

const codeBlockClassName =
  "relative my-3 rounded-lg border border-border bg-code text-code-foreground shadow-sm"

const codeCopyStickyClassName =
  "sticky top-0 z-10 flex h-0 justify-end overflow-visible pointer-events-none"

const codeCopyButtonClassName =
  "pointer-events-auto m-2 bg-code/90 hover:bg-muted border border-border text-[10px] text-code-foreground/80 hover:text-code-foreground uppercase tracking-wide"

const tableCopyButtonClassName =
  "h-7 px-2 bg-background/90 hover:bg-muted border border-border text-[10px] text-muted-foreground hover:text-foreground uppercase tracking-wide"

/** Serialize a rendered HTML table to TSV so spreadsheet apps keep columns. */
const tableToTsv = (table: HTMLTableElement | null): string => {
  if (!table) return ""
  return Array.from(table.querySelectorAll("tr"))
    .map((row) =>
      Array.from(row.querySelectorAll("th, td"))
        .map((cell) =>
          (cell.textContent ?? "")
            .replace(/\u00a0/g, " ")
            .replace(/\s+/g, " ")
            .trim()
            .replace(/\t/g, " ")
        )
        .join("\t")
    )
    .filter((row) => row.length > 0)
    .join("\n")
}

const scheduleIdle = (callback: () => void, timeout = 120) => {
  if (typeof window.requestIdleCallback === "function") {
    const id = window.requestIdleCallback(callback, { timeout })
    return () => window.cancelIdleCallback(id)
  }
  const id = window.setTimeout(callback, 0)
  return () => window.clearTimeout(id)
}

const copyToClipboard = async (text: string) => {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text)
    return
  }

  const textarea = document.createElement("textarea")
  textarea.value = text
  textarea.setAttribute("readonly", "")
  textarea.style.position = "fixed"
  textarea.style.top = "-9999px"
  textarea.style.left = "-9999px"
  document.body.appendChild(textarea)
  textarea.select()
  document.execCommand("copy")
  document.body.removeChild(textarea)
}

const downloadBlob = (blob: Blob, fileName: string) => {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement("a")
  anchor.href = url
  anchor.download = fileName
  anchor.rel = "noopener"
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

const CHAT_UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const COWORK_FILE_EXT_RE =
  /\.(md|markdown|txt|json|csv|py|ts|tsx|js|jsx|html|css|sql)$/i

/** Models invent cowork file_name links (relative or /chat/<file>) — those are not real routes. */
const isFakeCoworkDocumentHref = (href: string | undefined | null): boolean => {
  if (!href) return false
  const trimmed = href.trim()
  if (!trimmed || trimmed.startsWith("#") || /^(mailto|tel):/i.test(trimmed)) {
    return false
  }

  const isHttpUrl = /^https?:\/\//i.test(trimmed)
  try {
    // Resolve relative hrefs the same way the browser does (from /chat/<id> → /chat/file.md).
    const url = new URL(trimmed, window.location.href)
    const segment = decodeURIComponent(
      url.pathname.split("/").filter(Boolean).pop() || ""
    )
    if (!segment || !COWORK_FILE_EXT_RE.test(segment) || CHAT_UUID_RE.test(segment)) {
      return false
    }

    // Relative / scheme-less: "deck.md", "./deck.md"
    if (!isHttpUrl && !/^[a-z][a-z0-9+.-]*:/i.test(trimmed)) return true

    // Same-origin absolute that landed on a file-like /chat/<name.ext> or /<name.ext>
    if (url.origin === window.location.origin) {
      return (
        /^\/chat\/[^/]+\/?$/.test(url.pathname) || /^\/[^/]+\/?$/.test(url.pathname)
      )
    }

    return false
  } catch {
    return COWORK_FILE_EXT_RE.test(trimmed.split(/[\\/]/).pop() || "")
  }
}

const answerFileStem = (msg: ChatMessage) => {
  const stamp = msg.created_at ? msg.created_at.slice(0, 10) : "answer"
  return `chat-answer-${stamp}`
}

type DownloadableAttachment = {
  id?: string
  file_name?: string | null
  content_type?: string | null
  data_base64?: string | null
  content_url?: string | null
  preview_url?: string
}

const attachmentDownloadSrc = (attachment: DownloadableAttachment) => {
  if (attachment.preview_url) return attachment.preview_url
  if (attachment.data_base64) {
    return `data:${attachment.content_type ?? "application/octet-stream"};base64,${attachment.data_base64}`
  }
  return attachment.content_url || ""
}

const isAttachmentDownloadable = (attachment: DownloadableAttachment) =>
  Boolean(attachmentDownloadSrc(attachment))

/** Authoritative list for the end-of-message download strip (msg.attachments + timeline). */
const collectDownloadableAttachments = (msg: ChatMessage): DownloadableAttachment[] => {
  const byFileName = new Map<string, DownloadableAttachment>()
  const consider = (attachment: DownloadableAttachment) => {
    const fileName = attachment.file_name?.trim()
    if (!fileName || !isAttachmentDownloadable(attachment)) return
    const existing = byFileName.get(fileName)
    if (!existing || (!existing.content_url && attachment.content_url)) {
      byFileName.set(fileName, attachment)
    }
  }
  for (const attachment of msg.attachments ?? []) consider(attachment)
  for (const part of msg.stream_parts ?? []) {
    if (part.type !== "action") continue
    for (const attachment of part.attachments ?? []) consider(attachment)
  }
  return [...byFileName.values()]
}

type AssistantStreamPart = NonNullable<ChatMessage["stream_parts"]>[number]
type AssistantStreamTextPart = Extract<AssistantStreamPart, { type: "text" }>

/**
 * Everything before the final answer is "thoughts": tools, reasoning labels,
 * and any intermediate speech between them. While still generating, nothing is
 * final yet — trailing speech can still be followed by more tools.
 */
export const splitAssistantStreamParts = (
  parts: AssistantStreamPart[],
  isThinking = false
): { thoughtParts: AssistantStreamPart[]; finalTextParts: AssistantStreamTextPart[] } => {
  if (parts.length === 0) return { thoughtParts: [], finalTextParts: [] }
  if (isThinking) return { thoughtParts: parts, finalTextParts: [] }

  // Trailing tool/activity rows after the answer (e.g. post-turn sync) must not
  // hide the reply — anchor the split on the last non-empty text chunk.
  let lastTextIndex = -1
  for (let i = parts.length - 1; i >= 0; i -= 1) {
    const part = parts[i]
    if (part?.type === "text" && part.text.trim()) {
      lastTextIndex = i
      break
    }
  }

  let lastActionIndex = -1
  const actionSearchEnd = lastTextIndex >= 0 ? lastTextIndex : parts.length
  for (let i = 0; i < actionSearchEnd; i += 1) {
    const part = parts[i]
    if (part?.type !== "action") continue
    if (part.tool_event && isInternalToolEvent(part.tool_event)) continue
    lastActionIndex = i
  }
  if (lastActionIndex < 0) {
    return {
      thoughtParts: [],
      finalTextParts: parts.filter(
        (part): part is AssistantStreamTextPart => part.type === "text"
      ),
    }
  }
  return {
    thoughtParts: parts.slice(0, lastActionIndex + 1),
    finalTextParts: parts
      .slice(lastActionIndex + 1)
      .filter((part): part is AssistantStreamTextPart => part.type === "text"),
  }
}

/** Final answer only — text after the last tool/action; intermediate speech stays in thoughts. */
export const getFinalAnswerText = (msg: ChatMessage): string => {
  const parts = msg.stream_parts ?? []
  if (parts.length > 0) {
    const { finalTextParts } = splitAssistantStreamParts(parts, false)
    const fromParts = finalTextParts
      .map((part) => part.text)
      .join("")
      .trim()
    if (fromParts) return fromParts
  }
  return msg.content.trim()
}

const escapeHtml = (value: string) =>
  value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;")

const downloadAnswerMarkdown = (msg: ChatMessage) => {
  const text = getFinalAnswerText(msg)
  const blob = new Blob([text], { type: "text/markdown;charset=utf-8" })
  downloadBlob(blob, `${answerFileStem(msg)}.md`)
}

const downloadAnswerPdf = async (
  msg: ChatMessage,
  options: { question?: string | null; brandLabel: string; logoUrl?: string | null }
) => {
  const text = getFinalAnswerText(msg)
  const sources = msg.sources?.length ? dedupeSources(msg.sources) : []
  const question = (options.question ?? "").trim()
  const logoUrl =
    options.logoUrl?.trim() || `${window.location.origin}/logo_mark.svg`
  const absoluteLogoUrl = logoUrl.startsWith("http")
    ? logoUrl
    : `${window.location.origin}${logoUrl}`
  const imageUrls = collectChatImageUrlMap([msg])
  const { markdown: resolved } = await materializeExportMarkdownImages(
    text,
    imageUrls
  )
  const bodyHtml = markdownToExportHtml(resolved)
  const sourcesHtml =
    sources.length > 0
      ? `<div class="divider" aria-hidden="true">✦</div>
         <ol class="sources">
           ${sources
             .map((source, index) => {
               const href = source.url?.trim()
               const label = href || source.title?.trim() || source.host?.trim() || `Source ${index + 1}`
               return href
                 ? `<li><a href="${escapeHtml(href)}">${escapeHtml(label)}</a></li>`
                 : `<li>${escapeHtml(label)}</li>`
             })
             .join("")}
         </ol>`
      : ""

  // A4 at 96dpi CSS reference width. Rasterize in an isolated light iframe —
  // off-screen hosts + dark-mode body text made html2canvas emit blank PDFs.
  const pageCssWidth = 794
  const pageHtml = `
    <style>
      .pdf-page {
        box-sizing: border-box;
        width: ${pageCssWidth}px;
        padding: 64px 72px 72px;
        background: #ffffff;
        color: #070c1c;
        -webkit-font-smoothing: antialiased;
        text-rendering: geometricPrecision;
      }
      .pdf-brand {
        display: flex;
        align-items: center;
        gap: 10px;
        margin-bottom: 36px;
      }
      .pdf-brand img {
        width: 28px;
        height: 32px;
        object-fit: contain;
      }
      .pdf-brand span {
        font: 600 18px/1 "Google Sans Flex", ui-sans-serif, system-ui, sans-serif;
        letter-spacing: -0.02em;
        text-transform: lowercase;
      }
      /* Instrument Serif ships only as weight 400 — faux-bold looks pixelated in canvas. */
      .pdf-title {
        margin: 0 0 28px;
        font: 400 36px/1.2 "Instrument Serif", "Times New Roman", Times, serif;
        letter-spacing: -0.02em;
      }
      .pdf-body {
        font: 400 16px/1.55 "Google Sans Flex", ui-sans-serif, system-ui, sans-serif;
      }
      ${markdownExportBodyCss}
      .pdf-body h1,
      .pdf-body h2,
      .pdf-body h3 {
        margin: 28px 0 12px;
        font-family: "Instrument Serif", "Times New Roman", Times, serif;
        font-weight: 400;
        line-height: 1.25;
      }
      .pdf-body h1 { font-size: 28px; }
      .pdf-body h2 { font-size: 24px; }
      .pdf-body h3 { font-size: 20px; }
      .pdf-body p { margin: 0 0 16px; }
      .pdf-body li { margin: 0 0 8px; }
      .pdf-body strong {
        font-family: "Google Sans Flex", ui-sans-serif, system-ui, sans-serif;
        font-weight: 700;
      }
      .divider {
        margin: 36px 0 28px;
        text-align: center;
        color: #f9461f;
        font-size: 14px;
        letter-spacing: 6px;
      }
      .sources {
        margin: 0;
        padding-left: 18px;
        font: 400 12px/1.55 "Google Sans Flex", ui-sans-serif, system-ui, sans-serif;
        color: rgba(7, 12, 28, 0.78);
      }
      .sources li { margin: 0 0 6px; }
      .sources a {
        color: inherit;
        text-decoration: underline;
        word-break: break-all;
      }
    </style>
    <div class="pdf-page">
      <div class="pdf-brand">
        <img src="${absoluteLogoUrl}" alt="" />
        <span>${escapeHtml(options.brandLabel)}</span>
      </div>
      ${question ? `<h1 class="pdf-title">${escapeHtml(question)}</h1>` : ""}
      <div class="pdf-body">${bodyHtml || "<p></p>"}</div>
      ${sourcesHtml}
    </div>
  `
  const canvas = await rasterizePdfExportHtml(pageHtml, pageCssWidth)
  downloadBlob(canvasToA4PdfBlob(canvas), `${answerFileStem(msg)}.pdf`)
}

const downloadAnswerDocx = async (msg: ChatMessage) => {
  const text = getFinalAnswerText(msg)
  const { blob, fileName } = await exportCoworkMarkdown(text, {
    format: "docx",
    fileName: answerFileStem(msg),
    imageUrls: collectChatImageUrlMap([msg]),
  })
  downloadBlob(blob, fileName)
}

const ToolEventDetails = ({
  toolEvent,
  t,
  onWriteApprovalDecision,
  onSourceProposalDecision,
}: {
  toolEvent: ToolEvent
  t: I18nContextValue["t"]
  onWriteApprovalDecision?: MessageBubbleProps["onWriteApprovalDecision"]
  onSourceProposalDecision?: MessageBubbleProps["onSourceProposalDecision"]
}) => {
  const navigate = useNavigate()
  if (toolEvent.type === "source_proposal") {
    const status = toolEvent.status ?? "pending"
    const canDecide = status === "proposed" && Boolean(onSourceProposalDecision)
    const kindLabel =
      toolEvent.kind === "text"
        ? t("chat_source_proposal_kind_text")
        : t("chat_source_proposal_kind_url")
    return (
      <div className="space-y-3">
        <div className="space-y-1 text-xs">
          <p className="text-foreground">
            <span className="text-muted-foreground">{kindLabel}</span>
            {toolEvent.title ? ` · ${toolEvent.title}` : ""}
          </p>
          {toolEvent.url ? (
            <a
              href={toolEvent.url}
              target="_blank"
              rel="noreferrer"
              className="block truncate text-foreground underline-offset-2 hover:underline"
            >
              {toolEvent.url}
            </a>
          ) : null}
          {toolEvent.kind === "text" && toolEvent.content_text ? (
            <pre className="bg-background/40 max-h-32 overflow-auto rounded p-2 whitespace-pre-wrap">
              {toolEvent.content_text.slice(0, 800)}
              {toolEvent.content_text.length > 800 ? "…" : ""}
            </pre>
          ) : null}
          {toolEvent.reason ? (
            <p className="text-muted-foreground">{toolEvent.reason}</p>
          ) : null}
        </div>
        {canDecide ? (
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              variant="default"
              onClick={() => onSourceProposalDecision?.("accept", toolEvent)}
            >
              {t("chat_source_proposal_add")}
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => onSourceProposalDecision?.("dismiss", toolEvent)}
            >
              {t("chat_source_proposal_dismiss")}
            </Button>
          </div>
        ) : (
          <p className="text-muted-foreground text-xs">
            {status === "accepted"
              ? t("chat_source_proposal_added")
              : status === "dismissed"
                ? t("chat_source_proposal_dismissed")
                : status === "already_indexed"
                  ? t("chat_source_proposal_already")
                  : status === "error"
                    ? toolEvent.message || t("chat_source_proposal_failed")
                    : status === "pending"
                      ? t("chat_source_proposal_pending")
                      : toolEvent.message || status}
          </p>
        )}
      </div>
    )
  }
  if (toolEvent.type === "write_approval") {
    const pending = toolEvent.status !== "resolved"
    const actions = toolEvent.actions ?? []
    return (
      <div className="space-y-3">
        <ul className="space-y-1 text-xs">
          {actions.map((action) => (
            <li key={action.id} className="text-foreground">
              {action.summary}
            </li>
          ))}
        </ul>
        {pending && onWriteApprovalDecision ? (
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              variant="default"
              onClick={() => onWriteApprovalDecision("once", toolEvent)}
            >
              {t("chat_write_allow_once")}
            </Button>
            <Button
              size="sm"
              variant="secondary"
              onClick={() => onWriteApprovalDecision("session", toolEvent)}
            >
              {t("chat_write_allow_session")}
            </Button>
            <Button
              size="sm"
              variant="secondary"
              onClick={() => onWriteApprovalDecision("always", toolEvent)}
            >
              {t("chat_write_allow_always")}
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => onWriteApprovalDecision("deny", toolEvent)}
            >
              {t("chat_write_deny")}
            </Button>
          </div>
        ) : toolEvent.decision ? (
          <p className="text-muted-foreground text-xs">
            {toolEvent.decision === "once"
              ? t("chat_write_decision_once")
              : toolEvent.decision === "session"
                ? t("chat_write_decision_session")
                : toolEvent.decision === "always"
                  ? t("chat_write_decision_always")
                  : t("chat_write_decision_deny")}
          </p>
        ) : null}
      </div>
    )
  }
  if (toolEvent.type === "code_execution") {
    const outputFiles = (toolEvent.output?.output_files ?? []).filter(
      (file): file is NonNullable<typeof file> =>
        Boolean(file?.file_name && file.data_base64)
    )
    return (
      <div className="space-y-3">
        <div>
          <p className="opacity-70 mb-1 text-xs uppercase">{t("chat_execution_code")}</p>
          <pre className="bg-background/40 p-2 rounded overflow-x-auto text-xs">
            {toolEvent.code ?? ""}
          </pre>
        </div>
        <div>
          <p className="opacity-70 mb-1 text-xs uppercase">{t("chat_execution_output")}</p>
          <pre className="bg-background/40 p-2 rounded overflow-x-auto text-xs">
            {[
              toolEvent.output?.stdout,
              toolEvent.output?.stderr,
              toolEvent.output?.error
                ? `${t("common_error")}: ${toolEvent.output.error}`
                : null,
              toolEvent.output?.timed_out ? t("chat_execution_timed_out") : null,
              typeof toolEvent.output?.exit_code === "number"
                ? t("chat_execution_exit_code", {
                    code: toolEvent.output.exit_code,
                  })
                : null,
            ]
              .filter(Boolean)
              .join("\n") || t("chat_execution_no_output")}
          </pre>
        </div>
        {outputFiles.length > 0 ? (
          <div>
            <p className="opacity-70 mb-1 text-xs uppercase">{t("chat_generated_files")}</p>
            <div className="flex flex-wrap gap-2">
              {outputFiles.map((file, index) => (
                <a
                  key={`${file.file_name}-${index}`}
                  className="inline-flex items-center gap-1.5 hover:bg-muted px-3 py-2 border rounded-md text-xs"
                  href={attachmentDownloadSrc(file)}
                  download={file.file_name}
                >
                  <Download aria-hidden="true" className="opacity-70 size-3.5 shrink-0" />
                  {file.file_name}
                </a>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    )
  }
  if (toolEvent.type === "subagent") {
    const status = toolEvent.status || toolEvent.output?.status || "running"
    const childId = toolEvent.child_chat_id || toolEvent.output?.child_chat_id
    const title = toolEvent.title || toolEvent.output?.title || t("chat_subagent_default_title")
    const summary = toolEvent.summary || toolEvent.output?.summary
    const mode = toolEvent.mode || toolEvent.output?.mode || "blocking"
    return (
      <div className="space-y-2 text-xs">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium text-sm text-foreground">{title}</span>
          <span className="rounded-md border px-1.5 py-0.5 text-[10px] uppercase opacity-70">
            {mode}
          </span>
          <span className="rounded-md border px-1.5 py-0.5 text-[10px] uppercase opacity-70">
            {status}
          </span>
        </div>
        {summary ? (
          <p className="text-muted-foreground whitespace-pre-wrap wrap-break-word line-clamp-6">
            {summary}
          </p>
        ) : toolEvent.prompt_preview ? (
          <p className="text-muted-foreground whitespace-pre-wrap wrap-break-word line-clamp-4">
            {toolEvent.prompt_preview}
          </p>
        ) : null}
        {toolEvent.output?.error ? (
          <p className="text-destructive/90">
            {t("common_error")}: {toolEvent.output.error}
          </p>
        ) : null}
        {childId ? (
          <button
            type="button"
            className="inline-flex items-center gap-1.5 text-primary hover:underline"
            onClick={() =>
              void navigate({
                to: "/chat/{-$chatId}",
                params: { chatId: childId },
              })
            }
          >
            {t("chat_subagent_open")}
          </button>
        ) : (
          <p className="text-muted-foreground opacity-80">{t("chat_subagent_opening_soon")}</p>
        )}
      </div>
    )
  }
  if (toolEvent.type === "tool_call") {
    const rawPreview = toolEvent.input_preview?.trim() ?? ""
    let preview = rawPreview
    if (rawPreview.startsWith("{") || rawPreview.startsWith("[")) {
      try {
        preview = JSON.stringify(JSON.parse(rawPreview), null, 2)
      } catch {
        preview = rawPreview
      }
    }
    return (
      <div className="space-y-1 py-1 text-xs">
        <div className="opacity-80 whitespace-pre-wrap wrap-break-word">
          {preview || t("chat_running_tool_call")}
        </div>
        {toolEvent.output?.result_preview ? (
          <div className="text-muted-foreground">{toolEvent.output.result_preview}</div>
        ) : null}
        {toolEvent.output?.error ? (
          <div className="text-destructive/90">
            {t("common_error")}: {toolEvent.output.error}
          </div>
        ) : null}
        {toolEvent.output?.raw_output ? (
          <details className="pt-1">
            <summary className="opacity-80 cursor-pointer">{t("chat_result")}</summary>
            <pre className="bg-background/40 mt-1 p-2 rounded overflow-x-auto text-[11px] whitespace-pre-wrap">
              {JSON.stringify(toolEvent.output.raw_output, null, 2)}
            </pre>
          </details>
        ) : null}
      </div>
    )
  }
  if (toolEvent.type === "reasoning") {
    return (
      <div className="chat-markdown max-w-none text-muted-foreground text-xs [&_p]:my-1.5 [&_p]:leading-5 [&_ul]:my-1.5 [&_ol]:my-1.5 [&_li]:my-0.5 [&_strong]:text-foreground/80 [&_h1]:mt-2 [&_h1]:mb-1 [&_h1]:font-medium [&_h1]:text-sm [&_h2]:mt-2 [&_h2]:mb-1 [&_h2]:font-medium [&_h2]:text-sm [&_h3]:mt-2 [&_h3]:mb-1 [&_h3]:font-medium [&_h3]:text-xs">
        <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]}>
          {toolEvent.content}
        </ReactMarkdown>
      </div>
    )
  }
  if (toolEvent.type === "url_attachments") {
    return (
      <div className="space-y-3">
        <div>
          <p className="opacity-70 mb-1 text-xs uppercase">{t("chat_sources")}</p>
          <pre className="bg-background/40 p-2 rounded overflow-x-auto text-xs whitespace-pre-wrap">
            {(toolEvent.urls ?? []).join("\n") || t("chat_no_urls_provided")}
          </pre>
        </div>
        <div>
          <p className="opacity-70 mb-1 text-xs uppercase">{t("chat_result")}</p>
          <pre className="bg-background/40 p-2 rounded overflow-x-auto text-xs whitespace-pre-wrap">
            {toolEvent.output?.error
              ? `${t("common_error")}: ${toolEvent.output.error}`
              : (toolEvent.output?.results ?? [])
                  .map((row) =>
                    row.error
                      ? `- ${row.url ?? t("chat_unknown")}: ${t("common_error")} ${row.error}`
                      : `- ${row.file_name ?? t("chat_file")} (${row.content_type ?? t("chat_unknown")}, ${row.size_bytes ?? 0} ${t("chat_bytes_unit")})`
                  )
                  .join("\n") || t("chat_waiting_for_download")}
          </pre>
        </div>
      </div>
    )
  }
  if (toolEvent.type === "context_summary") {
    return (
      <div>
        <p className="opacity-70 mb-1 text-xs uppercase">{t("chat_summary")}</p>
        <pre className="bg-background/40 p-2 rounded overflow-x-auto text-xs whitespace-pre-wrap">
          {toolEvent.summary ?? ""}
        </pre>
      </div>
    )
  }
  return null
}

const CopyTextButton = ({
  text,
  getText,
  label,
  copiedLabel,
  className = "",
  iconOnly = false,
}: {
  text?: string
  getText?: () => string
  label: string
  copiedLabel: string
  className?: string
  iconOnly?: boolean
}) => {
  const [copied, setCopied] = useState(false)
  const resetTimeoutRef = useRef<number | null>(null)

  useEffect(() => {
    return () => {
      if (resetTimeoutRef.current != null) {
        window.clearTimeout(resetTimeoutRef.current)
      }
    }
  }, [])

  const handleCopy = async () => {
    try {
      await copyToClipboard(getText?.() ?? text ?? "")
      setCopied(true)
      if (resetTimeoutRef.current != null) {
        window.clearTimeout(resetTimeoutRef.current)
      }
      resetTimeoutRef.current = window.setTimeout(() => {
        setCopied(false)
        resetTimeoutRef.current = null
      }, 1600)
    } catch {
      // Keep the idle label if clipboard access fails.
    }
  }

  return (
    <Button
      type="button"
      variant="ghost"
      size={iconOnly ? "icon" : "sm"}
      className={className}
      onClick={() => void handleCopy()}
      aria-label={copied ? copiedLabel : label}
    >
      {iconOnly ? (
        copied ? (
          <Check
            aria-hidden="true"
            className="w-3.5 h-3.5 animate-in duration-200 fade-in zoom-in-95"
          />
        ) : (
          <Copy aria-hidden="true" className="w-3.5 h-3.5" />
        )
      ) : (
        <span
          key={copied ? "copied" : "idle"}
          className="inline-block animate-in duration-200 fade-in zoom-in-95"
        >
          {copied ? copiedLabel : label}
        </span>
      )}
    </Button>
  )
}

const MarkdownTable = ({
  children,
  copyLabel,
  copiedLabel,
  ...rest
}: {
  children?: ReactNode
  copyLabel: string
  copiedLabel: string
} & HTMLAttributes<HTMLTableElement>) => {
  const tableRef = useRef<HTMLTableElement>(null)

  return (
    <div className="my-3">
      <div className="mb-1 flex justify-end">
        <CopyTextButton
          getText={() => tableToTsv(tableRef.current)}
          label={copyLabel}
          copiedLabel={copiedLabel}
          className={tableCopyButtonClassName}
        />
      </div>
      <div className="overflow-x-auto">
        <table
          ref={tableRef}
          className="w-full text-sm border-collapse"
          {...rest}
        >
          {children}
        </table>
      </div>
    </div>
  )
}

const HighlightedCodeBlock = ({
  code,
  language,
  codeTheme,
  copyLabel,
  copiedLabel,
  restProps,
}: {
  code: string
  language: string
  codeTheme: Record<string, CSSProperties>
  copyLabel: string
  copiedLabel: string
  restProps: Record<string, unknown>
}) => {
  const [highlight, setHighlight] = useState(false)

  useEffect(() => {
    let cancelled = false
    const cancel = scheduleIdle(() => {
      if (!cancelled) setHighlight(true)
    })
    return () => {
      cancelled = true
      cancel()
    }
  }, [code, language])

  return (
    <div className={codeBlockClassName}>
      <div className={codeCopyStickyClassName}>
        <CopyTextButton
          text={code}
          label={copyLabel}
          copiedLabel={copiedLabel}
          className={codeCopyButtonClassName}
        />
      </div>
      {highlight ? (
        <SyntaxHighlighter
          {...restProps}
          style={codeTheme}
          language={language}
          PreTag="div"
          customStyle={{
            margin: 0,
            padding: "1rem",
            paddingTop: "2.5rem",
            background: "transparent",
          }}
          codeTagProps={{
            className: "font-mono text-[13px]",
          }}
        >
          {code}
        </SyntaxHighlighter>
      ) : (
        <pre className="m-0 p-4 pt-10 overflow-x-auto text-[13px] whitespace-pre-wrap">
          <code className={`language-${language} font-mono`}>{code}</code>
        </pre>
      )}
    </div>
  )
}

const DeferredMarkdown = ({
  markdown,
  urgent,
  components,
}: {
  markdown: string
  urgent: boolean
  components: ComponentProps<typeof ReactMarkdown>["components"]
}) => {
  const [ready, setReady] = useState(urgent)
  const normalized = useMemo(
    () => completeStreamingMarkdownTables(normalizeMathContent(markdown)),
    [markdown]
  )

  useEffect(() => {
    if (urgent) {
      setReady(true)
      return
    }
    // Keep an already-rendered tree; flashing raw pipes after stream-end looks broken.
    if (ready) return
    const cancel = scheduleIdle(() => setReady(true), 64)
    return cancel
  }, [markdown, urgent, ready])

  if (!ready) {
    return (
      <div className="text-inherit wrap-break-word whitespace-pre-wrap">
        {markdown}
      </div>
    )
  }

  return (
    <div className="chat-markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkBreaks, [remarkMath, { singleDollarTextMath: false }]]}
        rehypePlugins={[rehypeKatex]}
        components={components}
      >
        {normalized}
      </ReactMarkdown>
    </div>
  )
}

const ThinkingTrail = ({
  open,
  onOpenChange,
  title,
  leading,
  expandable = true,
  children,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  leading?: ReactNode
  expandable?: boolean
  children?: ReactNode
}) => (
  <details
    className="group/thinking text-muted-foreground text-xs"
    open={expandable ? open : false}
    onToggle={(event) => {
      if (!expandable) {
        event.preventDefault()
        event.currentTarget.open = false
        return
      }
      const nextOpen = event.currentTarget.open
      if (nextOpen !== open) onOpenChange(nextOpen)
    }}
  >
    <summary
      className={cn(
        "[&::-webkit-details-marker]:hidden inline-flex items-center gap-1.5 py-0.5 font-medium text-muted-foreground text-xs leading-5 list-none select-none",
        expandable ? "cursor-pointer" : "cursor-default"
      )}
    >
      {expandable ? (
        <span
          aria-hidden="true"
          className="inline-block opacity-50 transition-transform group-open/thinking:rotate-90 select-none"
        >
          ›
        </span>
      ) : null}
      {leading}
      <span className="tabular-nums">{title}</span>
    </summary>
    {expandable && children ? (
      <div className="space-y-2 mt-2 ml-3">{children}</div>
    ) : null}
  </details>
)

const MessageBubbleComponent = ({
  msg,
  isUser,
  isCodeEvent,
  isThinking,
  thinkingLabels,
  currentStepLabel,
  currentToolLabel,
  actionInfoLevel,
  actionsEnabled,
  ttsStatus = null,
  isEditing,
  isEditDragActive,
  editingAttachments,
  editAttachmentError,
  codeTheme,
  t,
  onOpenSources,
  exportQuestion = null,
  onStartEdit,
  onDeleteFromMessage,
  onRetryMessage,
  onShareMessage,
  onForkChat,
  onSaveEditedMessage,
  onCancelEdit,
  onEditPasteAttachments,
  onEditFilesSelected,
  onEditDragEnter,
  onEditDragOver,
  onEditDragLeave,
  onEditDrop,
  onRemoveEditingAttachment,
  onPreviewAttachment,
  onWriteApprovalDecision,
  onSourceProposalDecision,
}: MessageBubbleProps) => {
  const editFileInputRef = useRef<HTMLInputElement | null>(null)
  const editTextareaRef = useRef<HTMLTextAreaElement | null>(null)
  const thinkingStartedAtRef = useRef<number | null>(null)
  const [hasEditText, setHasEditText] = useState(() => Boolean(msg.content.trim()))
  const [thinkingOpen, setThinkingOpen] = useState(false)
  const [thinkingElapsedMs, setThinkingElapsedMs] = useState(0)
  const [thoughtDurationMs, setThoughtDurationMs] = useState<number | null>(null)
  const { appTitle: brandingTitle, logoUrl } = useBranding()
  const { data: currentUser } = useMe()
  const currentUserId = currentUser?.id
  useEffect(() => {
    if (!isEditing) return
    setHasEditText(Boolean(msg.content.trim()))
  }, [isEditing, msg.id, msg.content])
  const isContextSummaryEvent = msg.tool_event?.type === "context_summary"
  const codeEvent = msg.tool_event?.type === "code_execution" ? msg.tool_event : null
  const writeApprovalEvent =
    msg.tool_event?.type === "write_approval" ? msg.tool_event : null
  const sourceProposalEvent =
    msg.tool_event?.type === "source_proposal" ? msg.tool_event : null
  const toolCallEvent = msg.tool_event?.type === "tool_call" ? msg.tool_event : null
  const chatViewEvent = msg.activity_event?.type === "chat_view" ? msg.activity_event : null
  const urlAttachmentsEvent =
    msg.tool_event?.type === "url_attachments" ? msg.tool_event : null
  const contextSummaryEvent =
    msg.tool_event?.type === "context_summary" ? msg.tool_event : null
  const writeApprovalCards = (() => {
    const byId = new Map<
      string,
      Extract<NonNullable<ChatMessage["tool_event"]>, { type: "write_approval" }>
    >()
    const add = (
      event: Extract<NonNullable<ChatMessage["tool_event"]>, { type: "write_approval" }> | null
    ) => {
      if (!event) return
      const key = event.id ?? event.request_id ?? `anon-${byId.size}`
      const prev = byId.get(key)
      if (!prev || event.status === "resolved") byId.set(key, event)
    }
    add(writeApprovalEvent)
    for (const part of msg.stream_parts ?? []) {
      if (part.type === "action" && part.tool_event?.type === "write_approval") {
        add(part.tool_event)
      }
    }
    return Array.from(byId.values())
  })()
  const sourceProposalCards = (() => {
    const byId = new Map<
      string,
      Extract<NonNullable<ChatMessage["tool_event"]>, { type: "source_proposal" }>
    >()
    const add = (
      event: Extract<NonNullable<ChatMessage["tool_event"]>, { type: "source_proposal" }> | null
    ) => {
      if (!event) return
      const key = event.id ?? `anon-${byId.size}`
      const prev = byId.get(key)
      if (!prev || (event.status && event.status !== "pending")) byId.set(key, event)
    }
    add(sourceProposalEvent)
    for (const part of msg.stream_parts ?? []) {
      if (part.type === "action" && part.tool_event?.type === "source_proposal") {
        add(part.tool_event)
      }
    }
    return Array.from(byId.values())
  })()
  const hasEventHeader = Boolean(
    isCodeEvent ||
      toolCallEvent ||
      chatViewEvent ||
      urlAttachmentsEvent ||
      isContextSummaryEvent ||
      writeApprovalEvent ||
      sourceProposalEvent
  )
  const finalAnswerText = useMemo(() => getFinalAnswerText(msg), [msg])
  const speakText = useMemo(() => plainTextForTts(finalAnswerText), [finalAnswerText])
  const canCopyMessage = Boolean(
    isUser ? msg.content.trim() : !isThinking && finalAnswerText
  )
  const uniqueSources = msg.sources?.length ? dedupeSources(msg.sources) : []
  const content = useMemo(() => normalizeMathContent(msg.content), [msg.content])
  const streamParts = msg.stream_parts ?? []
  const streamTextLength = streamParts.reduce(
    (total, part) => total + (part.type === "text" ? part.text.length : 0),
    0
  )
  const hasActionParts = streamParts.some((part) => part.type === "action")
  // Prefer the interleaved timeline when it has actions or covers persisted text.
  // Partial text-only timelines still fall back so refresh races don't hide history.
  const hasStreamParts =
    streamParts.length > 0 &&
    (hasActionParts ||
      content.trim().length === 0 ||
      streamTextLength >= content.trim().length)
  const { thoughtParts, finalTextParts } = useMemo(
    () => splitAssistantStreamParts(streamParts, isThinking),
    [streamParts, isThinking]
  )
  const hasFinalAnswerText = finalTextParts.some((part) => part.text.trim().length > 0)
  const showThinkingDetails =
    actionInfoLevel === "detailed" &&
    (thoughtParts.length > 0 ||
      thinkingLabels.length > 0 ||
      Boolean(currentStepLabel || currentToolLabel))
  // Fruit + timer stay visible even when action details are hidden in settings.
  const isThoughtClockRunning = !isUser && isThinking
  const showThinkingHeader =
    !isUser && (isThoughtClockRunning || showThinkingDetails)

  // One lifecycle for the clock: msg.id remounts/resets must not leave the
  // interval running against a nulled start ref (that froze the UI at 00:00).
  useEffect(() => {
    thinkingStartedAtRef.current = null
    setThinkingElapsedMs(0)
    setThoughtDurationMs(null)
    setThinkingOpen(false)
  }, [msg.id])

  useEffect(() => {
    if (!isThoughtClockRunning) {
      if (thinkingStartedAtRef.current != null) {
        setThoughtDurationMs(Math.max(0, Date.now() - thinkingStartedAtRef.current))
      }
      return
    }
    if (thinkingStartedAtRef.current == null) {
      thinkingStartedAtRef.current = resolveThoughtStartMs(msg)
    }
    setThoughtDurationMs(null)
    const tick = () => {
      if (thinkingStartedAtRef.current == null) {
        thinkingStartedAtRef.current = resolveThoughtStartMs(msg)
      }
      setThinkingElapsedMs(Date.now() - thinkingStartedAtRef.current)
    }
    tick()
    const id = window.setInterval(tick, 250)
    return () => window.clearInterval(id)
  }, [isThoughtClockRunning, msg.id, msg.started_at, msg.created_at])

  useEffect(() => {
    if (isThinking) {
      setThinkingOpen(true)
      return
    }
    if (hasFinalAnswerText) setThinkingOpen(false)
  }, [isThinking, hasFinalAnswerText])

  const thinkingActivityHint = useMemo(() => {
    const raw =
      currentToolLabel ??
      (thinkingLabels.length > 0 ? thinkingLabels[thinkingLabels.length - 1] : null)
    return compactThinkingActivity(raw)
  }, [currentToolLabel, thinkingLabels])

  const thinkingTrailTitle = (() => {
    const timeRunning = formatThinkingClock(thinkingElapsedMs)
    if (isThoughtClockRunning) {
      return thinkingActivityHint
        ? t("chat_thinking_timer_activity", {
            time: timeRunning,
            activity: thinkingActivityHint,
          })
        : t("chat_thinking_timer", { time: timeRunning })
    }
    const doneMs =
      thoughtDurationMs ??
      (thinkingElapsedMs > 0 ? thinkingElapsedMs : null) ??
      resolvePersistedThoughtMs(msg)
    if (doneMs != null && doneMs > 0) {
      const timeDone = formatThinkingClock(doneMs)
      return thinkingActivityHint
        ? t("chat_thought_for_activity", {
            time: timeDone,
            activity: thinkingActivityHint,
          })
        : t("chat_thought_for", { time: timeDone })
    }
    return t("chat_thinking")
  })()

  const markdownComponents = useMemo(
    () => ({
      p({ children, node, ...rest }: any) {
        void node
        return (
          <p
            className={
              isUser
                ? "my-2 first:mt-0 last:mb-0 leading-6"
                : "my-3 leading-7"
            }
            {...rest}
          >
            {children}
          </p>
        )
      },
      ul({ children, node, ...rest }: any) {
        void node
        return (
          <ul className="my-3 space-y-2.5" {...rest}>
            {children}
          </ul>
        )
      },
      ol({ children, node, ...rest }: any) {
        void node
        return (
          <ol className="my-3 space-y-2.5" {...rest}>
            {children}
          </ol>
        )
      },
      li({ children, node, ...rest }: any) {
        void node
        return (
          <li className="leading-7" {...rest}>
            {children}
          </li>
        )
      },
      a({ children, node, href, ...rest }: any) {
        void node
        if (isFakeCoworkDocumentHref(href)) {
          return <span className="font-medium">{children}</span>
        }
        return (
          <a
            {...rest}
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            className="underline underline-offset-2 break-all"
          >
            {children}
          </a>
        )
      },
      hr({ node, ...rest }: any) {
        void node
        return <hr className="my-3 border-muted-foreground/30" {...rest} />
      },
      strong({ children, node, ...rest }: any) {
        void node
        return (
          <strong {...rest} className="font-semibold">
            {children}
          </strong>
        )
      },
      b({ children, node, ...rest }: any) {
        void node
        return (
          <b {...rest} className="font-semibold">
            {children}
          </b>
        )
      },
      h1({ children, node, ...rest }: any) {
        void node
        return (
          <h1 className="mt-6 mb-3 font-bold text-xl" {...rest}>
            {children}
          </h1>
        )
      },
      h2({ children, node, ...rest }: any) {
        void node
        return (
          <h2 className="mt-5 mb-3 font-bold text-lg" {...rest}>
            {children}
          </h2>
        )
      },
      h3({ children, node, ...rest }: any) {
        void node
        return (
          <h3 className="mt-5 mb-2 font-semibold text-base" {...rest}>
            {children}
          </h3>
        )
      },
      h4({ children, node, ...rest }: any) {
        void node
        return (
          <h4 className="mt-4 mb-2 font-semibold text-base" {...rest}>
            {children}
          </h4>
        )
      },
      h5({ children, node, ...rest }: any) {
        void node
        return (
          <h5 className="mt-3 mb-2 font-semibold text-sm" {...rest}>
            {children}
          </h5>
        )
      },
      h6({ children, node, ...rest }: any) {
        void node
        return (
          <h6 className="mt-3 mb-2 font-semibold text-sm" {...rest}>
            {children}
          </h6>
        )
      },
      table({ children, node, ...rest }: any) {
        void node
        return (
          <MarkdownTable
            copyLabel={t("common_copy")}
            copiedLabel={t("common_copied")}
            {...rest}
          >
            {children}
          </MarkdownTable>
        )
      },
      thead({ children, node, ...rest }: any) {
        void node
        return (
          <thead className="border-border border-b" {...rest}>
            {children}
          </thead>
        )
      },
      th({ children, node, ...rest }: any) {
        void node
        return (
          <th className="px-3 py-2.5 font-semibold text-left" {...rest}>
            {children}
          </th>
        )
      },
      td({ children, node, ...rest }: any) {
        void node
        return (
          <td className="px-3 py-2.5 align-top" {...rest}>
            {children}
          </td>
        )
      },
      tr({ children, node, ...rest }: any) {
        void node
        return (
          <tr className="border-border border-b even:bg-muted/30" {...rest}>
            {children}
          </tr>
        )
      },
      code(props: any) {
        const { className, children, ref: refProp, ...rest } = props
        void refProp
        const match = /language-(\w+)/.exec(className || "")
        // Never String(children): arrays join with "," and corrupt Mermaid source.
        const codeContent = (
          Array.isArray(children)
            ? children.map((child) => (typeof child === "string" ? child : "")).join("")
            : typeof children === "string"
              ? children
              : String(children ?? "")
        ).replace(/\n$/, "")
        const mermaidChart = isBlockCode(codeContent, className)
          ? toMermaidChart(codeContent, match?.[1] ?? null)
          : null
        if (mermaidChart) {
          return <MermaidDiagram chart={mermaidChart} />
        }
        if (isBlockCode(codeContent, className)) {
          if (match) {
            return (
              <HighlightedCodeBlock
                code={codeContent}
                language={match[1]}
                codeTheme={codeTheme}
                copyLabel={t("chat_copy_code")}
                copiedLabel={t("common_copied")}
                restProps={rest}
              />
            )
          }
          return (
            <div className={codeBlockClassName}>
              <div className={codeCopyStickyClassName}>
                <CopyTextButton
                  text={codeContent}
                  label={t("chat_copy_code")}
                  copiedLabel={t("common_copied")}
                  className={codeCopyButtonClassName}
                />
              </div>
              <pre className="m-0 p-4 pt-10 overflow-x-auto text-[13px] whitespace-pre-wrap">
                <code className={className} {...rest}>
                  {codeContent}
                </code>
              </pre>
            </div>
          )
        }
        return (
          <code className={className} {...rest}>
            {children}
          </code>
        )
      },
    }),
    [codeTheme, isUser, t]
  )
  const isLiveGeneration =
    msg.generation_status === "queued" ||
    msg.generation_status === "running" ||
    msg.generation_status === "streaming"

  const renderMarkdown = (markdown: string, key?: string) => (
    <DeferredMarkdown
      key={key}
      markdown={isUser ? preserveMarkdownBlankLines(markdown) : markdown}
      urgent={isLiveGeneration}
      components={markdownComponents}
    />
  )
  const attachmentSrc = (attachment: DownloadableAttachment) => attachmentDownloadSrc(attachment)
  const attachmentHref = (attachment: DownloadableAttachment) => attachmentDownloadSrc(attachment)
  // User uploads live on msg.attachments; assistant "generated files" strip is separate.
  const userAttachments = useMemo(
    () =>
      isUser
        ? (msg.attachments ?? []).filter((attachment) =>
            Boolean(attachment.file_name?.trim())
          )
        : [],
    [isUser, msg.attachments]
  )
  const generatedFiles = useMemo(
    () => (!isUser ? collectDownloadableAttachments(msg) : []),
    [isUser, msg]
  )

  const handleEditPickFiles = () => {
    editFileInputRef.current?.click()
  }

  const handleEditFilesSelected = (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? [])
    if (files.length === 0) return
    onEditFilesSelected(files)
    event.target.value = ""
  }
  const canSaveEdit = hasEditText || editingAttachments.length > 0
  const saveEdited = () => {
    onSaveEditedMessage(msg, editTextareaRef.current?.value ?? msg.content)
  }
  const showUserSideActions =
    isUser && !isEditing && (canCopyMessage || actionsEnabled)
  const canSpeakMessage =
    canUseTtsPlayback(ttsStatus) &&
    Boolean(speakText) &&
    !isUser &&
    !isEditing &&
    !isThinking
  const showAssistantAnswerActions = !isThinking
  const showAssistantFooter =
    !isUser &&
    !isEditing &&
    (canCopyMessage ||
      canSpeakMessage ||
      uniqueSources.length > 0 ||
      Boolean(msg.model_name) ||
      (actionInfoLevel === "detailed" &&
        ((msg.usage?.total_tokens ?? 0) > 0 ||
          resolveTotalTimeMs(msg.usage) != null)) ||
      (!isThinking && Boolean(onForkChat)) ||
      (actionsEnabled && msg.generation_status === "failed"))

  const authorLabel =
    isUser && msg.user_display_name && msg.user_id && msg.user_id !== currentUserId
      ? msg.user_display_name
      : null
  const canManageMessage =
    actionsEnabled &&
    (!msg.user_id || !currentUserId || msg.user_id === currentUserId)

  return (
    <div
      className={`mx-auto flex w-full min-w-0 max-w-(--chat-content-width) ${
        isUser ? "justify-end" : "justify-start"
      }`}
    >
      <div className={`group relative min-w-0 ${isEditing || !isUser ? "w-full" : "max-w-[85%]"}`}>
        {authorLabel ? (
          <p className="mb-1 text-right text-xs text-muted-foreground">{authorLabel}</p>
        ) : null}
        <div
          className={`min-w-0 rounded-lg wrap-break-word whitespace-normal ${
            isUser
              ? "overflow-clip bg-secondary p-2 text-base leading-6 text-foreground"
              : hasEventHeader
                ? "bg-transparent p-2 text-base leading-7 text-foreground"
                : "bg-transparent p-0 text-base leading-7 text-foreground"
          }`}
        >
          {hasEventHeader ? (
            <div className="flex justify-between items-center gap-2">
              <p className="opacity-70 mb-1.5 font-medium text-xs">
                {isCodeEvent
                  ? t("chat_executing_code")
                  : writeApprovalEvent
                    ? t("chat_write_approval")
                    : sourceProposalEvent
                      ? t("chat_source_proposal")
                      : toolCallEvent
                        ? t("chat_tool_label", { name: toolCallEvent.tool_name })
                        : chatViewEvent
                          ? t("chat_activity")
                          : urlAttachmentsEvent
                            ? t("chat_downloading_attachments")
                            : t("chat_context_summarized")}
              </p>
            </div>
          ) : null}
          {isCodeEvent ? (
            <details className="space-y-3">
              <summary className="text-xs uppercase tracking-wide cursor-pointer">
                {t("chat_execution_details")}
              </summary>
              {codeEvent ? (
                <ToolEventDetails
                  toolEvent={codeEvent}
                  t={t}
                  onWriteApprovalDecision={onWriteApprovalDecision}
                  onSourceProposalDecision={onSourceProposalDecision}
                />
              ) : null}
            </details>
          ) : writeApprovalEvent ? (
            <ToolEventDetails
              toolEvent={writeApprovalEvent}
              t={t}
              onWriteApprovalDecision={onWriteApprovalDecision}
              onSourceProposalDecision={onSourceProposalDecision}
            />
          ) : sourceProposalEvent ? (
            <ToolEventDetails
              toolEvent={sourceProposalEvent}
              t={t}
              onWriteApprovalDecision={onWriteApprovalDecision}
              onSourceProposalDecision={onSourceProposalDecision}
            />
          ) : toolCallEvent ? (
            <ToolEventDetails
              toolEvent={toolCallEvent}
              t={t}
              onWriteApprovalDecision={onWriteApprovalDecision}
              onSourceProposalDecision={onSourceProposalDecision}
            />
          ) : chatViewEvent ? (
            <div className="space-y-1 py-1 text-xs">
              {(chatViewEvent.opens ?? []).map((open, index) => {
                const openedAt =
                  open.opened_at && !Number.isNaN(new Date(open.opened_at).getTime())
                    ? new Date(open.opened_at).toLocaleString()
                    : null
                return (
                  <div key={`view-open-${index}`} className="opacity-80">
                    {t("chat_viewed_the_chat", {
                      user: open.viewer || t("chat_anonymous_user"),
                    })}
                    {openedAt ? ` (${openedAt})` : ""}
                  </div>
                )
              })}
              {(chatViewEvent.opens ?? []).length === 0 ? (
                <div className="opacity-80">{t("chat_chat_viewed")}</div>
              ) : null}
            </div>
          ) : urlAttachmentsEvent ? (
            <details className="space-y-3">
              <summary className="text-xs uppercase tracking-wide cursor-pointer">
                {t("chat_downloaded_attachments")}
              </summary>
              <ToolEventDetails
                toolEvent={urlAttachmentsEvent}
                t={t}
                onWriteApprovalDecision={onWriteApprovalDecision}
                onSourceProposalDecision={onSourceProposalDecision}
              />
            </details>
          ) : isContextSummaryEvent ? (
            <details className="space-y-3">
              <summary className="text-xs uppercase tracking-wide cursor-pointer">
                {t("chat_context_summarized")}
              </summary>
              {contextSummaryEvent ? (
                <ToolEventDetails
                  toolEvent={contextSummaryEvent}
                  t={t}
                  onWriteApprovalDecision={onWriteApprovalDecision}
                  onSourceProposalDecision={onSourceProposalDecision}
                />
              ) : null}
            </details>
          ) : isEditing ? (
            <div
              className={`space-y-2 rounded-md ${
                isEditDragActive
                  ? "ring-2 ring-primary/40 ring-offset-2 ring-offset-background"
                  : ""
              }`}
              onDragEnter={onEditDragEnter}
              onDragOver={onEditDragOver}
              onDragLeave={onEditDragLeave}
              onDrop={onEditDrop}
            >
              <Textarea
                key={`${msg.id}-edit`}
                ref={editTextareaRef}
                defaultValue={msg.content}
                onChange={(event) => {
                  const nextHasText = event.target.value.trim().length > 0
                  setHasEditText((prev) => (prev === nextHasText ? prev : nextHasText))
                }}
                onPaste={(event) => {
                  onEditPasteAttachments(event)
                  requestAnimationFrame(() => {
                    const nextHasText =
                      (editTextareaRef.current?.value ?? "").trim().length > 0
                    setHasEditText((prev) => (prev === nextHasText ? prev : nextHasText))
                  })
                }}
                onKeyDown={(event) => {
                  if (!shouldSubmitOnEnter(event)) return
                  event.preventDefault()
                  if (canSaveEdit) {
                    saveEdited()
                  }
                }}
                rows={3}
                className="bg-muted min-h-32 max-h-[calc(100svh-12rem)] overflow-y-auto text-foreground"
              />
              {editAttachmentError ? (
                <p className="text-destructive text-sm" role="alert">
                  {editAttachmentError}
                </p>
              ) : null}
              {editingAttachments.length > 0 ? (
                <div className="flex flex-wrap gap-2">
                  {editingAttachments.map((attachment, index) => {
                    const isImage = (attachment.content_type ?? "").startsWith("image/")
                    return (
                      <div key={`${attachment.file_name}-${index}`} className="relative">
                        {isImage ? (
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            className="p-0 rounded-md w-auto h-auto overflow-hidden"
                            onClick={() => onPreviewAttachment(attachment)}
                          >
                            <img
                              src={attachmentSrc(attachment)}
                              alt={attachment.file_name}
                              className="rounded-md w-16 h-16 object-cover"
                            />
                          </Button>
                        ) : (
                          <div className="px-3 py-2 border rounded-md text-xs">
                            {attachment.file_name}
                          </div>
                        )}
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="-top-2 -right-2 absolute bg-background shadow rounded-full w-8 h-8"
                          onClick={() => onRemoveEditingAttachment(index)}
                          aria-label={t("common_delete")}
                        >
                          <X aria-hidden="true" className="w-3 h-3" />
                        </Button>
                      </div>
                    )
                  })}
                </div>
              ) : null}
              <div className="flex justify-between items-center">
                <div className="flex items-center gap-2">
                  <input
                    ref={editFileInputRef}
                    type="file"
                    multiple
                    className="hidden"
                    onChange={handleEditFilesSelected}
                  />
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={handleEditPickFiles}
                    aria-label={t("chat_add_files")}
                  >
                    <Plus aria-hidden="true" className="w-5 h-5" />
                  </Button>
                </div>
              </div>
              <div className="flex gap-2">
                <Button size="sm" onClick={saveEdited} disabled={!canSaveEdit}>
                  {t("chat_resend")}
                </Button>
                <Button size="sm" variant="outline" onClick={onCancelEdit}>
                  {t("chat_cancel")}
                </Button>
              </div>
            </div>
          ) : (
            <>
              <div className="space-y-2">
                {showThinkingHeader ? (
                  <ThinkingTrail
                    open={thinkingOpen}
                    onOpenChange={setThinkingOpen}
                    title={thinkingTrailTitle}
                    expandable={showThinkingDetails}
                    leading={
                      isThoughtClockRunning ? (
                        <ThinkingFruitRive
                          // Survive temp→server id remaps while the same turn is running.
                          key={msg.task_id ?? msg.id}
                          className="size-4"
                        />
                      ) : null
                    }
                  >
                    {showThinkingDetails ? (
                      hasStreamParts ? (
                        thoughtParts.map((part, index) => {
                          if (part.type === "text") {
                            if (!part.text.trim()) return null
                            return (
                              <div
                                key={`thought-text-${index}`}
                                className="text-muted-foreground text-xs leading-5"
                              >
                                {renderMarkdown(part.text, `thought-text-${index}`)}
                              </div>
                            )
                          }
                          const actionAttachments = part.attachments ?? []
                          const toolEvent = part.tool_event
                          if (toolEvent?.type === "write_approval") {
                            return null
                          }
                          const attachmentRow =
                            actionAttachments.length > 0 ? (
                              <div className="flex flex-wrap gap-2 pl-3">
                                {actionAttachments.map((attachment, attachmentIndex) => {
                                  const isImage = (attachment.content_type ?? "").startsWith(
                                    "image/"
                                  )
                                  if (isImage) {
                                    return (
                                      <Button
                                        key={`${attachment.file_name}-${attachmentIndex}`}
                                        type="button"
                                        variant="ghost"
                                        size="icon"
                                        className="p-0 rounded-md w-auto h-auto overflow-hidden"
                                        onClick={() =>
                                          onPreviewAttachment({
                                            file_name: attachment.file_name,
                                            content_type: attachment.content_type,
                                            data_base64: attachment.data_base64,
                                            content_url: attachment.content_url,
                                            preview_url:
                                              "preview_url" in attachment
                                                ? attachment.preview_url
                                                : undefined,
                                          })
                                        }
                                      >
                                        <img
                                          src={attachmentSrc(attachment)}
                                          alt={attachment.file_name}
                                          className="bg-muted/50 rounded-md w-auto max-w-48 h-auto max-h-48 object-contain"
                                        />
                                      </Button>
                                    )
                                  }
                                  return (
                                    <a
                                      key={`${attachment.file_name}-${attachmentIndex}`}
                                      className="hover:bg-muted px-3 py-2 border rounded-md text-xs"
                                      href={attachmentHref(attachment)}
                                      download={attachment.file_name}
                                    >
                                      {attachment.file_name}
                                    </a>
                                  )
                                })}
                              </div>
                            ) : null
                          if (!toolEvent) {
                            return (
                              <div key={`action-${index}`} className="space-y-2">
                                <div className="flex items-start gap-1.5 text-muted-foreground text-xs leading-5">
                                  <span aria-hidden="true" className="opacity-50 select-none">
                                    ›
                                  </span>
                                  <span className="min-w-0 wrap-break-word">{part.label}</span>
                                </div>
                                {attachmentRow}
                              </div>
                            )
                          }
                          return (
                            <div key={`action-${index}`} className="space-y-2">
                              <details className="group/action text-muted-foreground text-xs">
                                <summary className="[&::-webkit-details-marker]:hidden flex items-start gap-1.5 leading-5 cursor-pointer list-none">
                                  <span
                                    aria-hidden="true"
                                    className="inline-block opacity-50 select-none transition-transform group-open/action:rotate-90"
                                  >
                                    ›
                                  </span>
                                  <span className="min-w-0 wrap-break-word">{part.label}</span>
                                </summary>
                                <div className="space-y-2 mt-2 ml-3 text-foreground">
                                  <ToolEventDetails
                                    toolEvent={toolEvent}
                                    t={t}
                                    onWriteApprovalDecision={onWriteApprovalDecision}
                                    onSourceProposalDecision={onSourceProposalDecision}
                                  />
                                </div>
                              </details>
                              {attachmentRow}
                            </div>
                          )
                        })
                      ) : thinkingLabels.length > 0 ? (
                        <div className="flex flex-wrap gap-2">
                          {thinkingLabels.map((label, index) => (
                            <span
                              key={`${label}-${index}`}
                              className="px-2 py-0.5 border border-muted-foreground/30 rounded-full text-[10px] text-muted-foreground uppercase tracking-wide"
                            >
                              {label}
                            </span>
                          ))}
                        </div>
                      ) : null
                    ) : null}
                  </ThinkingTrail>
                ) : null}
                {writeApprovalCards.length > 0 && !writeApprovalEvent ? (
                  <div className="space-y-3">
                    {writeApprovalCards.map((event) => (
                      <div key={event.id ?? event.request_id} className="space-y-2">
                        <p className="opacity-70 font-medium text-xs">
                          {t("chat_write_approval")}
                        </p>
                        <ToolEventDetails
                          toolEvent={event}
                          t={t}
                          onWriteApprovalDecision={onWriteApprovalDecision}
                          onSourceProposalDecision={onSourceProposalDecision}
                        />
                      </div>
                    ))}
                  </div>
                ) : null}
                {sourceProposalCards.length > 0 && !sourceProposalEvent ? (
                  <div className="space-y-3">
                    {sourceProposalCards.map((event) => (
                      <div key={event.id ?? event.title ?? "proposal"} className="space-y-2">
                        <p className="opacity-70 font-medium text-xs">
                          {t("chat_source_proposal")}
                        </p>
                        <ToolEventDetails
                          toolEvent={event}
                          t={t}
                          onWriteApprovalDecision={onWriteApprovalDecision}
                          onSourceProposalDecision={onSourceProposalDecision}
                        />
                      </div>
                    ))}
                  </div>
                ) : null}
                {hasStreamParts
                  ? // Always only the post-tool answer in the main bubble; intermediate
                    // speech stays in the detailed trail (or is omitted when hidden).
                    finalTextParts.map((part, index) => {
                      if (!part.text.trim()) return null
                      return renderMarkdown(part.text, `final-text-${index}`)
                    })
                  : content.trim().length > 0
                    ? renderMarkdown(content)
                    : null}
                {/* Attachments from actions still surface when details are hidden. */}
                {hasStreamParts && actionInfoLevel !== "detailed"
                  ? streamParts.map((part, index) => {
                      if (part.type !== "action") return null
                      const actionAttachments = part.attachments ?? []
                      if (actionAttachments.length === 0) return null
                      return (
                        <div key={`action-files-${index}`} className="space-y-2">
                          <div className="flex flex-wrap gap-2">
                            {actionAttachments.map((attachment, attachmentIndex) => {
                              const isImage = (attachment.content_type ?? "").startsWith(
                                "image/"
                              )
                              if (isImage) {
                                return (
                                  <Button
                                    key={`${attachment.file_name}-${attachmentIndex}`}
                                    type="button"
                                    variant="ghost"
                                    size="icon"
                                    className="p-0 rounded-md w-auto h-auto overflow-hidden"
                                    onClick={() =>
                                      onPreviewAttachment({
                                        file_name: attachment.file_name,
                                        content_type: attachment.content_type,
                                        data_base64: attachment.data_base64,
                                        content_url: attachment.content_url,
                                        preview_url:
                                          "preview_url" in attachment
                                            ? attachment.preview_url
                                            : undefined,
                                      })
                                    }
                                  >
                                    <img
                                      src={attachmentSrc(attachment)}
                                      alt={attachment.file_name}
                                      className="bg-muted/50 rounded-md w-auto max-w-32 h-auto max-h-32 object-contain"
                                    />
                                  </Button>
                                )
                              }
                              return (
                                <a
                                  key={`${attachment.file_name}-${attachmentIndex}`}
                                  className="hover:bg-muted px-3 py-2 border rounded-md text-xs"
                                  href={attachmentHref(attachment)}
                                  download={attachment.file_name}
                                >
                                  {attachment.file_name}
                                </a>
                              )
                            })}
                          </div>
                        </div>
                      )
                    })
                  : null}
              </div>
              {userAttachments.length > 0 ? (
                <div className="flex flex-wrap gap-2 mt-3">
                  {userAttachments.map((attachment, index) => {
                    const isImage = (attachment.content_type ?? "").startsWith("image/")
                    const href = attachmentHref(attachment)
                    if (isImage) {
                      return (
                        <Button
                          key={`${attachment.file_name}-${index}`}
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="p-0 rounded-md w-auto h-auto overflow-hidden"
                          onClick={() =>
                            onPreviewAttachment({
                              file_name: attachment.file_name,
                              content_type: attachment.content_type,
                              data_base64: attachment.data_base64,
                              content_url: attachment.content_url,
                              preview_url:
                                "preview_url" in attachment
                                  ? attachment.preview_url
                                  : undefined,
                            })
                          }
                        >
                          <img
                            src={attachmentSrc(attachment)}
                            alt={attachment.file_name}
                            className="bg-muted/50 rounded-md w-auto max-w-32 h-auto max-h-32 object-contain"
                          />
                        </Button>
                      )
                    }
                    if (href) {
                      return (
                        <a
                          key={`${attachment.file_name}-${index}`}
                          className="inline-flex items-center gap-1.5 hover:bg-muted px-3 py-2 border rounded-md text-xs"
                          href={href}
                          download={attachment.file_name}
                        >
                          <Download aria-hidden="true" className="opacity-70 size-3.5 shrink-0" />
                          {attachment.file_name}
                        </a>
                      )
                    }
                    return (
                      <div
                        key={`${attachment.file_name}-${index}`}
                        className="inline-flex items-center gap-1.5 px-3 py-2 border rounded-md text-xs"
                      >
                        {attachment.file_name}
                      </div>
                    )
                  })}
                </div>
              ) : null}
              {generatedFiles.length > 0 ? (
                <div className="mt-4 pt-3 border-t border-border/60">
                  <p className="mb-2 font-medium text-foreground/80 text-xs">
                    {t("chat_generated_files")}
                  </p>
                  <div className="flex flex-wrap gap-2">
                    {generatedFiles.map((attachment, index) => {
                      const isImage = (attachment.content_type ?? "").startsWith("image/")
                      if (isImage) {
                        return (
                          <Button
                            key={`${attachment.file_name}-${index}`}
                            type="button"
                            variant="ghost"
                            size="icon"
                            className="p-0 rounded-md w-auto h-auto overflow-hidden"
                            onClick={() =>
                              onPreviewAttachment({
                                file_name: attachment.file_name ?? "attachment",
                                content_type: attachment.content_type ?? "application/octet-stream",
                                data_base64: attachment.data_base64 ?? undefined,
                                content_url: attachment.content_url ?? undefined,
                                preview_url:
                                  "preview_url" in attachment
                                    ? attachment.preview_url
                                    : undefined,
                              })
                            }
                          >
                            <img
                              src={attachmentSrc(attachment)}
                              alt={attachment.file_name ?? undefined}
                              className="bg-muted/50 rounded-md w-auto max-w-32 h-auto max-h-32 object-contain"
                            />
                          </Button>
                        )
                      }
                      return (
                        <a
                          key={`${attachment.file_name}-${index}`}
                          className="inline-flex items-center gap-1.5 hover:bg-muted px-3 py-2 border rounded-md text-xs"
                          href={attachmentHref(attachment)}
                          download={attachment.file_name ?? undefined}
                        >
                          <Download aria-hidden="true" className="opacity-70 size-3.5 shrink-0" />
                          {attachment.file_name}
                        </a>
                      )
                    })}
                  </div>
                </div>
              ) : null}
            </>
          )}
        </div>
        {showUserSideActions ? (
          <div className="flex justify-end mt-1 md:mt-0 md:right-full md:absolute md:inset-y-0 md:flex-col md:justify-end md:mr-0.5 pointer-events-none">
            <div className="bottom-0 z-10 sticky grid grid-cols-2 w-fit opacity-100 md:flex md:items-center md:group-focus-within:opacity-100 md:group-hover:opacity-100 md:opacity-0 pointer-events-auto">
              {canManageMessage ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="opacity-70 hover:opacity-100 size-7"
                  onClick={() => onStartEdit(msg)}
                  aria-label={t("chat_edit_message")}
                >
                  <Pencil aria-hidden="true" className="size-4" />
                </Button>
              ) : null}
              {canCopyMessage ? (
                <CopyTextButton
                  text={msg.content}
                  label={t("chat_copy_message")}
                  copiedLabel={t("common_copied")}
                  iconOnly
                  className="opacity-70 hover:opacity-100 size-7"
                />
              ) : null}
              {canManageMessage ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="opacity-70 hover:opacity-100 size-7"
                  onClick={() => onDeleteFromMessage(msg)}
                  aria-label={t("chat_delete_message")}
                >
                  <Trash2 aria-hidden="true" className="size-4" />
                </Button>
              ) : null}
            </div>
          </div>
        ) : null}
        {showAssistantFooter ? (
          <div className="flex flex-wrap justify-between items-center gap-2 mt-2 transition">
            <div className="flex justify-start items-center gap-1">
              {showAssistantAnswerActions ? (
              <TooltipProvider delayDuration={300}>
                <div className="flex items-center gap-1">
                  {canCopyMessage ? (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <span>
                          <CopyTextButton
                            text={finalAnswerText}
                            label={t("chat_copy_message")}
                            copiedLabel={t("common_copied")}
                            iconOnly
                            className="opacity-70 hover:opacity-100 size-7"
                          />
                        </span>
                      </TooltipTrigger>
                      <TooltipContent>{t("chat_copy_message")}</TooltipContent>
                    </Tooltip>
                  ) : null}
                  {canSpeakMessage ? (
                    <SpeakMessageButton text={speakText} ttsStatus={ttsStatus} />
                  ) : null}
                  {canCopyMessage ? (
                    <DropdownMenu>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <DropdownMenuTrigger asChild>
                            <Button
                              type="button"
                              variant="ghost"
                              size="icon"
                              className="opacity-70 hover:opacity-100 size-7"
                              aria-label={t("chat_download_answer")}
                            >
                              <Download aria-hidden="true" className="size-4" />
                            </Button>
                          </DropdownMenuTrigger>
                        </TooltipTrigger>
                        <TooltipContent>{t("chat_download_answer")}</TooltipContent>
                      </Tooltip>
                      <DropdownMenuContent align="start">
                        <DropdownMenuItem
                          onClick={() => {
                            void downloadAnswerPdf(msg, {
                              question: exportQuestion,
                              brandLabel: brandingTitle || t("chat_title"),
                              logoUrl,
                            })
                          }}
                        >
                          {t("chat_download_pdf")}
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          onClick={() => {
                            void downloadAnswerDocx(msg)
                          }}
                        >
                          {t("chat_download_docx")}
                        </DropdownMenuItem>
                        <DropdownMenuItem onClick={() => downloadAnswerMarkdown(msg)}>
                          {t("chat_download_markdown")}
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  ) : null}
                  {actionsEnabled && onShareMessage ? (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="opacity-70 hover:opacity-100 size-7"
                          onClick={() => onShareMessage(msg)}
                          aria-label={t("chat_share_answer")}
                        >
                          <Share aria-hidden="true" className="size-4" />
                        </Button>
                      </TooltipTrigger>
                      <TooltipContent>{t("chat_share_answer")}</TooltipContent>
                    </Tooltip>
                  ) : null}
                  {onForkChat ? (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="opacity-70 hover:opacity-100 size-7"
                          onClick={() => onForkChat(msg)}
                          aria-label={t("chat_fork")}
                        >
                          <GitFork aria-hidden="true" className="size-4" />
                        </Button>
                      </TooltipTrigger>
                      <TooltipContent>{t("chat_fork")}</TooltipContent>
                    </Tooltip>
                  ) : null}
                  {actionsEnabled ? (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="opacity-70 hover:opacity-100 size-7"
                          onClick={() => onRetryMessage(msg)}
                          aria-label={t("chat_retry")}
                        >
                          <RotateCcw aria-hidden="true" className="size-4" />
                        </Button>
                      </TooltipTrigger>
                      <TooltipContent>{t("chat_retry")}</TooltipContent>
                    </Tooltip>
                  ) : null}
                </div>
              </TooltipProvider>
              ) : null}
              {uniqueSources.length > 0 ||
              ((msg.usage?.total_tokens ?? 0) > 0) ||
              (actionInfoLevel === "detailed" &&
                resolveTotalTimeMs(msg.usage) != null) ? (
                <div className="flex items-center gap-2">
                  {uniqueSources.length > 0 ? (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="px-2.5 rounded-full h-7 text-xs"
                      onClick={() => onOpenSources?.(uniqueSources)}
                    >
                      {uniqueSources.length === 1
                        ? t("chat_sources_count_one", { count: uniqueSources.length })
                        : t("chat_sources_count", { count: uniqueSources.length })}
                    </Button>
                  ) : null}
                  {actionInfoLevel === "detailed" && (msg.usage?.total_tokens ?? 0) > 0 ? (
                    <TooltipProvider delayDuration={200}>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <span
                            className="inline-flex items-center bg-background px-2.5 border border-border rounded-full h-7 text-muted-foreground text-xs cursor-default"
                            tabIndex={0}
                          >
                            {t("chat_tokens_count", {
                              count: msg.usage!.total_tokens.toLocaleString(),
                            })}
                          </span>
                        </TooltipTrigger>
                        <TooltipContent className="px-3 py-2">
                          <div className="gap-x-4 gap-y-1 grid grid-cols-[1fr_auto] min-w-[9rem] text-xs">
                            <span>{t("usage_input")}</span>
                            <span className="tabular-nums text-right">
                              {msg.usage!.input_tokens.toLocaleString()}
                            </span>
                            <span>{t("usage_output")}</span>
                            <span className="tabular-nums text-right">
                              {msg.usage!.output_tokens.toLocaleString()}
                            </span>
                            <span>{t("usage_cached")}</span>
                            <span className="tabular-nums text-right">
                              {msg.usage!.cached_tokens.toLocaleString()}
                            </span>
                            <span>{t("usage_thinking")}</span>
                            <span className="tabular-nums text-right">
                              {msg.usage!.thinking_tokens.toLocaleString()}
                            </span>
                            <span>{t("usage_cost")}</span>
                            <span className="tabular-nums text-right">
                              {msg.usage!.cost_usd == null
                                ? "—"
                                : msg.usage!.cost_usd.toLocaleString(undefined, {
                                    style: "currency",
                                    currency: "USD",
                                    minimumFractionDigits:
                                      msg.usage!.cost_usd > 0 && msg.usage!.cost_usd < 0.01
                                        ? 4
                                        : 2,
                                    maximumFractionDigits:
                                      msg.usage!.cost_usd > 0 && msg.usage!.cost_usd < 0.01
                                        ? 4
                                        : 2,
                                  })}
                            </span>
                          </div>
                        </TooltipContent>
                      </Tooltip>
                    </TooltipProvider>
                  ) : null}
                  {actionInfoLevel === "detailed" &&
                  resolveTotalTimeMs(msg.usage) != null ? (
                    <TooltipProvider delayDuration={200}>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <span
                            className="inline-flex items-center bg-background px-2.5 border border-border rounded-full h-7 text-muted-foreground text-xs tabular-nums cursor-default"
                            tabIndex={0}
                          >
                            {formatDurationMs(resolveTotalTimeMs(msg.usage)!)}
                          </span>
                        </TooltipTrigger>
                        <TooltipContent className="px-3 py-2">
                          <div className="gap-x-4 gap-y-1 grid grid-cols-[1fr_auto] min-w-[9rem] text-xs">
                            <span>{t("usage_generation_time")}</span>
                            <span className="tabular-nums text-right">
                              {formatDurationMs(resolveTotalTimeMs(msg.usage)!)}
                            </span>
                            {msg.usage?.queue_wait_ms != null ? (
                              <>
                                <span>{t("usage_wait_time")}</span>
                                <span className="tabular-nums text-right">
                                  {formatDurationMs(msg.usage.queue_wait_ms)}
                                </span>
                              </>
                            ) : null}
                            {msg.usage?.time_to_first_token_ms != null ? (
                              <>
                                <span>{t("usage_ttft")}</span>
                                <span className="tabular-nums text-right">
                                  {formatDurationMs(msg.usage.time_to_first_token_ms)}
                                </span>
                              </>
                            ) : null}
                            {msg.usage?.tokens_per_second != null ? (
                              <>
                                <span>{t("usage_tokens_per_second")}</span>
                                <span className="tabular-nums text-right">
                                  {formatTokensPerSecond(msg.usage.tokens_per_second)}{" "}
                                  {t("usage_tok_per_sec_unit")}
                                </span>
                              </>
                            ) : null}
                          </div>
                        </TooltipContent>
                      </Tooltip>
                    </TooltipProvider>
                  ) : null}
                </div>
              ) : null}
            </div>
            {msg.model_name ? (
              <div className="flex items-center gap-2 min-w-0">
                <span className="text-muted-foreground text-xs truncate leading-4">
                  {msg.model_name}
                </span>
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  )
}

export const MessageBubble = memo(
  MessageBubbleComponent,
  (prev, next) => {
    if (prev.msg !== next.msg) return false
    if (prev.isUser !== next.isUser) return false
    if (prev.isCodeEvent !== next.isCodeEvent) return false
    if (prev.isThinking !== next.isThinking) return false
    if (prev.currentStepLabel !== next.currentStepLabel) return false
    if (prev.currentToolLabel !== next.currentToolLabel) return false
    if (prev.actionInfoLevel !== next.actionInfoLevel) return false
    if (prev.actionsEnabled !== next.actionsEnabled) return false
    if (prev.ttsStatus !== next.ttsStatus) return false
    if (prev.isEditing !== next.isEditing) return false
    if (prev.codeTheme !== next.codeTheme) return false
    if (prev.t !== next.t) return false
    if (prev.onOpenSources !== next.onOpenSources) return false
    if (prev.onWriteApprovalDecision !== next.onWriteApprovalDecision) return false
    if (prev.onSourceProposalDecision !== next.onSourceProposalDecision) return false
    if (prev.thinkingLabels.length !== next.thinkingLabels.length) return false
    for (let i = 0; i < prev.thinkingLabels.length; i += 1) {
      if (prev.thinkingLabels[i] !== next.thinkingLabels[i]) return false
    }
    // Editing-only props should trigger rerender only for edited message bubble.
    if (next.isEditing) {
      if (prev.isEditDragActive !== next.isEditDragActive) return false
      if (prev.editingAttachments !== next.editingAttachments) return false
      if (prev.editAttachmentError !== next.editAttachmentError) return false
    }
    return true
  }
)
