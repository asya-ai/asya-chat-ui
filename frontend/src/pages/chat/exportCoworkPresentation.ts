import { Marp } from "@marp-team/marp-core"
import html2canvas from "html2canvas"
import { jsPDF } from "jspdf"
import PptxGenJS from "pptxgenjs"

export type PresentationExportFormat = "pdf" | "pptx"

const SLIDE_WIDTH = 1280
const SLIDE_HEIGHT = 720

const DEFAULT_MARP_FRONT_MATTER = `---
marp: true
theme: gaia
paginate: true
size: 16:9
---`

const MERMAID_FENCE_LANGS = new Set([
  "mermaid",
  "mmd",
  "xychart",
  "xychart-beta",
  "flowchart",
  "graph",
  "sequence",
  "sequencediagram",
  "pie",
  "gantt",
  "journey",
  "timeline",
  "quadrantchart",
  "mindmap",
  "sankey-beta",
  "block-beta",
])

let mermaidReady = false
let mermaidSeq = 0

const ensureMarpFrontMatter = (markdown: string) => {
  const trimmed = markdown.trimStart()
  if (trimmed.startsWith("---")) return markdown
  return `${DEFAULT_MARP_FRONT_MATTER}\n\n${markdown}`
}

const loadMermaid = async () => {
  const mermaid = (await import("mermaid")).default
  if (!mermaidReady) {
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: "loose",
    })
    mermaidReady = true
  }
  return mermaid
}

const mermaidSourceFromCodeEl = (codeEl: HTMLElement): string | null => {
  const langClass = [...codeEl.classList].find((name) => name.startsWith("language-"))
  const lang = (langClass?.slice("language-".length) || "").toLowerCase()
  if (!lang || !MERMAID_FENCE_LANGS.has(lang)) return null
  const body = (codeEl.textContent || "").replace(/\r\n/g, "\n").trim()
  if (!body) return null
  let source =
    lang === "mermaid" || lang === "mmd"
      ? body
      : (() => {
          const first = body.split("\n", 1)[0]?.trim().toLowerCase() ?? ""
          if (first === lang || first.startsWith(`${lang} `)) return body
          return `${lang}\n${body}`
        })()
  if (!/%%\{\s*init\s*:/i.test(source)) {
    source = `%%{init: {'theme':'dark'}}%%\n${source}`
  }
  return source
}

const renderMermaidInRoot = async (root: ParentNode) => {
  const codeNodes = Array.from(
    root.querySelectorAll("pre code[class*='language-']")
  ) as HTMLElement[]
  if (codeNodes.length === 0) return
  const mermaid = await loadMermaid()
  for (const codeEl of codeNodes) {
    const source = mermaidSourceFromCodeEl(codeEl)
    if (!source) continue
    const pre = codeEl.closest("pre")
    if (!pre) continue
    mermaidSeq += 1
    try {
      const { svg } = await mermaid.render(`cowork-export-mermaid-${mermaidSeq}`, source)
      const wrap = document.createElement("div")
      wrap.className = "cowork-marp-mermaid"
      wrap.innerHTML = svg
      pre.replaceWith(wrap)
    } catch {
      // Keep code fence if Mermaid fails.
    }
  }
}

const fileStemFromName = (fileName: string) => {
  const base = fileName.trim() || "presentation"
  return base.replace(/\.(md|markdown|pptx|pdf)$/i, "") || "presentation"
}

const waitForFonts = async () => {
  try {
    await document.fonts.ready
  } catch {
    // Ignore font readiness failures.
  }
  await new Promise((resolve) => window.setTimeout(resolve, 50))
}

const rasterizeSlides = async (markdown: string): Promise<string[]> => {
  const marp = new Marp({
    html: true,
    script: false,
    inlineSVG: false,
  })
  const { html, css } = marp.render(ensureMarpFrontMatter(markdown || "# Untitled\n"))

  const host = document.createElement("div")
  host.setAttribute("data-cowork-marp-export", "true")
  host.style.cssText = [
    "position: fixed",
    "left: -100000px",
    "top: 0",
    "width: 1280px",
    "pointer-events: none",
    "opacity: 1",
    "z-index: -1",
  ].join(";")

  const styleEl = document.createElement("style")
  styleEl.textContent = `
    ${css}
    [data-cowork-marp-export] .marpit {
      width: ${SLIDE_WIDTH}px;
      margin: 0;
      padding: 0;
    }
    [data-cowork-marp-export] .marpit > section {
      display: block !important;
      width: ${SLIDE_WIDTH}px !important;
      height: ${SLIDE_HEIGHT}px !important;
      margin: 0 !important;
      box-sizing: border-box !important;
      overflow: hidden !important;
    }
    [data-cowork-marp-export] .cowork-marp-mermaid {
      display: flex;
      justify-content: center;
      align-items: center;
      width: 100%;
      max-height: 420px;
      overflow: hidden;
    }
    [data-cowork-marp-export] .cowork-marp-mermaid svg {
      max-width: 100%;
      max-height: 420px;
      height: auto !important;
    }
  `
  host.appendChild(styleEl)

  const deck = document.createElement("div")
  deck.innerHTML = html
  host.appendChild(deck)
  document.body.appendChild(host)

  try {
    await renderMermaidInRoot(host)
    await waitForFonts()

    const sections = Array.from(host.querySelectorAll(".marpit > section")) as HTMLElement[]
    if (sections.length === 0) {
      throw new Error("No slides found to export")
    }

    const images: string[] = []
    for (const section of sections) {
      const canvas = await html2canvas(section, {
        backgroundColor: null,
        scale: 1,
        width: SLIDE_WIDTH,
        height: SLIDE_HEIGHT,
        windowWidth: SLIDE_WIDTH,
        windowHeight: SLIDE_HEIGHT,
        logging: false,
        useCORS: true,
      })
      images.push(canvas.toDataURL("image/png"))
    }
    return images
  } finally {
    host.remove()
  }
}

const buildPdf = (slideImages: string[]) => {
  const doc = new jsPDF({
    orientation: "landscape",
    unit: "pt",
    format: [SLIDE_WIDTH, SLIDE_HEIGHT],
    compress: true,
  })
  slideImages.forEach((image, index) => {
    if (index > 0) doc.addPage([SLIDE_WIDTH, SLIDE_HEIGHT], "landscape")
    doc.addImage(image, "PNG", 0, 0, SLIDE_WIDTH, SLIDE_HEIGHT)
  })
  return doc.output("blob")
}

const buildPptx = async (slideImages: string[]) => {
  const pptx = new PptxGenJS()
  pptx.defineLayout({ name: "COWORK_16x9", width: 13.333, height: 7.5 })
  pptx.layout = "COWORK_16x9"
  for (const image of slideImages) {
    const slide = pptx.addSlide()
    slide.addImage({
      data: image,
      x: 0,
      y: 0,
      w: "100%",
      h: "100%",
    })
  }
  const output = await pptx.write({ outputType: "blob" })
  return output as Blob
}

export const exportCoworkPresentation = async (
  markdown: string,
  options: { format: PresentationExportFormat; fileName?: string }
): Promise<{ blob: Blob; fileName: string }> => {
  const slideImages = await rasterizeSlides(markdown)
  const stem = fileStemFromName(options.fileName || "presentation")
  if (options.format === "pdf") {
    return {
      blob: buildPdf(slideImages),
      fileName: `${stem}.pdf`,
    }
  }
  return {
    blob: await buildPptx(slideImages),
    fileName: `${stem}.pptx`,
  }
}
