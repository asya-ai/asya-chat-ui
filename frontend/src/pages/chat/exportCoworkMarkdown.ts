import { Document, Packer, Paragraph, TextRun } from "docx"
import html2canvas from "html2canvas"
import { jsPDF } from "jspdf"

export type MarkdownExportFormat = "md" | "txt" | "pdf" | "docx"

const escapeHtml = (value: string) =>
  value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")

const formatInlineMarkdown = (value: string) => {
  let html = escapeHtml(value)
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>")
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
  html = html.replace(/\*([^*]+)\*/g, "<em>$1</em>")
  html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2">$1</a>')
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
    if (!listType || listItems.length === 0) return
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

const fileStemFromName = (fileName: string) => {
  const base = fileName.trim() || "document"
  return base.replace(/\.(md|markdown|txt|pdf|docx)$/i, "") || "document"
}

const buildSourceBlob = (markdown: string, extension: "md" | "txt") =>
  new Blob([markdown], {
    type: extension === "md" ? "text/markdown;charset=utf-8" : "text/plain;charset=utf-8",
  })

const buildDocxBlob = async (markdown: string) => {
  const bodyFont = "Calibri"
  const paragraphs = markdown.split(/\n/).map(
    (line) =>
      new Paragraph({
        children: [new TextRun({ text: line, size: 24, font: bodyFont })],
      })
  )
  const document = new Document({
    styles: {
      default: {
        document: {
          run: { font: bodyFont, size: 24 },
        },
      },
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

const buildPdfBlob = async (markdown: string, title: string) => {
  const pageCssWidth = 794
  const bodyHtml = markdownToExportHtml(markdown)
  const root = document.createElement("div")
  root.setAttribute("data-cowork-md-pdf-export", "true")
  root.style.cssText = `position:fixed;left:-10000px;top:0;width:${pageCssWidth}px;pointer-events:none;z-index:-1;`
  root.innerHTML = `
    <style>
      .pdf-page {
        box-sizing: border-box;
        width: ${pageCssWidth}px;
        padding: 56px 64px 64px;
        background: #ffffff;
        color: #1a1210;
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
        padding-left: 22px;
      }
      .pdf-body li { margin: 0 0 6px; }
      .pdf-body strong { font-weight: 700; }
      .pdf-body em { font-style: italic; }
      .pdf-body code {
        font: 13px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace;
        background: rgba(25, 9, 6, 0.06);
        padding: 0 4px;
        border-radius: 4px;
      }
      .pdf-body a { color: inherit; }
    </style>
    <div class="pdf-page">
      ${title.trim() ? `<h1 class="pdf-title">${escapeHtml(title.trim())}</h1>` : ""}
      <div class="pdf-body">${bodyHtml || "<p></p>"}</div>
    </div>
  `
  document.body.appendChild(root)

  try {
    try {
      await document.fonts.ready
    } catch {
      // Ignore font readiness failures.
    }
    const page = root.querySelector(".pdf-page") as HTMLElement
    const canvas = await html2canvas(page, {
      backgroundColor: "#ffffff",
      scale: 2,
      useCORS: true,
      logging: false,
    })

    const pageWidth = 595.28
    const pageHeight = 841.89
    const imgWidth = pageWidth
    const imgHeight = (canvas.height * imgWidth) / canvas.width
    const doc = new jsPDF({ unit: "pt", format: "a4", compress: true })
    let heightLeft = imgHeight
    let position = 0
    const imgData = canvas.toDataURL("image/png")

    doc.addImage(imgData, "PNG", 0, position, imgWidth, imgHeight)
    heightLeft -= pageHeight
    while (heightLeft > 0) {
      position = heightLeft - imgHeight
      doc.addPage()
      doc.addImage(imgData, "PNG", 0, position, imgWidth, imgHeight)
      heightLeft -= pageHeight
    }
    return doc.output("blob")
  } finally {
    root.remove()
  }
}

export const exportCoworkMarkdown = async (
  markdown: string,
  options: {
    format: MarkdownExportFormat
    fileName?: string
    title?: string
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
      blob: await buildDocxBlob(markdown),
      fileName: `${stem}.docx`,
    }
  }
  return {
    blob: await buildPdfBlob(markdown, options.title || stem),
    fileName: `${stem}.pdf`,
  }
}
