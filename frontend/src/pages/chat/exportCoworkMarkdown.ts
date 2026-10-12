import { Document, HeadingLevel, ImageRun, Packer, Paragraph, TextRun } from "docx"
import html2canvas from "html2canvas"
import { jsPDF } from "jspdf"
import {
  type CoworkImageUrlMap,
  resolveCoworkPresentationMarkdown,
} from "@/pages/chat/resolveCoworkPresentationImages"

export type MarkdownExportFormat = "md" | "txt" | "pdf" | "docx"

const OPEN_FENCE = /^( {0,3})(`{3,}|~{3,})(.*)$/
const IMAGE_INLINE_RE_SOURCE =
  String.raw`!\[([^\]]*)\]\(([^)\s]+)(?:\s+(?:"[^"]*"|'[^']*'))?\)`
const imageInlineRe = () => new RegExp(IMAGE_INLINE_RE_SOURCE, "g")
const IMAGE_LINE_RE = new RegExp(`^${IMAGE_INLINE_RE_SOURCE}$`)
const DOCX_IMAGE_MAX_WIDTH_PX = 620
const PDF_IMAGE_MAX_WIDTH_PX = 650

const escapeHtml = (value: string) =>
  value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;")

/** PDF/HTML body styles shared by answer + cowork exports. */
export const markdownExportBodyCss = `
.pdf-body p { margin: 0 0 14px; }
.pdf-body h1,
.pdf-body h2,
.pdf-body h3 {
  margin: 24px 0 10px;
  font-weight: 600;
  line-height: 1.25;
}
.pdf-body h1 { font-size: 26px; }
.pdf-body h2 { font-size: 22px; }
.pdf-body h3 { font-size: 18px; }
.pdf-body ul,
.pdf-body ol {
  margin: 0 0 14px;
  padding-inline-start: 4.5ch;
}
.pdf-body ul { list-style: disc; }
.pdf-body ol { list-style: decimal; font-variant-numeric: tabular-nums; }
.pdf-body li { margin: 0 0 6px; }
.pdf-body blockquote {
  margin: 0 0 14px;
  padding: 0 0 0 14px;
  border-left: 3px solid rgba(7, 12, 28, 0.18);
  color: rgba(7, 12, 28, 0.78);
}
.pdf-body strong { font-weight: 700; }
.pdf-body em { font-style: italic; }
.pdf-body a { color: inherit; }
.pdf-body code {
  font: 13px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  background: rgba(7, 12, 28, 0.06);
  padding: 0.1em 0.35em;
  border-radius: 4px;
}
.pdf-body pre {
  margin: 0 0 16px;
  padding: 14px 16px;
  overflow-x: auto;
  background: #0f172a;
  color: #e2e8f0;
  border-radius: 10px;
}
.pdf-body pre code {
  display: block;
  padding: 0;
  background: transparent;
  color: inherit;
  font: 12.5px/1.55 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  white-space: pre-wrap;
  word-break: break-word;
}
.pdf-body sup {
  font-size: 10px;
  line-height: 0;
  vertical-align: super;
}
.pdf-body hr {
  margin: 20px 0;
  border: 0;
  border-top: 1px solid rgba(7, 12, 28, 0.12);
}
.pdf-body img {
  display: block;
  max-width: ${PDF_IMAGE_MAX_WIDTH_PX}px;
  width: auto;
  height: auto;
  margin: 8px 0 18px;
  border-radius: 8px;
}
.pdf-body .pdf-figure {
  margin: 0;
}
`

type MdBlock =
  | { type: "paragraph"; text: string }
  | { type: "heading"; level: 1 | 2 | 3; text: string }
  | { type: "list"; ordered: boolean; items: string[] }
  | { type: "code"; language: string; code: string }
  | { type: "blockquote"; text: string }
  | { type: "image"; alt: string; src: string }
  | { type: "hr" }

type DocxImageType = "jpg" | "png" | "gif" | "bmp"

type LoadedExportImage = {
  data: Uint8Array
  type: DocxImageType
  width: number
  height: number
  dataUrl: string
}

const absolutizeUrl = (src: string) => {
  const value = src.trim()
  if (!value) return value
  if (
    value.startsWith("data:") ||
    value.startsWith("blob:") ||
    /^https?:\/\//i.test(value)
  ) {
    return value
  }
  if (typeof window === "undefined") return value
  if (value.startsWith("//")) return `${window.location.protocol}${value}`
  if (value.startsWith("/")) return `${window.location.origin}${value}`
  try {
    return new URL(value, window.location.href).href
  } catch {
    return value
  }
}

const fitImageSize = (width: number, height: number, maxWidth: number) => {
  const w = Math.max(1, width || maxWidth)
  const h = Math.max(1, height || maxWidth)
  if (w <= maxWidth) return { width: Math.round(w), height: Math.round(h) }
  const scale = maxWidth / w
  return { width: Math.round(w * scale), height: Math.round(h * scale) }
}

const blobToDataUrl = (blob: Blob) =>
  new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result || ""))
    reader.onerror = () => reject(reader.error ?? new Error("Failed to read image"))
    reader.readAsDataURL(blob)
  })

const probeImageSize = (dataUrl: string) =>
  new Promise<{ width: number; height: number }>((resolve) => {
    const img = new Image()
    img.onload = () =>
      resolve({
        width: img.naturalWidth || PDF_IMAGE_MAX_WIDTH_PX,
        height: img.naturalHeight || PDF_IMAGE_MAX_WIDTH_PX,
      })
    img.onerror = () =>
      resolve({ width: PDF_IMAGE_MAX_WIDTH_PX, height: PDF_IMAGE_MAX_WIDTH_PX })
    img.src = dataUrl
  })

const mimeToDocxType = (mime: string): DocxImageType | null => {
  const value = mime.toLowerCase()
  if (value.includes("jpeg") || value.includes("jpg")) return "jpg"
  if (value.includes("png")) return "png"
  if (value.includes("gif")) return "gif"
  if (value.includes("bmp")) return "bmp"
  return null
}

const canvasToPngBytes = async (
  dataUrl: string
): Promise<{ bytes: Uint8Array; dataUrl: string } | null> => {
  try {
    const img = await new Promise<HTMLImageElement>((resolve, reject) => {
      const el = new Image()
      el.onload = () => resolve(el)
      el.onerror = () => reject(new Error("image decode failed"))
      el.src = dataUrl
    })
    const canvas = document.createElement("canvas")
    canvas.width = Math.max(1, img.naturalWidth || 1)
    canvas.height = Math.max(1, img.naturalHeight || 1)
    const ctx = canvas.getContext("2d")
    if (!ctx) return null
    ctx.drawImage(img, 0, 0)
    const pngDataUrl = canvas.toDataURL("image/png")
    const blob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob((value) => resolve(value), "image/png")
    )
    if (!blob) return null
    return {
      bytes: new Uint8Array(await blob.arrayBuffer()),
      dataUrl: pngDataUrl,
    }
  } catch {
    return null
  }
}

const loadExportImage = async (src: string): Promise<LoadedExportImage | null> => {
  const absolute = absolutizeUrl(src)
  if (!absolute) return null
  try {
    let dataUrl = absolute
    let bytes: Uint8Array
    let mime = "image/png"

    if (absolute.startsWith("data:")) {
      const match = /^data:([^;,]+)/i.exec(absolute)
      mime = match?.[1]?.trim() || "image/png"
      const comma = absolute.indexOf(",")
      const payload = comma >= 0 ? absolute.slice(comma + 1) : ""
      const binary = atob(payload)
      bytes = new Uint8Array(binary.length)
      for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i)
    } else {
      const response = await fetch(absolute)
      if (!response.ok) return null
      const blob = await response.blob()
      mime = blob.type || "image/png"
      dataUrl = await blobToDataUrl(blob)
      bytes = new Uint8Array(await blob.arrayBuffer())
    }

    const size = await probeImageSize(dataUrl)
    let type = mimeToDocxType(mime)
    if (!type) {
      const converted = await canvasToPngBytes(dataUrl)
      if (!converted) return null
      bytes = converted.bytes
      type = "png"
      dataUrl = converted.dataUrl
    }

    return {
      data: bytes,
      type,
      width: size.width,
      height: size.height,
      dataUrl,
    }
  } catch {
    return null
  }
}

/** Resolve bare attachment filenames, then fetch to data URLs for PDF/DOCX embed. */
export const materializeExportMarkdownImages = async (
  markdown: string,
  imageUrls?: CoworkImageUrlMap | null
): Promise<{ markdown: string; dataUrls: Record<string, string> }> => {
  const resolvedMarkdown = resolveCoworkPresentationMarkdown(markdown, imageUrls)
  const srcs = new Set<string>()
  for (const match of resolvedMarkdown.matchAll(imageInlineRe())) {
    if (match[2]) srcs.add(match[2])
  }
  const dataUrls: Record<string, string> = {}
  await Promise.all(
    [...srcs].map(async (src) => {
      const loaded = await loadExportImage(src)
      if (!loaded) return
      dataUrls[src] = loaded.dataUrl
    })
  )
  const rewritten =
    Object.keys(dataUrls).length === 0
      ? resolvedMarkdown
      : resolvedMarkdown.replace(imageInlineRe(), (full, alt: string, src: string) => {
          const dataUrl = dataUrls[src]
          if (!dataUrl) return full
          return `![${alt}](${dataUrl})`
        })
  return { markdown: rewritten, dataUrls }
}

const parseFenceOpen = (line: string) => {
  const match = OPEN_FENCE.exec(line)
  if (!match) return null
  return {
    char: match[2][0] ?? "`",
    len: match[2].length,
    language: match[3].trim().split(/\s+/)[0] ?? "",
  }
}

const isFenceClose = (
  line: string,
  fenceChar: string,
  fenceLen: number
): boolean => {
  const match = OPEN_FENCE.exec(line)
  if (!match) return false
  return (
    (match[2][0] ?? "") === fenceChar &&
    match[2].length >= fenceLen &&
    match[3].trim() === ""
  )
}

/** Split markdown into structural blocks (fences, lists, headings, …). */
export const parseMarkdownBlocks = (markdown: string): MdBlock[] => {
  const lines = markdown.replace(/\r\n/g, "\n").split("\n")
  const blocks: MdBlock[] = []
  let paragraph: string[] = []
  let listItems: string[] = []
  let listOrdered: boolean | null = null
  let fence: { char: string; len: number; language: string; lines: string[] } | null =
    null
  let quoteLines: string[] = []

  const flushParagraph = () => {
    if (paragraph.length === 0) return
    blocks.push({ type: "paragraph", text: paragraph.join("\n") })
    paragraph = []
  }
  const flushList = () => {
    if (listOrdered === null || listItems.length === 0) {
      listItems = []
      listOrdered = null
      return
    }
    blocks.push({ type: "list", ordered: listOrdered, items: [...listItems] })
    listItems = []
    listOrdered = null
  }
  const flushQuote = () => {
    if (quoteLines.length === 0) return
    blocks.push({ type: "blockquote", text: quoteLines.join("\n") })
    quoteLines = []
  }
  const flushAll = () => {
    flushParagraph()
    flushList()
    flushQuote()
  }

  for (const rawLine of lines) {
    const line = rawLine.trimEnd()

    if (fence) {
      if (isFenceClose(line, fence.char, fence.len)) {
        blocks.push({
          type: "code",
          language: fence.language,
          code: fence.lines.join("\n").replace(/\n$/, ""),
        })
        fence = null
      } else {
        fence.lines.push(rawLine.replace(/\n$/, ""))
      }
      continue
    }

    const open = parseFenceOpen(line)
    if (open) {
      flushAll()
      fence = { ...open, lines: [] }
      continue
    }

    const trimmed = line.trim()
    if (!trimmed) {
      flushAll()
      continue
    }

    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
      flushAll()
      blocks.push({ type: "hr" })
      continue
    }

    const heading = /^(#{1,3})\s+(.+)$/.exec(trimmed)
    if (heading) {
      flushAll()
      blocks.push({
        type: "heading",
        level: heading[1].length as 1 | 2 | 3,
        text: heading[2],
      })
      continue
    }

    const image = IMAGE_LINE_RE.exec(trimmed)
    if (image) {
      flushAll()
      blocks.push({ type: "image", alt: image[1], src: image[2] })
      continue
    }

    const quote = /^>\s?(.*)$/.exec(line)
    if (quote) {
      flushParagraph()
      flushList()
      quoteLines.push(quote[1])
      continue
    }
    if (quoteLines.length > 0) {
      flushQuote()
    }

    const unordered = /^[-*•]\s+(.+)$/.exec(trimmed)
    if (unordered) {
      flushParagraph()
      if (listOrdered === true) flushList()
      listOrdered = false
      listItems.push(unordered[1])
      continue
    }

    const ordered = /^\d+[.)]\s+(.+)$/.exec(trimmed)
    if (ordered) {
      flushParagraph()
      if (listOrdered === false) flushList()
      listOrdered = true
      listItems.push(ordered[1])
      continue
    }

    flushList()
    paragraph.push(trimmed)
  }

  if (fence) {
    blocks.push({
      type: "code",
      language: fence.language,
      code: fence.lines.join("\n").replace(/\n$/, ""),
    })
  }
  flushAll()
  return blocks
}

export const formatInlineMarkdown = (value: string) => {
  const imageSlots: string[] = []
  const withSlots = value.replace(
    imageInlineRe(),
    (_full, alt: string, src: string) => {
      const token = `\u0000IMG${imageSlots.length}\u0000`
      imageSlots.push(
        `<img src="${escapeHtml(absolutizeUrl(src))}" alt="${escapeHtml(alt)}" />`
      )
      return token
    }
  )
  let html = escapeHtml(withSlots)
  // Citations like [1] before links so [1](url) still becomes a link.
  html = html.replace(/\[(\d+)\](?!\()/g, "<sup>[$1]</sup>")
  html = html.replace(
    /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
    '<a href="$2">$1</a>'
  )
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>")
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
  html = html.replace(/(^|[^*])\*([^*]+)\*(?!\*)/g, "$1<em>$2</em>")
  // Soft line breaks inside a paragraph block.
  html = html.replace(/\n/g, "<br />")
  imageSlots.forEach((img, index) => {
    html = html.replaceAll(escapeHtml(`\u0000IMG${index}\u0000`), img)
  })
  return html
}

const renderImageHtml = (alt: string, src: string) =>
  `<figure class="pdf-figure"><img src="${escapeHtml(absolutizeUrl(src))}" alt="${escapeHtml(alt)}" /></figure>`

export const markdownToExportHtml = (markdown: string) => {
  const blocks = parseMarkdownBlocks(markdown)
  return blocks
    .map((block) => {
      switch (block.type) {
        case "heading":
          return `<h${block.level}>${formatInlineMarkdown(block.text)}</h${block.level}>`
        case "paragraph":
          return `<p>${formatInlineMarkdown(block.text)}</p>`
        case "blockquote":
          return `<blockquote><p>${formatInlineMarkdown(block.text)}</p></blockquote>`
        case "list": {
          const tag = block.ordered ? "ol" : "ul"
          return `<${tag}>${block.items
            .map((item) => `<li>${formatInlineMarkdown(item)}</li>`)
            .join("")}</${tag}>`
        }
        case "code": {
          const langClass = block.language
            ? ` class="language-${escapeHtml(block.language)}"`
            : ""
          return `<pre><code${langClass}>${escapeHtml(block.code)}</code></pre>`
        }
        case "image":
          return renderImageHtml(block.alt, block.src)
        case "hr":
          return "<hr />"
        default:
          return ""
      }
    })
    .join("")
}

const fileStemFromName = (fileName: string) => {
  const base = fileName.trim() || "document"
  return base.replace(/\.(md|markdown|txt|pdf|docx)$/i, "") || "document"
}

const buildSourceBlob = (markdown: string, extension: "md" | "txt") =>
  new Blob([markdown], {
    type: extension === "md" ? "text/markdown;charset=utf-8" : "text/plain;charset=utf-8",
  })

const CODE_FONT = "Consolas"

/**
 * Convert inline markdown tokens to docx TextRuns.
 * Link / bold / italic / code are mutually exclusive alternatives — check the
 * specific capture groups, never the outer alternation group (that caused
 * every token to render as `label (undefined)`).
 */
export const inlineMarkdownToRuns = (value: string, font: string): TextRun[] => {
  const runs: TextRun[] = []
  const tokenPattern =
    /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)|\*\*([^*]+)\*\*|\*([^*]+)\*|`([^`]+)`/g
  let cursor = 0
  for (const match of value.matchAll(tokenPattern)) {
    const index = match.index ?? 0
    if (index > cursor) {
      runs.push(new TextRun({ text: value.slice(cursor, index), font }))
    }
    if (match[2] !== undefined) {
      runs.push(
        new TextRun({
          text: `${match[1]} (${match[2]})`,
          font,
          underline: {},
        })
      )
    } else if (match[3] !== undefined) {
      runs.push(new TextRun({ text: match[3], font, bold: true }))
    } else if (match[4] !== undefined) {
      runs.push(new TextRun({ text: match[4], font, italics: true }))
    } else if (match[5] !== undefined) {
      runs.push(
        new TextRun({
          text: match[5],
          font: CODE_FONT,
          shading: { type: "clear", fill: "E8EAF0" },
        })
      )
    }
    cursor = index + match[0].length
  }
  if (cursor < value.length) {
    runs.push(new TextRun({ text: value.slice(cursor), font }))
  }
  return runs.length > 0 ? runs : [new TextRun({ text: "", font })]
}

const markdownToDocxParagraphs = async (
  markdown: string,
  font: string
): Promise<Paragraph[]> => {
  const blocks = parseMarkdownBlocks(markdown)
  const paragraphs: Paragraph[] = []
  const imageCache = new Map<string, LoadedExportImage | null>()

  const getImage = async (src: string) => {
    if (imageCache.has(src)) return imageCache.get(src) ?? null
    const loaded = await loadExportImage(src)
    imageCache.set(src, loaded)
    return loaded
  }

  for (const block of blocks) {
    switch (block.type) {
      case "heading": {
        const levelMap: Record<1 | 2 | 3, (typeof HeadingLevel)[keyof typeof HeadingLevel]> = {
          1: HeadingLevel.HEADING_1,
          2: HeadingLevel.HEADING_2,
          3: HeadingLevel.HEADING_3,
        }
        paragraphs.push(
          new Paragraph({
            heading: levelMap[block.level],
            children: inlineMarkdownToRuns(block.text, font),
          })
        )
        break
      }
      case "paragraph":
        paragraphs.push(
          new Paragraph({
            spacing: { after: 200 },
            children: inlineMarkdownToRuns(block.text.replace(/\n/g, " "), font),
          })
        )
        break
      case "blockquote":
        paragraphs.push(
          new Paragraph({
            spacing: { after: 200 },
            indent: { left: 360 },
            border: {
              left: { style: "single", size: 12, color: "C5CAD3", space: 8 },
            },
            children: inlineMarkdownToRuns(block.text.replace(/\n/g, " "), font),
          })
        )
        break
      case "list":
        for (const item of block.items) {
          paragraphs.push(
            new Paragraph({
              ...(block.ordered
                ? { numbering: { reference: "markdown-ordered-list", level: 0 } }
                : { bullet: { level: 0 } }),
              children: inlineMarkdownToRuns(item, font),
            })
          )
        }
        break
      case "code": {
        const codeLines = block.code.length > 0 ? block.code.split("\n") : [""]
        codeLines.forEach((line, index) => {
          paragraphs.push(
            new Paragraph({
              spacing: {
                before: index === 0 ? 120 : 0,
                after: index === codeLines.length - 1 ? 200 : 0,
              },
              shading: { type: "clear", fill: "0F172A" },
              children: [
                new TextRun({
                  text: line || " ",
                  font: CODE_FONT,
                  size: 18,
                  color: "E2E8F0",
                }),
              ],
            })
          )
        })
        break
      }
      case "image": {
        const loaded = await getImage(block.src)
        if (!loaded) {
          paragraphs.push(
            new Paragraph({
              spacing: { after: 200 },
              children: [
                new TextRun({
                  text: block.alt ? `[Image: ${block.alt}]` : "[Image]",
                  font,
                  italics: true,
                }),
              ],
            })
          )
          break
        }
        const size = fitImageSize(
          loaded.width,
          loaded.height,
          DOCX_IMAGE_MAX_WIDTH_PX
        )
        paragraphs.push(
          new Paragraph({
            spacing: { before: 120, after: 200 },
            children: [
              new ImageRun({
                type: loaded.type,
                data: loaded.data,
                transformation: { width: size.width, height: size.height },
                altText: {
                  title: block.alt || "Image",
                  description: block.alt || "Exported chat image",
                  name: block.alt || "image",
                },
              }),
            ],
          })
        )
        break
      }
      case "hr":
        paragraphs.push(
          new Paragraph({
            border: {
              bottom: { style: "single", size: 6, color: "D0D5DD", space: 1 },
            },
            spacing: { before: 200, after: 200 },
            children: [new TextRun({ text: "", font })],
          })
        )
        break
      default:
        break
    }
  }
  return paragraphs
}

const buildDocxBlob = async (
  markdown: string,
  imageUrls?: CoworkImageUrlMap | null
) => {
  const { markdown: resolved } = await materializeExportMarkdownImages(
    markdown,
    imageUrls
  )
  const bodyFont = "Calibri"
  const paragraphs = await markdownToDocxParagraphs(resolved, bodyFont)
  const document = new Document({
    styles: {
      default: {
        document: {
          run: { font: bodyFont, size: 24 },
        },
      },
    },
    numbering: {
      config: [
        {
          reference: "markdown-ordered-list",
          levels: [
            {
              level: 0,
              format: "decimal",
              text: "%1.",
              alignment: "start",
            },
          ],
        },
      ],
    },
    sections: [
      {
        children:
          paragraphs.length > 0
            ? paragraphs
            : [new Paragraph({ children: [new TextRun({ text: "", font: bodyFont, size: 24 })] })],
      },
    ],
  })
  return Packer.toBlob(document)
}

/** Safari ~16M px² / 16k edge; stay under so html2canvas does not return a 0×0 canvas. */
const MAX_CANVAS_EDGE = 16384
const MAX_CANVAS_AREA = 16_777_216

const collectParentFontFaceCss = () => {
  const rules: string[] = []
  for (const sheet of Array.from(document.styleSheets)) {
    try {
      for (const rule of Array.from(sheet.cssRules || [])) {
        if (rule instanceof CSSFontFaceRule) rules.push(rule.cssText)
      }
    } catch {
      // Cross-origin stylesheets throw on cssRules access.
    }
  }
  return rules.join("\n")
}

const resolveExportScale = (pageCssWidth: number, cssHeight: number, preferred: number) => {
  const height = Math.max(cssHeight, 1)
  const maxByEdge = MAX_CANVAS_EDGE / Math.max(pageCssWidth, height)
  const maxByArea = Math.sqrt(MAX_CANVAS_AREA / (pageCssWidth * height))
  return Math.max(0.5, Math.min(preferred, maxByEdge, maxByArea))
}

/**
 * Rasterize a self-contained `.pdf-page` HTML fragment.
 * Uses an in-viewport light iframe so dark-mode body color / off-screen hosts
 * cannot blank the capture (same class of bug as presentation export).
 */
export const rasterizePdfExportHtml = async (
  pageHtml: string,
  pageCssWidth: number
): Promise<HTMLCanvasElement> => {
  const iframe = document.createElement("iframe")
  iframe.setAttribute("aria-hidden", "true")
  iframe.tabIndex = -1
  iframe.style.cssText = [
    "position:fixed",
    "left:0",
    "top:0",
    `width:${pageCssWidth}px`,
    "height:1px",
    "opacity:0.01",
    "pointer-events:none",
    "border:0",
    "z-index:-1",
  ].join(";")
  document.body.appendChild(iframe)

  try {
    const idoc = iframe.contentDocument
    if (!idoc) throw new Error("PDF export iframe unavailable")

    idoc.open()
    idoc.write(`<!DOCTYPE html><html><head><meta charset="utf-8" />
<meta name="color-scheme" content="light" />
<base href="${escapeHtml(window.location.origin)}/" />
<style>${collectParentFontFaceCss()}</style>
</head><body style="margin:0;background:#ffffff;color:#070c1c;">${pageHtml}</body></html>`)
    idoc.close()

    const page = idoc.querySelector(".pdf-page") as HTMLElement | null
    if (!page) throw new Error("PDF export page missing")

    await Promise.all([
      idoc.fonts?.ready ?? Promise.resolve(),
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

    const cssHeight = Math.max(page.scrollHeight, page.clientHeight, 1)
    iframe.style.height = `${cssHeight}px`

    const preferredScale = Math.min(
      4,
      Math.max(2, Math.ceil((window.devicePixelRatio || 1) * 2))
    )
    const scale = resolveExportScale(pageCssWidth, cssHeight, preferredScale)
    const canvas = await html2canvas(page, {
      scale,
      backgroundColor: "#ffffff",
      useCORS: true,
      logging: false,
      windowWidth: pageCssWidth,
    })
    if (!canvas.width || !canvas.height) {
      throw new Error("PDF export produced an empty canvas")
    }
    return canvas
  } finally {
    iframe.remove()
  }
}

/** Slice a tall canvas into A4 pages (avoids soft downscale of one giant image). */
export const canvasToA4PdfBlob = (canvas: HTMLCanvasElement): Blob => {
  const doc = new jsPDF({ unit: "pt", format: "a4", compress: true })
  const pageWidth = doc.internal.pageSize.getWidth()
  const pageHeight = doc.internal.pageSize.getHeight()
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
  if (pageIndex === 0) {
    throw new Error("PDF export produced an empty document")
  }
  return doc.output("blob")
}

const buildPdfBlob = async (
  markdown: string,
  title: string,
  imageUrls?: CoworkImageUrlMap | null
) => {
  const pageCssWidth = 794
  const { markdown: resolved } = await materializeExportMarkdownImages(
    markdown,
    imageUrls
  )
  const bodyHtml = markdownToExportHtml(resolved)
  const pageHtml = `
    <style>
      .pdf-page {
        box-sizing: border-box;
        width: ${pageCssWidth}px;
        padding: 56px 64px 64px;
        background: #ffffff;
        color: #070c1c;
        -webkit-font-smoothing: antialiased;
        text-rendering: geometricPrecision;
      }
      .pdf-title {
        margin: 0 0 24px;
        font: 600 28px/1.25 ui-sans-serif, system-ui, sans-serif;
        letter-spacing: -0.02em;
      }
      .pdf-body {
        font: 400 15px/1.55 ui-sans-serif, system-ui, sans-serif;
      }
      ${markdownExportBodyCss}
    </style>
    <div class="pdf-page">
      ${title.trim() ? `<h1 class="pdf-title">${escapeHtml(title.trim())}</h1>` : ""}
      <div class="pdf-body">${bodyHtml || "<p></p>"}</div>
    </div>
  `
  const canvas = await rasterizePdfExportHtml(pageHtml, pageCssWidth)
  return canvasToA4PdfBlob(canvas)
}

export const exportCoworkMarkdown = async (
  markdown: string,
  options: {
    format: MarkdownExportFormat
    fileName?: string
    title?: string
    imageUrls?: CoworkImageUrlMap | null
  }
): Promise<{ blob: Blob; fileName: string }> => {
  const stem = fileStemFromName(options.fileName || options.title || "document")
  if (options.format === "md" || options.format === "txt") {
    return {
      blob: buildSourceBlob(markdown, options.format),
      fileName: `${stem}.${options.format}`,
    }
  }
  if (options.format === "docx") {
    return {
      blob: await buildDocxBlob(markdown, options.imageUrls),
      fileName: `${stem}.docx`,
    }
  }
  return {
    blob: await buildPdfBlob(markdown, options.title || stem, options.imageUrls),
    fileName: `${stem}.pdf`,
  }
}
