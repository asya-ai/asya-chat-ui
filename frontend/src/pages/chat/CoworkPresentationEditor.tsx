import { useEffect, useMemo, useRef, useState } from "react"
import { Marp } from "@marp-team/marp-core"
import { Code2, Presentation } from "lucide-react"
import CodeMirror from "@uiw/react-codemirror"
import { EditorView } from "@codemirror/view"
import { markdown } from "@codemirror/lang-markdown"
import { oneDark } from "@codemirror/theme-one-dark"

import { Button } from "@/components/ui/button"
import { getTheme, type ThemeMode } from "@/lib/theme"
import { cn } from "@/lib/utils"

type CoworkPresentationEditorProps = {
  value: string
  readOnly?: boolean
  className?: string
  onChange: (value: string) => void
}

type PresentationMode = "slides" | "source"

const DEFAULT_MARP_FRONT_MATTER = `---
marp: true
theme: gaia
_class: lead
paginate: true
size: 16:9
style: |
  section {
    font-size: 28px;
    padding: 52px 56px;
  }
  h1 { font-size: 1.7em; }
  h2 { font-size: 1.35em; }
  table {
    font-size: 0.72em;
    width: 100%;
  }
  th, td {
    padding: 0.35em 0.5em;
  }
  footer {
    font-size: 0.45em;
    color: #667;
  }
---`

/**
 * Isolate slides from the app document: no Tailwind preflight, no dark-mode
 * inherited colors/fonts. Marp theme CSS owns the look.
 */
const SHADOW_RESET_CSS = `
:host {
  display: block;
  width: 100%;
  min-width: 0;
  color-scheme: only light;
  color: #111;
  background: transparent;
  font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
  font-size: 16px;
  font-weight: 400;
  font-style: normal;
  line-height: 1.5;
  letter-spacing: normal;
  text-align: left;
  text-transform: none;
  text-decoration: none;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}
.cowork-marp-host,
.cowork-marp-host * {
  box-sizing: border-box;
}
.cowork-marp-host a {
  color: inherit;
}
`

/** Preview-only layout: stack every slide like PDF pages. */
const PREVIEW_LAYOUT_CSS = `
.cowork-marp-host {
  display: block;
  width: 100%;
  min-width: 0;
  color-scheme: only light;
}
.cowork-marp-host .marpit,
div.marpit {
  display: flex !important;
  flex-direction: column;
  gap: 1.25rem;
  align-items: stretch;
  width: 100% !important;
  max-width: 100% !important;
  min-width: 0 !important;
  height: auto !important;
  margin: 0 !important;
  padding: 0 !important;
}
/* Card chrome lives on HTML, not the Marp SVG. A white SVG backdrop plus
   border-radius leaks a 1px seam on dark slides (subpixel + anti-alias). */
.cowork-marp-slide {
  display: block;
  width: 100%;
  max-width: 52rem;
  margin: 0 auto;
  overflow: hidden;
  border-radius: 0.5rem;
  line-height: 0;
  box-shadow:
    0 1px 2px rgb(0 0 0 / 0.06),
    0 8px 24px rgb(0 0 0 / 0.08);
}
.cowork-marp-host .marpit > .cowork-marp-slide > svg[data-marpit-svg],
div.marpit > .cowork-marp-slide > svg[data-marpit-svg] {
  display: block !important;
  width: 100% !important;
  max-width: none !important;
  height: auto !important;
  max-height: none !important;
  margin: 0 !important;
  background: transparent !important;
  overflow: hidden;
}
.cowork-marp-mermaid {
  display: flex;
  justify-content: center;
  align-items: center;
  width: 100%;
  max-height: 420px;
  margin: 0.4em 0 0.6em;
  overflow: hidden;
}
.cowork-marp-mermaid svg {
  max-width: 100%;
  max-height: 420px;
  height: auto !important;
}
`

const useAppTheme = (): ThemeMode => {
  const [theme, setTheme] = useState<ThemeMode>(() => getTheme())
  useEffect(() => {
    const sync = () => setTheme(getTheme())
    const observer = new MutationObserver(sync)
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    })
    return () => observer.disconnect()
  }, [])
  return theme
}

const ensureMarpFrontMatter = (markdown: string) => {
  const trimmed = markdown.trimStart()
  if (trimmed.startsWith("---")) return markdown
  return `${DEFAULT_MARP_FRONT_MATTER}\n\n${markdown}`
}

const ensureShadowRoot = (host: HTMLElement): ShadowRoot => {
  if (host.shadowRoot) return host.shadowRoot
  return host.attachShadow({ mode: "open" })
}

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

let coworkMermaidReady = false
let coworkMermaidSeq = 0

const loadCoworkMermaid = async () => {
  const mermaid = (await import("mermaid")).default
  if (!coworkMermaidReady) {
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: "loose",
    })
    coworkMermaidReady = true
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
  // Prefer dark charts on deck slides without fighting global mermaid init used by chat.
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

  const mermaid = await loadCoworkMermaid()
  for (const codeEl of codeNodes) {
    const source = mermaidSourceFromCodeEl(codeEl)
    if (!source) continue
    const pre = codeEl.closest("pre")
    if (!pre) continue
    coworkMermaidSeq += 1
    const renderId = `cowork-marp-mermaid-${coworkMermaidSeq}`
    try {
      const { svg } = await mermaid.render(renderId, source)
      const wrap = document.createElement("div")
      wrap.className = "cowork-marp-mermaid"
      wrap.innerHTML = svg
      pre.replaceWith(wrap)
    } catch {
      // Keep the original code fence if Mermaid cannot parse it.
    }
  }
}

export const CoworkPresentationEditor = ({
  value,
  readOnly = false,
  className,
  onChange,
}: CoworkPresentationEditorProps) => {
  const theme = useAppTheme()
  const isDark = theme === "dark"
  const [mode, setMode] = useState<PresentationMode>("slides")
  const previewHostRef = useRef<HTMLDivElement | null>(null)
  const browserCleanupRef = useRef<(() => void) | null>(null)

  const marp = useMemo(
    () =>
      new Marp({
        html: true,
        script: false,
        // App owns SVG chrome (radius/shadow); don't let theme ::backdrop paint it.
        inlineSVG: { backdropSelector: false },
      }),
    []
  )

  const rendered = useMemo(() => {
    try {
      return marp.render(ensureMarpFrontMatter(value || "# Untitled\n"))
    } catch (error) {
      return {
        html: `<section><pre>${String(error)}</pre></section>`,
        css: "",
        comments: [] as string[][],
      }
    }
  }, [marp, value])

  const slideCount = useMemo(() => {
    if (typeof document === "undefined") return 1
    const template = document.createElement("div")
    template.innerHTML = rendered.html
    const count = template.querySelectorAll("section").length
    return Math.max(1, count)
  }, [rendered.html])

  useEffect(() => {
    const host = previewHostRef.current
    if (!host || mode !== "slides") return

    const shadow = ensureShadowRoot(host)
    shadow.innerHTML = [
      `<style>${SHADOW_RESET_CSS}\n${rendered.css}\n${PREVIEW_LAYOUT_CSS}</style>`,
      `<div class="cowork-marp-host">${rendered.html}</div>`,
    ].join("")

    for (const svg of shadow.querySelectorAll(
      ".cowork-marp-host .marpit > svg[data-marpit-svg]"
    )) {
      const wrap = document.createElement("div")
      wrap.className = "cowork-marp-slide"
      svg.replaceWith(wrap)
      wrap.appendChild(svg)
    }

    let cancelled = false
    void (async () => {
      const [{ browser }] = await Promise.all([
        import("@marp-team/marp-core/browser.js"),
        renderMermaidInRoot(shadow),
      ])
      if (cancelled || !previewHostRef.current?.shadowRoot) return
      browserCleanupRef.current?.()
      const root = previewHostRef.current.shadowRoot
      const api = browser(root)
      requestAnimationFrame(() => {
        if (cancelled) return
        api.update()
      })
      browserCleanupRef.current = () => api.cleanup()
    })()

    return () => {
      cancelled = true
      browserCleanupRef.current?.()
      browserCleanupRef.current = null
    }
  }, [mode, rendered.css, rendered.html])

  return (
    <div className={cn("flex min-h-0 flex-1 flex-col overflow-hidden bg-background", className)}>
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-border px-3 py-2">
        <div className="flex items-center gap-1 rounded-lg bg-muted p-0.5">
          <Button
            type="button"
            size="sm"
            variant={mode === "slides" ? "secondary" : "ghost"}
            className="h-7 gap-1 px-2.5 text-xs"
            onClick={() => setMode("slides")}
          >
            <Presentation className="size-3.5" aria-hidden="true" />
            Slides
          </Button>
          <Button
            type="button"
            size="sm"
            variant={mode === "source" ? "secondary" : "ghost"}
            className="h-7 gap-1 px-2.5 text-xs"
            onClick={() => setMode("source")}
          >
            <Code2 className="size-3.5" aria-hidden="true" />
            Source
          </Button>
        </div>
        {mode === "slides" ? (
          <span className="text-xs text-muted-foreground">
            {slideCount} {slideCount === 1 ? "slide" : "slides"} · scroll
          </span>
        ) : (
          <span className="text-xs text-muted-foreground">Marp markdown</span>
        )}
      </div>

      {mode === "slides" ? (
        <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden bg-neutral-200 p-4 dark:bg-neutral-900">
          <div
            ref={previewHostRef}
            className="cowork-marp-preview mx-auto w-full max-w-4xl"
            data-color-scheme="light"
          />
        </div>
      ) : (
        <div className="min-h-0 flex-1 overflow-hidden">
          <CodeMirror
            value={value}
            height="100%"
            theme={isDark ? "dark" : "light"}
            editable={!readOnly}
            extensions={[
              markdown(),
              EditorView.lineWrapping,
              ...(isDark ? [oneDark] : []),
              EditorView.theme({
                "&": { height: "100%", fontSize: "13px", backgroundColor: "transparent" },
                ".cm-scroller": {
                  overflow: "auto",
                  fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
                },
                ".cm-gutters": { background: "transparent", border: "none" },
              }),
            ]}
            basicSetup={{
              lineNumbers: true,
              foldGutter: true,
              highlightActiveLine: true,
              autocompletion: false,
            }}
            onChange={onChange}
            className="h-full bg-background [&_.cm-editor]:h-full [&_.cm-editor]:bg-background [&_.cm-editor]:outline-none"
          />
        </div>
      )}
    </div>
  )
}
