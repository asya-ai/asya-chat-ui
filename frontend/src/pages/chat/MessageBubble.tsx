import { memo, useEffect, useMemo, useRef, useState } from "react"
import type { ComponentProps, CSSProperties } from "react"
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
  Bookmark,
  Pencil,
  RotateCcw,
  Share,
  Trash2,
  Plus,
  X,
} from "lucide-react"
import html2canvas from "html2canvas"
import { jsPDF } from "jspdf"

import { exportCoworkMarkdown } from "@/pages/chat/exportCoworkMarkdown"

import type { I18nContextValue } from "@/lib/i18n-context"
import type { ActionInfoLevel } from "@/lib/storage"
import type {
  ChatMessage,
  ChatMessageAttachmentInput,
  SourceItem,
  ToolEvent,
} from "@/lib/types"
import { dedupeSources } from "@/lib/types"
import { shouldSubmitOnEnter } from "@/lib/chat-input"
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
  onSaveAsPrompt?: (msg: ChatMessage) => void
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

const formatTokensPerSecond = (value: number): string => {
  if (!Number.isFinite(value) || value <= 0) return "—"
  if (value >= 100) return value.toFixed(0)
  if (value >= 10) return value.toFixed(1)
  return value.toFixed(2)
}

let mermaidInitialized = false
let mermaidRenderId = 0

const nextMermaidRenderId = () => {
  mermaidRenderId += 1
  return `chatui-mermaid-${mermaidRenderId}`
}

const loadMermaid = async () => {
  const mermaid = (await import("mermaid")).default
  if (!mermaidInitialized) {
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: "strict",
    })
    mermaidInitialized = true
  }
  return mermaid
}

const MERMAID_START_PATTERN =
  /^(?:graph|flowchart|sequenceDiagram|classDiagram|stateDiagram(?:-v2)?|erDiagram|journey|gantt|pie|gitGraph|mindmap|timeline|quadrantChart|sankey-beta|block-beta|xychart-beta|C4Context)\b/i

const normalizeMathContent = (content: string) => {
  const lines = content.split(/\r?\n/)
  const output: string[] = []
  let mathLines: string[] | null = null
  let isInCodeFence = false

  for (const line of lines) {
    const trimmed = line.trim()
    if (trimmed.startsWith("```")) {
      if (mathLines) {
        output.push("[", ...mathLines)
        mathLines = null
      }
      isInCodeFence = !isInCodeFence
      output.push(line)
      continue
    }

    if (!isInCodeFence && trimmed === "[" && !mathLines) {
      mathLines = []
      continue
    }

    if (!isInCodeFence && trimmed === "]" && mathLines) {
      output.push("$$", mathLines.join("\n").trim(), "$$")
      mathLines = null
      continue
    }

    if (mathLines) {
      mathLines.push(line)
    } else {
      output.push(line)
    }
  }

  if (mathLines) {
    output.push("[", ...mathLines)
  }

  return output.join("\n").replace(/\\text\{([→\-–—]+)\}/g, (_, value) => value)
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
  if (language?.toLowerCase() === "mermaid") {
    return content.trim()
  }

  const trimmed = content.trim()
  if (!trimmed) return null

  if (/^mermaid\s*[\r\n]+/i.test(trimmed)) {
    const chart = trimmed.replace(/^mermaid\s*[\r\n]+/i, "").trim()
    return chart || null
  }

  if (MERMAID_START_PATTERN.test(trimmed)) {
    return trimmed
  }

  return null
}

const isBlockCode = (content: string, className?: string) =>
  Boolean(/language-\w+/.exec(className || "")) || content.includes("\n")

const codeBlockClassName =
  "relative my-3 overflow-hidden rounded-lg border border-border bg-code text-code-foreground shadow-sm"

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

/** Final answer only — drops preamble / thoughts before the first tool call. */
export const getFinalAnswerText = (msg: ChatMessage): string => {
  const parts = msg.stream_parts ?? []
  const firstActionIndex = parts.findIndex((part) => part.type === "action")
  if (firstActionIndex >= 0) {
    const afterTools = parts
      .slice(firstActionIndex + 1)
      .filter(
        (part): part is Extract<NonNullable<ChatMessage["stream_parts"]>[number], { type: "text" }> =>
          part.type === "text"
      )
      .map((part) => part.text)
      .join("")
      .trim()
    if (afterTools) return afterTools
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

const formatInlineMarkdown = (value: string) => {
  let html = escapeHtml(value)
  html = html.replace(/\[(\d+)\]/g, "<sup>[$1]</sup>")
  html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2">$1</a>')
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>")
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
  html = html.replace(/(^|[^*])\*([^*]+)\*(?!\*)/g, "$1<em>$2</em>")
  return html
}

const markdownToExportHtml = (markdown: string) => {
  const lines = markdown.replace(/\r\n/g, "\n").split("\n")
  const blocks: string[] = []
  let paragraph: string[] = []
  let listItems: string[] = []
  let listType: "ul" | "ol" | null = null

  const flushParagraph = () => {
    if (paragraph.length === 0) return
    blocks.push(`<p>${formatInlineMarkdown(paragraph.join(" "))}</p>`)
    paragraph = []
  }

  const flushList = () => {
    if (!listType || listItems.length === 0) {
      listItems = []
      listType = null
      return
    }
    const tag = listType
    blocks.push(
      `<${tag}>${listItems.map((item) => `<li>${formatInlineMarkdown(item)}</li>`).join("")}</${tag}>`
    )
    listItems = []
    listType = null
  }

  for (const rawLine of lines) {
    const line = rawLine.trimEnd()
    const trimmed = line.trim()
    if (!trimmed) {
      flushParagraph()
      flushList()
      continue
    }
    const heading = /^(#{1,3})\s+(.+)$/.exec(trimmed)
    if (heading) {
      flushParagraph()
      flushList()
      const level = heading[1].length
      blocks.push(`<h${level}>${formatInlineMarkdown(heading[2])}</h${level}>`)
      continue
    }
    const unordered = /^[-*•]\s+(.+)$/.exec(trimmed)
    if (unordered) {
      flushParagraph()
      if (listType && listType !== "ul") flushList()
      listType = "ul"
      listItems.push(unordered[1])
      continue
    }
    const ordered = /^\d+[.)]\s+(.+)$/.exec(trimmed)
    if (ordered) {
      flushParagraph()
      if (listType && listType !== "ol") flushList()
      listType = "ol"
      listItems.push(ordered[1])
      continue
    }
    flushList()
    paragraph.push(trimmed)
  }
  flushParagraph()
  flushList()
  return blocks.join("")
}

const downloadAnswerMarkdown = (msg: ChatMessage) => {
  const text = getFinalAnswerText(msg)
  const blob = new Blob([text], { type: "text/markdown;charset=utf-8" })
  downloadBlob(blob, `${answerFileStem(msg)}.md`)
}

const downloadAnswerPdf = async (
  msg: ChatMessage,
  options: { question?: string | null; brandLabel: string }
) => {
  const text = getFinalAnswerText(msg)
  const sources = msg.sources?.length ? dedupeSources(msg.sources) : []
  const question = (options.question ?? "").trim()
  const logoUrl = `${window.location.origin}/logo_chat.svg`
  const bodyHtml = markdownToExportHtml(text)
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

  // A4 at 96dpi CSS reference width. Keep the offscreen node fully opaque —
  // opacity:0 makes some browsers skip font rasterization / soft-blur glyphs.
  const pageCssWidth = 794
  const root = document.createElement("div")
  root.setAttribute("data-pdf-export", "true")
  root.style.cssText = `position:fixed;left:-10000px;top:0;width:${pageCssWidth}px;pointer-events:none;z-index:-1;`
  root.innerHTML = `
    <style>
      .pdf-page {
        box-sizing: border-box;
        width: ${pageCssWidth}px;
        padding: 64px 72px 72px;
        background: #ffffff;
        color: #1a1210;
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
      .pdf-body p {
        margin: 0 0 16px;
      }
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
      .pdf-body ul,
      .pdf-body ol {
        margin: 0 0 16px;
        padding-left: 22px;
      }
      .pdf-body li {
        margin: 0 0 8px;
      }
      .pdf-body strong {
        font-family: "Google Sans Flex", ui-sans-serif, system-ui, sans-serif;
        font-weight: 700;
      }
      .pdf-body em { font-style: italic; }
      .pdf-body code {
        font: 13px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace;
        background: rgba(25, 9, 6, 0.06);
        padding: 0 4px;
        border-radius: 4px;
      }
      .pdf-body a { color: inherit; }
      .pdf-body sup {
        font-size: 10px;
        line-height: 0;
        vertical-align: super;
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
        color: rgba(25, 9, 6, 0.78);
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
        <img src="${logoUrl}" alt="" />
        <span>${escapeHtml(options.brandLabel)}</span>
      </div>
      ${question ? `<h1 class="pdf-title">${escapeHtml(question)}</h1>` : ""}
      <div class="pdf-body">${bodyHtml || "<p></p>"}</div>
      ${sourcesHtml}
    </div>
  `
  document.body.appendChild(root)

  try {
    const page = root.querySelector(".pdf-page")
    if (!(page instanceof HTMLElement)) return
    await Promise.all([
      document.fonts?.ready ?? Promise.resolve(),
      ...Array.from(page.querySelectorAll("img")).map(
        (img) =>
          new Promise<void>((resolve) => {
            if (img.complete) {
              resolve()
              return
            }
            img.onload = () => resolve()
            img.onerror = () => resolve()
          })
      ),
    ])
    // ~300dpi: 794px @ 96dpi → scale ≈ 3.125. Cap at 4 to limit memory on long answers.
    const scale = Math.min(4, Math.max(3, Math.ceil((window.devicePixelRatio || 1) * 2)))
    const canvas = await html2canvas(page, {
      scale,
      backgroundColor: "#ffffff",
      useCORS: true,
      logging: false,
      windowWidth: pageCssWidth,
    })
    const doc = new jsPDF({ unit: "pt", format: "a4", compress: true })
    const pageWidth = doc.internal.pageSize.getWidth()
    const pageHeight = doc.internal.pageSize.getHeight()
    // Slice the high-res canvas into page-sized bitmaps instead of offsetting one
    // giant image — cleaner in viewers and avoids soft downscale artifacts.
    const pageCanvasHeight = Math.max(1, Math.floor((pageHeight * canvas.width) / pageWidth))
    let sourceY = 0
    let pageIndex = 0
    while (sourceY < canvas.height) {
      const sliceHeight = Math.min(pageCanvasHeight, canvas.height - sourceY)
      const pageCanvas = document.createElement("canvas")
      pageCanvas.width = canvas.width
      pageCanvas.height = sliceHeight
      const ctx = pageCanvas.getContext("2d")
      if (!ctx) break
      ctx.fillStyle = "#ffffff"
      ctx.fillRect(0, 0, pageCanvas.width, pageCanvas.height)
      ctx.drawImage(
        canvas,
        0,
        sourceY,
        canvas.width,
        sliceHeight,
        0,
        0,
        canvas.width,
        sliceHeight
      )
      const imgData = pageCanvas.toDataURL("image/png")
      const sliceImgHeight = (sliceHeight * pageWidth) / canvas.width
      if (pageIndex > 0) doc.addPage()
      doc.addImage(imgData, "PNG", 0, 0, pageWidth, sliceImgHeight)
      sourceY += pageCanvasHeight
      pageIndex += 1
    }
    doc.save(`${answerFileStem(msg)}.pdf`)
  } finally {
    root.remove()
  }
}

const downloadAnswerDocx = async (msg: ChatMessage) => {
  const text = getFinalAnswerText(msg)
  const { blob, fileName } = await exportCoworkMarkdown(text, {
    format: "docx",
    fileName: answerFileStem(msg),
  })
  downloadBlob(blob, fileName)
}

const ToolEventDetails = ({
  toolEvent,
  t,
}: {
  toolEvent: ToolEvent
  t: I18nContextValue["t"]
}) => {
  if (toolEvent.type === "code_execution") {
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
              toolEvent.output?.requires_approval
                ? t("chat_execution_requires_approval")
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
      <div className="max-w-none text-muted-foreground text-xs [&_p]:my-1.5 [&_p]:leading-5 [&_ul]:my-1.5 [&_ol]:my-1.5 [&_li]:my-0.5 [&_strong]:text-foreground/80 [&_h1]:mt-2 [&_h1]:mb-1 [&_h1]:font-medium [&_h1]:text-sm [&_h2]:mt-2 [&_h2]:mb-1 [&_h2]:font-medium [&_h2]:text-sm [&_h3]:mt-2 [&_h3]:mb-1 [&_h3]:font-medium [&_h3]:text-xs">
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
  label,
  copiedLabel,
  className = "",
  iconOnly = false,
}: {
  text: string
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
      await copyToClipboard(text)
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
      <CopyTextButton
        text={code}
        label={copyLabel}
        copiedLabel={copiedLabel}
        className="top-2 right-2 z-10 absolute bg-code/90 hover:bg-muted border border-border text-[10px] text-code-foreground/80 hover:text-code-foreground uppercase tracking-wide"
      />
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
    <ReactMarkdown
      remarkPlugins={[remarkGfm, remarkBreaks, remarkMath]}
      rehypePlugins={[rehypeKatex]}
      components={components}
    >
      {normalized}
    </ReactMarkdown>
  )
}

const MermaidDiagram = ({
  chart,
  copyLabel,
  copiedLabel,
  renderFailedLabel,
}: {
  chart: string
  copyLabel: string
  copiedLabel: string
  renderFailedLabel: string
}) => {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const [renderError, setRenderError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const renderDiagram = async () => {
      const container = containerRef.current
      if (!container) return
      container.innerHTML = ""
      setRenderError(null)
      try {
        const mermaid = await loadMermaid()
        const { svg, bindFunctions } = await mermaid.render(nextMermaidRenderId(), chart)
        if (cancelled || !containerRef.current) return
        containerRef.current.innerHTML = svg
        bindFunctions?.(containerRef.current)
      } catch (error) {
        if (cancelled) return
        setRenderError(
          error instanceof Error
            ? error.message
            : renderFailedLabel
        )
      }
    }
    void renderDiagram()
    return () => {
      cancelled = true
    }
  }, [chart, renderFailedLabel])

  if (renderError) {
    return (
      <div className="relative my-3">
        <CopyTextButton
          text={chart}
          label={copyLabel}
          copiedLabel={copiedLabel}
          className="top-2 right-2 absolute bg-background/80 border border-muted-foreground/30 text-[10px] text-muted-foreground hover:text-foreground uppercase tracking-wide"
        />
        <pre className="bg-destructive/10 p-2 rounded text-destructive text-xs whitespace-pre-wrap">
          {renderError}
        </pre>
      </div>
    )
  }

  return (
    <div className="relative my-3">
      <CopyTextButton
        text={chart}
        label={copyLabel}
        copiedLabel={copiedLabel}
        className="top-2 right-2 z-10 absolute bg-background/80 border border-muted-foreground/30 text-[10px] text-muted-foreground hover:text-foreground uppercase tracking-wide"
      />
      <div
        ref={containerRef}
        className="max-w-full [&_svg]:max-w-none [&_svg]:h-auto overflow-x-auto"
      />
    </div>
  )
}

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
  onSaveAsPrompt,
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
}: MessageBubbleProps) => {
  const editFileInputRef = useRef<HTMLInputElement | null>(null)
  const editTextareaRef = useRef<HTMLTextAreaElement | null>(null)
  const [hasEditText, setHasEditText] = useState(() => Boolean(msg.content.trim()))

  useEffect(() => {
    if (!isEditing) return
    setHasEditText(Boolean(msg.content.trim()))
  }, [isEditing, msg.id, msg.content])
  const isContextSummaryEvent = msg.tool_event?.type === "context_summary"
  const codeEvent = msg.tool_event?.type === "code_execution" ? msg.tool_event : null
  const toolCallEvent = msg.tool_event?.type === "tool_call" ? msg.tool_event : null
  const chatViewEvent = msg.activity_event?.type === "chat_view" ? msg.activity_event : null
  const urlAttachmentsEvent =
    msg.tool_event?.type === "url_attachments" ? msg.tool_event : null
  const contextSummaryEvent =
    msg.tool_event?.type === "context_summary" ? msg.tool_event : null
  const hasEventHeader = Boolean(
    isCodeEvent ||
      toolCallEvent ||
      chatViewEvent ||
      urlAttachmentsEvent ||
      isContextSummaryEvent
  )
  const finalAnswerText = useMemo(() => getFinalAnswerText(msg), [msg])
  const canCopyMessage = Boolean(
    isUser ? msg.content.trim() : finalAnswerText
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
  const markdownComponents = useMemo(
    () => ({
      p({ children, node, ...rest }: any) {
        void node
        return (
          <p className={isUser ? "m-0 leading-6" : "my-3 leading-7"} {...rest}>
            {children}
          </p>
        )
      },
      ul({ children, node, ...rest }: any) {
        void node
        return (
          <ul className="space-y-2.5 my-3 pl-6 list-disc" {...rest}>
            {children}
          </ul>
        )
      },
      ol({ children, node, ...rest }: any) {
        void node
        return (
          <ol className="space-y-2.5 my-3 pl-6 list-decimal" {...rest}>
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
          <div className="my-3 overflow-x-auto">
            <table className="w-full text-sm border-collapse" {...rest}>
              {children}
            </table>
          </div>
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
        const codeContent = String(children).replace(/\n$/, "")
        const mermaidChart = isBlockCode(codeContent, className)
          ? toMermaidChart(codeContent, match?.[1] ?? null)
          : null
        if (mermaidChart) {
          return (
            <MermaidDiagram
              chart={mermaidChart}
              copyLabel={t("chat_copy_mermaid")}
              copiedLabel={t("common_copied")}
              renderFailedLabel={t("chat_mermaid_render_failed")}
            />
          )
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
              <CopyTextButton
                text={codeContent}
                label={t("chat_copy_code")}
                copiedLabel={t("common_copied")}
                className="top-2 right-2 z-10 absolute bg-code/90 hover:bg-muted border border-border text-[10px] text-code-foreground/80 hover:text-code-foreground uppercase tracking-wide"
              />
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
      markdown={markdown}
      urgent={isLiveGeneration}
      components={markdownComponents}
    />
  )
  const attachmentSrc = (attachment: {
    content_type: string
    data_base64?: string
    content_url?: string
    preview_url?: string
  }) => {
    if (attachment.preview_url) return attachment.preview_url
    if (attachment.data_base64) {
      return `data:${attachment.content_type};base64,${attachment.data_base64}`
    }
    return attachment.content_url || ""
  }
  const attachmentHref = (attachment: {
    content_type: string
    data_base64?: string
    content_url?: string
  }) => {
    return attachmentSrc(attachment)
  }
  const attachmentIdentity = (attachment: {
    file_name?: string | null
    content_type?: string | null
    data_base64?: string | null
    content_url?: string | null
  }) =>
    attachment.data_base64 ||
    attachment.content_url ||
    `${attachment.file_name ?? ""}:${attachment.content_type ?? ""}`
  const timelineAttachmentKeys = new Set(
    streamParts.flatMap((part) =>
      part.type === "action" ? (part.attachments ?? []).map(attachmentIdentity) : []
    )
  )
  const bottomAttachments = (msg.attachments ?? []).filter(
    (attachment) => !timelineAttachmentKeys.has(attachmentIdentity(attachment))
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
  const showAssistantFooter =
    !isUser &&
    !isEditing &&
    (canCopyMessage ||
      uniqueSources.length > 0 ||
      Boolean(msg.model_name) ||
      (actionInfoLevel === "detailed" &&
        ((msg.usage?.total_tokens ?? 0) > 0 ||
          msg.usage?.generation_time_ms != null)) ||
      (actionsEnabled && msg.generation_status === "failed"))

  return (
    <div
      className={`mx-auto flex w-full min-w-0 max-w-(--chat-content-width) ${
        isUser ? "justify-end" : "justify-start"
      }`}
    >
      <div className={`group relative min-w-0 ${isEditing || !isUser ? "w-full" : "max-w-[85%]"}`}>
        {showUserSideActions ? (
          <div className="right-full absolute inset-y-0 flex flex-col justify-end mr-0.5 pointer-events-none">
            <div className="bottom-0 z-10 sticky flex items-center opacity-100 md:group-focus-within:opacity-100 md:group-hover:opacity-100 md:opacity-0 pointer-events-auto">
              {actionsEnabled ? (
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
              {actionsEnabled && onSaveAsPrompt && canCopyMessage ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="opacity-70 hover:opacity-100 size-7"
                  onClick={() => onSaveAsPrompt(msg)}
                  aria-label={t("prompt_save_message")}
                >
                  <Bookmark aria-hidden="true" className="size-4" />
                </Button>
              ) : null}
              {actionsEnabled ? (
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
        <div
          className={`min-w-0 overflow-clip rounded-lg wrap-break-word whitespace-normal ${
            isUser
              ? "bg-secondary p-2 text-base leading-6 text-foreground"
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
                <ToolEventDetails toolEvent={codeEvent} t={t} />
              ) : null}
            </details>
          ) : toolCallEvent ? (
            <ToolEventDetails toolEvent={toolCallEvent} t={t} />
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
              <ToolEventDetails toolEvent={urlAttachmentsEvent} t={t} />
            </details>
          ) : isContextSummaryEvent ? (
            <details className="space-y-3">
              <summary className="text-xs uppercase tracking-wide cursor-pointer">
                {t("chat_context_summarized")}
              </summary>
              {contextSummaryEvent ? (
                <ToolEventDetails toolEvent={contextSummaryEvent} t={t} />
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
              {hasStreamParts ? (
                <div className="space-y-2">
                  {streamParts.map((part, index) => {
                    if (part.type === "text") {
                      if (!part.text.trim()) return null
                      return renderMarkdown(part.text, `text-${index}`)
                    }
                    const actionAttachments = part.attachments ?? []
                    if (actionInfoLevel === "none") {
                      if (actionAttachments.length === 0) return null
                      return (
                        <div key={`action-${index}`} className="space-y-2">
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
                    }
                    if (actionInfoLevel !== "detailed") return null
                    const toolEvent = part.tool_event
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
                            <ToolEventDetails toolEvent={toolEvent} t={t} />
                          </div>
                        </details>
                        {attachmentRow}
                      </div>
                    )
                  })}
                  {isThinking ? (
                    actionInfoLevel === "detailed" ? (
                      <div className="flex items-center gap-1 py-1" role="status" aria-live="polite">
                        <span
                          aria-hidden="true"
                          className="bg-muted-foreground/60 rounded-full w-2 h-2 animate-bounce"
                          style={{ animationDelay: "0ms" }}
                        />
                        <span
                          aria-hidden="true"
                          className="bg-muted-foreground/60 rounded-full w-2 h-2 animate-bounce"
                          style={{ animationDelay: "150ms" }}
                        />
                        <span
                          aria-hidden="true"
                          className="bg-muted-foreground/60 rounded-full w-2 h-2 animate-bounce"
                          style={{ animationDelay: "300ms" }}
                        />
                      </div>
                    ) : (
                      <div
                        className="inline-flex justify-center items-center gap-0.5 px-1.5 py-0.5 bg-border rounded-full font-medium text-foreground text-xs leading-4"
                        role="status"
                        aria-live="polite"
                      >
                        <span
                          aria-hidden="true"
                          className="opacity-[0.79] size-3.5 animate-spin figma-icon"
                          style={{ maskImage: "url('/icon-thinking.svg')" }}
                        />
                        <span>{t("chat_thinking")}</span>
                      </div>
                    )
                  ) : null}
                </div>
              ) : (
                <>
                  {content.trim().length > 0 ? renderMarkdown(content) : null}
                  {isThinking || thinkingLabels.length > 0 ? (
                    actionInfoLevel === "detailed" ? (
                      <div className="space-y-2 py-2" role="status" aria-live="polite">
                        {currentStepLabel || currentToolLabel ? (
                          <div className="text-[11px] text-muted-foreground uppercase tracking-wide">
                            {[currentStepLabel, currentToolLabel].filter(Boolean).join(" - ")}
                          </div>
                        ) : null}
                        {isThinking ? (
                          <div className="flex items-center gap-1">
                            <span
                              aria-hidden="true"
                              className="bg-muted-foreground/60 rounded-full w-2 h-2 animate-bounce"
                              style={{ animationDelay: "0ms" }}
                            />
                            <span
                              aria-hidden="true"
                              className="bg-muted-foreground/60 rounded-full w-2 h-2 animate-bounce"
                              style={{ animationDelay: "150ms" }}
                            />
                            <span
                              aria-hidden="true"
                              className="bg-muted-foreground/60 rounded-full w-2 h-2 animate-bounce"
                              style={{ animationDelay: "300ms" }}
                            />
                          </div>
                        ) : null}
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
                      </div>
                    ) : isThinking ? (
                      <div
                        className="inline-flex justify-center items-center gap-0.5 px-1.5 py-0.5 bg-border rounded-full font-medium text-foreground text-xs leading-4"
                        role="status"
                        aria-live="polite"
                      >
                        <span
                          aria-hidden="true"
                          className="opacity-[0.79] size-3.5 animate-spin figma-icon"
                          style={{ maskImage: "url('/icon-thinking.svg')" }}
                        />
                        <span>{t("chat_thinking")}</span>
                      </div>
                    ) : null
                  ) : null}
                </>
              )}
                            {bottomAttachments.length > 0 ? (
                <div className="flex flex-wrap gap-2 mt-3">
                  {bottomAttachments.map((attachment, index) => {
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
                              file_name: attachment.file_name,
                              content_type: attachment.content_type,
                              data_base64: attachment.data_base64,
                              content_url: attachment.content_url,
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
                        key={`${attachment.file_name}-${index}`}
                        className="hover:bg-muted px-3 py-2 border rounded-md text-xs"
                        href={attachmentHref(attachment)}
                        download={attachment.file_name}
                      >
                        {attachment.file_name}
                      </a>
                    )
                  })}
                </div>
              ) : null}
            </>
          )}
        </div>
        {showAssistantFooter ? (
          <div className="flex flex-wrap justify-between items-center gap-2 mt-2 transition">
            <div className="flex justify-start items-center gap-1">
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
                              brandLabel: t("chat_title"),
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
              {uniqueSources.length > 0 ||
              ((msg.usage?.total_tokens ?? 0) > 0) ||
              (actionInfoLevel === "detailed" &&
                msg.usage?.generation_time_ms != null) ? (
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
                  msg.usage?.generation_time_ms != null ? (
                    <TooltipProvider delayDuration={200}>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <span
                            className="inline-flex items-center bg-background px-2.5 border border-border rounded-full h-7 text-muted-foreground text-xs tabular-nums cursor-default"
                            tabIndex={0}
                          >
                            {formatDurationMs(msg.usage.generation_time_ms)}
                          </span>
                        </TooltipTrigger>
                        <TooltipContent className="px-3 py-2">
                          <div className="gap-x-4 gap-y-1 grid grid-cols-[1fr_auto] min-w-[9rem] text-xs">
                            <span>{t("usage_generation_time")}</span>
                            <span className="tabular-nums text-right">
                              {formatDurationMs(msg.usage.generation_time_ms)}
                            </span>
                            {msg.usage.time_to_first_token_ms != null ? (
                              <>
                                <span>{t("usage_ttft")}</span>
                                <span className="tabular-nums text-right">
                                  {formatDurationMs(msg.usage.time_to_first_token_ms)}
                                </span>
                              </>
                            ) : null}
                            {msg.usage.tokens_per_second != null ? (
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
    if (prev.isEditing !== next.isEditing) return false
    if (prev.codeTheme !== next.codeTheme) return false
    if (prev.t !== next.t) return false
    if (prev.onOpenSources !== next.onOpenSources) return false
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
