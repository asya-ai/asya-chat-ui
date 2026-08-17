import { lazy, Suspense, useEffect, useState } from "react"
import CodeMirror from "@uiw/react-codemirror"
import { EditorView } from "@codemirror/view"
import { oneDark } from "@codemirror/theme-one-dark"
import { markdown } from "@codemirror/lang-markdown"
import { javascript } from "@codemirror/lang-javascript"
import { python } from "@codemirror/lang-python"
import { json } from "@codemirror/lang-json"
import { html } from "@codemirror/lang-html"
import { css } from "@codemirror/lang-css"
import { sql } from "@codemirror/lang-sql"
import type { Extension } from "@codemirror/state"

import { CoworkMarkdownEditor } from "@/pages/chat/CoworkMarkdownEditor"
import { getTheme, type ThemeMode } from "@/lib/theme"
import { cn } from "@/lib/utils"

const CoworkPresentationEditor = lazy(() =>
  import("@/pages/chat/CoworkPresentationEditor").then((mod) => ({
    default: mod.CoworkPresentationEditor,
  }))
)

type CoworkEditorProps = {
  value: string
  language?: string | null
  format?: string | null
  readOnly?: boolean
  className?: string
  onChange: (value: string) => void
}

const languageExtension = (format?: string | null, language?: string | null): Extension[] => {
  const key = (language || format || "").toLowerCase()
  if (format === "markdown" || key === "markdown" || key === "md") return [markdown()]
  if (format === "json" || key === "json") return [json()]
  if (key === "python" || key === "py") return [python()]
  if (key === "html") return [html()]
  if (key === "css" || key === "scss") return [css()]
  if (key === "sql") return [sql()]
  if (
    key === "javascript" ||
    key === "js" ||
    key === "typescript" ||
    key === "ts" ||
    key === "tsx" ||
    key === "jsx"
  ) {
    return [
      javascript({
        jsx: key === "jsx" || key === "tsx",
        typescript: key === "ts" || key === "tsx",
      }),
    ]
  }
  if (format === "csv") return []
  return []
}

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

const isMarkdownFormat = (format?: string | null, language?: string | null) => {
  const key = (format || language || "").toLowerCase()
  return key === "markdown" || key === "md"
}

const isPresentationFormat = (format?: string | null) =>
  (format || "").toLowerCase() === "presentation"

export const CoworkEditor = ({
  value,
  language,
  format,
  readOnly = false,
  className,
  onChange,
}: CoworkEditorProps) => {
  const theme = useAppTheme()
  const isDark = theme === "dark"

  if (isPresentationFormat(format)) {
    return (
      <Suspense
        fallback={
          <div
            className={cn(
              "flex min-h-0 flex-1 items-center justify-center text-sm text-muted-foreground",
              className
            )}
          >
            Loading slides…
          </div>
        }
      >
        <CoworkPresentationEditor
          value={value}
          readOnly={readOnly}
          className={className}
          onChange={onChange}
        />
      </Suspense>
    )
  }

  if (isMarkdownFormat(format, language)) {
    return (
      <CoworkMarkdownEditor
        value={value}
        readOnly={readOnly}
        className={className}
        onChange={onChange}
      />
    )
  }

  const extensions = [
    ...languageExtension(format, language),
    EditorView.lineWrapping,
    ...(isDark ? [oneDark] : []),
    EditorView.theme({
      "&": {
        height: "100%",
        fontSize: "13px",
        backgroundColor: "transparent",
      },
      ".cm-scroller": {
        overflow: "auto",
        fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
      },
      ".cm-content": { padding: "12px 0", caretColor: "var(--foreground)" },
      ".cm-gutters": {
        background: "transparent",
        border: "none",
        color: "var(--muted-foreground)",
      },
      ".cm-activeLineGutter": { backgroundColor: "transparent" },
    }),
  ]

  return (
    <div className={cn("min-h-0 flex-1 overflow-hidden bg-background", className)}>
      <CodeMirror
        value={value}
        height="100%"
        theme={isDark ? "dark" : "light"}
        editable={!readOnly}
        extensions={extensions}
        basicSetup={{
          lineNumbers: true,
          foldGutter: true,
          highlightActiveLine: true,
          autocompletion: false,
        }}
        onChange={onChange}
        className="h-full bg-background [&_.cm-editor]:h-full [&_.cm-editor]:bg-background [&_.cm-editor]:outline-none [&_.cm-gutters]:bg-background"
      />
    </div>
  )
}
