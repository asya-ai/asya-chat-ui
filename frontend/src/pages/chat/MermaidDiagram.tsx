import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react"
import { Maximize2, RotateCcw, XIcon, ZoomIn, ZoomOut } from "lucide-react"
import {
  TransformComponent,
  TransformWrapper,
  useControls,
} from "react-zoom-pan-pinch"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogTitle,
} from "@/components/ui/dialog"
import { useI18n } from "@/lib/i18n-context"
import {
  diagnoseMermaidChart,
  isIncompleteMermaidPrefix,
  prepareMermaidChart,
  splitMermaidCharts,
} from "@/lib/sanitizeMermaid"

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

const toolbarStickyClassName =
  "sticky top-0 z-10 flex h-0 justify-end overflow-visible pointer-events-none"

const toolbarButtonClassName =
  "pointer-events-auto h-7 px-2 bg-background/80 border border-muted-foreground/30 text-[10px] text-muted-foreground hover:text-foreground uppercase tracking-wide"

const explorerCloseClassName =
  "ring-offset-background focus:ring-ring rounded-full bg-muted/80 border border-muted-foreground/30 p-1.5 text-foreground opacity-80 shadow-sm transition-opacity hover:bg-muted hover:opacity-100 focus:ring-2 focus:ring-offset-2 focus:outline-hidden disabled:pointer-events-none [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4"

const cloneInlineMermaidInto = (
  target: HTMLElement,
  source: HTMLElement | null
): boolean => {
  if (!source || source.childElementCount === 0) return false
  target.replaceChildren(
    ...Array.from(source.childNodes).map((node) => node.cloneNode(true))
  )
  return true
}

const renderMermaidInto = async (
  container: HTMLElement,
  chart: string,
  options: { cancelled: () => boolean; wrapClassName: string }
) => {
  container.innerHTML = ""
  if (isIncompleteMermaidPrefix(chart)) return
  const charts = splitMermaidCharts(chart)
  if (charts.length === 0) return

  const mermaid = await loadMermaid()
  for (const part of charts) {
    if (options.cancelled()) return
    const chartToRender = await prepareMermaidChart(part)
    if (options.cancelled()) return
    const wrap = document.createElement("div")
    wrap.className = options.wrapClassName
    const { svg, bindFunctions } = await mermaid.render(
      nextMermaidRenderId(),
      chartToRender
    )
    if (options.cancelled()) return
    wrap.innerHTML = svg
    container.appendChild(wrap)
    bindFunctions?.(wrap)
  }
}

const MermaidCopyButton = ({
  text,
  label,
  copiedLabel,
}: {
  text: string
  label: string
  copiedLabel: string
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

  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      className={toolbarButtonClassName}
      aria-label={copied ? copiedLabel : label}
      onClick={() => {
        void (async () => {
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
            // Keep idle label if clipboard fails.
          }
        })()
      }}
    >
      <span
        key={copied ? "copied" : "idle"}
        className="inline-block animate-in duration-200 fade-in zoom-in-95"
      >
        {copied ? copiedLabel : label}
      </span>
    </Button>
  )
}

const ExplorerZoomControls = ({
  zoomInLabel,
  zoomOutLabel,
  zoomResetLabel,
}: {
  zoomInLabel: string
  zoomOutLabel: string
  zoomResetLabel: string
}) => {
  const { zoomIn, zoomOut, resetTransform } = useControls()
  return (
    <div className="pointer-events-auto flex items-center gap-1">
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="h-8 w-8 bg-background/80 border border-muted-foreground/30 text-muted-foreground hover:text-foreground"
        aria-label={zoomInLabel}
        onClick={() => zoomIn()}
      >
        <ZoomIn className="h-4 w-4" />
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="h-8 w-8 bg-background/80 border border-muted-foreground/30 text-muted-foreground hover:text-foreground"
        aria-label={zoomOutLabel}
        onClick={() => zoomOut()}
      >
        <ZoomOut className="h-4 w-4" />
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="h-8 w-8 bg-background/80 border border-muted-foreground/30 text-muted-foreground hover:text-foreground"
        aria-label={zoomResetLabel}
        onClick={() => resetTransform()}
      >
        <RotateCcw className="h-4 w-4" />
      </Button>
    </div>
  )
}

/** Recenter after async Mermaid SVG lands in the transform content. */
const FitWhenReady = ({
  ready,
  target,
}: {
  ready: boolean
  target: HTMLDivElement | null
}) => {
  const { zoomToElement, centerView } = useControls()
  useEffect(() => {
    if (!ready) return
    const id = window.requestAnimationFrame(() => {
      if (target && target.childElementCount > 0) {
        void zoomToElement(target, 1, 0)
        return
      }
      void centerView(1, 0)
    })
    return () => window.cancelAnimationFrame(id)
  }, [ready, centerView, zoomToElement, target])
  return null
}

export const MermaidDiagram = ({ chart }: { chart: string }) => {
  const { t } = useI18n()
  const containerRef = useRef<HTMLDivElement | null>(null)
  const [explorerHost, setExplorerHost] = useState<HTMLDivElement | null>(null)
  const handleExplorerHostRef = useCallback((node: HTMLDivElement | null) => {
    setExplorerHost(node)
  }, [])
  const [renderError, setRenderError] = useState<string | null>(null)
  const [explorerOpen, setExplorerOpen] = useState(false)
  const [explorerError, setExplorerError] = useState<string | null>(null)
  const [explorerReady, setExplorerReady] = useState(false)

  const copyLabel = t("chat_copy_mermaid")
  const copiedLabel = t("common_copied")
  const renderFailedLabel = t("chat_mermaid_render_failed")
  const expandLabel = t("chat_expand_mermaid")
  const zoomInLabel = t("chat_mermaid_zoom_in")
  const zoomOutLabel = t("chat_mermaid_zoom_out")
  const zoomResetLabel = t("chat_mermaid_zoom_reset")

  useEffect(() => {
    let cancelled = false
    const run = async () => {
      const container = containerRef.current
      if (!container) return
      setRenderError(null)
      try {
        await renderMermaidInto(container, chart, {
          cancelled: () => cancelled,
          wrapClassName:
            "max-w-full [&_svg]:max-w-none [&_svg]:h-auto overflow-x-auto",
        })
      } catch (error) {
        if (cancelled) return
        const maidMsg = await diagnoseMermaidChart(chart)
        if (cancelled) return
        setRenderError(
          maidMsg ??
            (error instanceof Error ? error.message : renderFailedLabel)
        )
      }
    }
    void run()
    return () => {
      cancelled = true
    }
  }, [chart, renderFailedLabel])

  useEffect(() => {
    if (!explorerOpen) {
      setExplorerReady(false)
      setExplorerError(null)
      return
    }
    if (!explorerHost) return
    let cancelled = false
    const run = async () => {
      setExplorerReady(false)
      setExplorerError(null)
      const container = explorerHost
      try {
        if (cloneInlineMermaidInto(container, containerRef.current)) {
          if (!cancelled) setExplorerReady(true)
          return
        }
        await renderMermaidInto(container, chart, {
          cancelled: () => cancelled,
          wrapClassName:
            "[&_svg]:max-w-none [&_svg]:h-auto [&_svg]:w-auto select-none",
        })
        if (cancelled) return
        if (container.childElementCount === 0) {
          setExplorerError(renderFailedLabel)
          return
        }
        setExplorerReady(true)
      } catch (error) {
        if (cancelled) return
        const maidMsg = await diagnoseMermaidChart(chart)
        if (cancelled) return
        setExplorerError(
          maidMsg ??
            (error instanceof Error ? error.message : renderFailedLabel)
        )
      }
    }
    void run()
    return () => {
      cancelled = true
    }
  }, [explorerOpen, explorerHost, chart, renderFailedLabel])

  return (
    <>
      <div className="relative my-3">
        <div className={toolbarStickyClassName}>
          <div className="pointer-events-auto m-2 flex items-center gap-1">
            <MermaidCopyButton
              text={chart}
              label={copyLabel}
              copiedLabel={copiedLabel}
            />
            {!renderError && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className={toolbarButtonClassName}
                aria-label={expandLabel}
                onClick={() => setExplorerOpen(true)}
              >
                <Maximize2 className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
                {expandLabel}
              </Button>
            )}
          </div>
        </div>
        {renderError ? (
          <pre className="bg-destructive/10 p-2 pt-10 rounded text-destructive text-xs whitespace-pre-wrap">
            {renderError}
          </pre>
        ) : (
          <div ref={containerRef} className="flex flex-col gap-3 pt-10" />
        )}
      </div>

      <Dialog open={explorerOpen} onOpenChange={setExplorerOpen}>
        <DialogContent
          showCloseButton={false}
          className="flex h-[calc(100vh-2rem)] w-[calc(100vw-2rem)] max-w-[calc(100vw-2rem)] flex-col gap-0 overflow-hidden p-0 sm:max-w-[calc(100vw-2rem)]"
        >
          <DialogTitle className="sr-only">{expandLabel}</DialogTitle>
          <div className="relative z-30 flex shrink-0 items-center gap-2 border-b border-border px-3 py-2">
            <MermaidCopyButton
              text={chart}
              label={copyLabel}
              copiedLabel={copiedLabel}
            />
            <div className="flex-1" />
            <DialogClose className={explorerCloseClassName}>
              <XIcon />
              <span className="sr-only">{t("common_close")}</span>
            </DialogClose>
          </div>
          <div className="relative min-h-0 flex-1">
            {explorerError ? (
              <pre className="m-4 overflow-auto rounded bg-destructive/10 p-3 text-destructive text-xs whitespace-pre-wrap">
                {explorerError}
              </pre>
            ) : (
              <TransformWrapper
                initialScale={1}
                minScale={0.25}
                maxScale={8}
                centerOnInit
                wheel={{ step: 0.12 }}
                pinch={{ step: 4 }}
                doubleClick={{ mode: "zoomIn", step: 0.4 }}
              >
                <FitWhenReady ready={explorerReady} target={explorerHost} />
                <div className="pointer-events-none absolute top-3 left-1/2 z-20 -translate-x-1/2">
                  <ExplorerZoomControls
                    zoomInLabel={zoomInLabel}
                    zoomOutLabel={zoomOutLabel}
                    zoomResetLabel={zoomResetLabel}
                  />
                </div>
                <TransformComponent
                  wrapperClass="!h-full !w-full"
                  contentClass="!flex !h-full !w-full !items-center !justify-center"
                  wrapperStyle={{ width: "100%", height: "100%" }}
                >
                  <div
                    ref={handleExplorerHostRef}
                    className="flex flex-col gap-6 p-4"
                  />
                </TransformComponent>
              </TransformWrapper>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}
