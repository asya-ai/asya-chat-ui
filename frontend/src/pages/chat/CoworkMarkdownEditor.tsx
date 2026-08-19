import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  BlockTypeSelect,
  BoldItalicUnderlineToggles,
  CodeMirrorEditor,
  CreateLink,
  DiffSourceToggleWrapper,
  InsertTable,
  ListsToggle,
  MDXEditor,
  Separator,
  StrikeThroughSupSubToggles,
  UndoRedo,
  codeBlockPlugin,
  codeMirrorPlugin,
  diffSourcePlugin,
  headingsPlugin,
  linkDialogPlugin,
  linkPlugin,
  listsPlugin,
  markdownShortcutPlugin,
  quotePlugin,
  tablePlugin,
  thematicBreakPlugin,
  toolbarPlugin,
  type MDXEditorMethods,
} from "@mdxeditor/editor"
import "@mdxeditor/editor/style.css"
import "@/pages/chat/cowork-mdxeditor.css"

import { getTheme, type ThemeMode } from "@/lib/theme"
import { cn } from "@/lib/utils"

type CoworkMarkdownEditorProps = {
  value: string
  readOnly?: boolean
  className?: string
  /** Slide bodies shouldn't use thematic breaks — `---` is Marp's slide separator. */
  allowThematicBreak?: boolean
  onChange: (value: string) => void
}

type ViewMode = "rich-text" | "source"

type RecoveryState = {
  nonce: number
  viewMode: ViewMode
  suppressHtml: boolean
  markdownOverride: string | null
}

/** MDX requires JSX void tags (`<br />`); Marp/models often emit HTML (`<br>`). */
const MDX_VOID_TAGS = [
  "area",
  "base",
  "br",
  "col",
  "embed",
  "hr",
  "img",
  "input",
  "link",
  "meta",
  "param",
  "source",
  "track",
  "wbr",
] as const

const OPEN_FENCE = /^( {0,3})(`{3,}|~{3,})(.*)$/

const CODE_BLOCK_LANGUAGES: Record<string, string> = {
  "": "Plain text",
  text: "Plain text",
  txt: "Plain text",
  markdown: "Markdown",
  md: "Markdown",
  mermaid: "Mermaid",
  json: "JSON",
  jsonc: "JSON",
  yaml: "YAML",
  yml: "YAML",
  python: "Python",
  py: "Python",
  javascript: "JavaScript",
  js: "JavaScript",
  typescript: "TypeScript",
  ts: "TypeScript",
  tsx: "TSX",
  jsx: "JSX",
  html: "HTML",
  css: "CSS",
  sql: "SQL",
  bash: "Bash",
  sh: "Shell",
  shell: "Shell",
  csv: "CSV",
}

type MarkdownSegment = { kind: "body" | "fence"; text: string }

const splitMarkdownFences = (markdown: string): MarkdownSegment[] => {
  const lines = markdown.split("\n")
  const segments: MarkdownSegment[] = []
  let bodyLines: string[] = []
  let fenceLines: string[] | null = null
  let fenceChar = ""
  let fenceLen = 0

  const flushBody = () => {
    if (bodyLines.length === 0) return
    segments.push({ kind: "body", text: bodyLines.join("\n") })
    bodyLines = []
  }

  for (const line of lines) {
    const match = line.match(OPEN_FENCE)
    if (fenceLines === null) {
      if (match) {
        flushBody()
        fenceLines = [line]
        fenceChar = match[2][0] ?? "`"
        fenceLen = match[2].length
      } else {
        bodyLines.push(line)
      }
      continue
    }
    fenceLines.push(line)
    if (
      match &&
      (match[2][0] ?? "") === fenceChar &&
      match[2].length >= fenceLen &&
      match[3].trim() === ""
    ) {
      segments.push({ kind: "fence", text: fenceLines.join("\n") })
      fenceLines = null
    }
  }

  if (fenceLines) {
    fenceLines.push(fenceChar.repeat(fenceLen))
    segments.push({ kind: "fence", text: fenceLines.join("\n") })
  }
  flushBody()
  return segments
}

const applyVoidTags = (markdown: string): string => {
  let next = markdown
  for (const tag of MDX_VOID_TAGS) {
    next = next.replace(new RegExp(`<${tag}\\s*>`, "gi"), `<${tag} />`)
    next = next.replace(
      new RegExp(`<${tag}(\\s+[^>]*?)\\s*>`, "gi"),
      (match, attrs: string) => {
        if (attrs.trimEnd().endsWith("/")) return match
        return `<${tag}${attrs} />`
      }
    )
  }
  return next
}

const escapeMdxBody = (body: string): string => {
  const inlineCode = /(`+)([\s\S]*?)\1/g
  let last = 0
  let result = ""
  let match: RegExpExecArray | null
  while ((match = inlineCode.exec(body))) {
    result += escapeMdxText(body.slice(last, match.index))
    result += match[0]
    last = match.index + match[0].length
  }
  result += escapeMdxText(body.slice(last))
  return result
}

const escapeMdxText = (text: string): string => {
  const withAutolinks = text.replace(/<(https?:\/\/[^>\s]+)>/gi, "$1")
  return applyVoidTags(withAutolinks)
    .replace(/(?<!\\)\{/g, "\\{")
    .replace(/(?<!\\)\}/g, "\\}")
}

const normalizeMarkdownForMdx = (markdown: string): string =>
  splitMarkdownFences(markdown)
    .map((segment) =>
      segment.kind === "fence" ? segment.text : escapeMdxBody(segment.text)
    )
    .join("\n")

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

export const CoworkMarkdownEditor = ({
  value,
  readOnly = false,
  className,
  allowThematicBreak = true,
  onChange,
}: CoworkMarkdownEditorProps) => {
  const editorRef = useRef<MDXEditorMethods>(null)
  const lastEmittedRef = useRef(value)
  const recoveryRef = useRef({ triedFix: false })
  const [recovery, setRecovery] = useState<RecoveryState>({
    nonce: 0,
    viewMode: "rich-text",
    suppressHtml: false,
    markdownOverride: null,
  })
  const theme = useAppTheme()
  const isDark = theme === "dark"
  const normalizedValue = useMemo(() => normalizeMarkdownForMdx(value), [value])
  const markdownForEditor = recovery.markdownOverride ?? normalizedValue

  const plugins = useMemo(
    () => [
      headingsPlugin({ allowedHeadingLevels: [1, 2, 3, 4] }),
      listsPlugin(),
      quotePlugin(),
      ...(allowThematicBreak ? [thematicBreakPlugin()] : []),
      linkPlugin(),
      linkDialogPlugin(),
      tablePlugin(),
      codeBlockPlugin({
        defaultCodeBlockLanguage: "text",
        codeBlockEditorDescriptors: [
          { priority: -10, match: () => true, Editor: CodeMirrorEditor },
        ],
      }),
      codeMirrorPlugin({ codeBlockLanguages: CODE_BLOCK_LANGUAGES }),
      markdownShortcutPlugin(),
      diffSourcePlugin({ viewMode: recovery.viewMode, diffMarkdown: "" }),
      toolbarPlugin({
        toolbarClassName: "cowork-mdx-toolbar",
        toolbarContents: () =>
          readOnly ? null : (
            <DiffSourceToggleWrapper options={["rich-text", "source"]}>
              <UndoRedo />
              <Separator />
              <BlockTypeSelect />
              <Separator />
              <BoldItalicUnderlineToggles />
              <StrikeThroughSupSubToggles options={["Strikethrough"]} />
              <Separator />
              <ListsToggle />
              <Separator />
              <CreateLink />
              <InsertTable />
            </DiffSourceToggleWrapper>
          ),
      }),
    ],
    [allowThematicBreak, readOnly, recovery.viewMode]
  )

  useEffect(() => {
    if (normalizedValue === lastEmittedRef.current) return
    lastEmittedRef.current = normalizedValue
    recoveryRef.current.triedFix = false
    setRecovery((prev) => ({
      nonce:
        prev.viewMode === "rich-text" && !prev.suppressHtml && !prev.markdownOverride
          ? prev.nonce
          : prev.nonce + 1,
      viewMode: "rich-text",
      suppressHtml: false,
      markdownOverride: null,
    }))
    editorRef.current?.setMarkdown(normalizedValue)
  }, [normalizedValue])

  // Popup portal lives outside the panel; keep its theme class in sync.
  useEffect(() => {
    const syncPopupTheme = () => {
      document.querySelectorAll(".mdxeditor-popup-container").forEach((node) => {
        node.classList.add("cowork-mdxeditor")
        node.classList.toggle("dark-theme", isDark)
        node.classList.toggle("dark-editor", isDark)
      })
    }
    syncPopupTheme()
    const observer = new MutationObserver(syncPopupTheme)
    observer.observe(document.body, { childList: true, subtree: true })
    return () => observer.disconnect()
  }, [isDark])

  const handleParseError = useCallback(
    ({ source }: { error: string; source: string }) => {
      const fixed = normalizeMarkdownForMdx(source)
      if (!recoveryRef.current.triedFix && fixed !== source) {
        recoveryRef.current.triedFix = true
        lastEmittedRef.current = fixed
        if (!readOnly) onChange(fixed)
        setRecovery((prev) => ({
          nonce: prev.nonce + 1,
          viewMode: "rich-text",
          suppressHtml: false,
          markdownOverride: fixed,
        }))
        return
      }
      recoveryRef.current.triedFix = true
      setRecovery((prev) => {
        const markdownOverride = prev.markdownOverride ?? fixed
        if (!prev.suppressHtml) {
          return {
            nonce: prev.nonce + 1,
            viewMode: "rich-text",
            suppressHtml: true,
            markdownOverride,
          }
        }
        if (prev.viewMode !== "source") {
          return {
            nonce: prev.nonce + 1,
            viewMode: "source",
            suppressHtml: true,
            markdownOverride,
          }
        }
        return prev
      })
    },
    [onChange, readOnly]
  )

  return (
    <div className={cn("flex min-h-0 flex-1 flex-col overflow-hidden bg-background", className)}>
      <MDXEditor
        key={recovery.nonce}
        ref={editorRef}
        markdown={markdownForEditor}
        readOnly={readOnly}
        suppressHtmlProcessing={recovery.suppressHtml}
        onError={handleParseError}
        onChange={(markdown, initialNormalize) => {
          if (initialNormalize) {
            lastEmittedRef.current = markdown
            return
          }
          lastEmittedRef.current = markdown
          onChange(markdown)
        }}
        plugins={plugins}
        className={cn(
          "cowork-mdxeditor mdxeditor-full-height flex min-h-0 flex-1 flex-col overflow-hidden !border-0 !shadow-none",
          isDark ? "dark-theme dark-editor" : "light-theme"
        )}
        contentEditableClassName={cn(
          "cowork-md-editor px-4 py-3 text-sm leading-6 text-foreground outline-none",
          "[&_h1]:mt-4 [&_h1]:mb-2 [&_h1]:text-xl [&_h1]:font-semibold",
          "[&_h2]:mt-3 [&_h2]:mb-2 [&_h2]:text-lg [&_h2]:font-semibold",
          "[&_h3]:mt-3 [&_h3]:mb-1.5 [&_h3]:text-base [&_h3]:font-semibold",
          "[&_p]:my-2",
          "[&_ul]:my-2 [&_ul]:list-disc [&_ul]:space-y-1 [&_ul]:pl-6",
          "[&_ol]:my-2 [&_ol]:list-decimal [&_ol]:space-y-1 [&_ol]:pl-6",
          "[&_li]:leading-6",
          "[&_blockquote]:my-3 [&_blockquote]:border-l-2 [&_blockquote]:border-border [&_blockquote]:pl-3 [&_blockquote]:text-muted-foreground",
          "[&_hr]:my-4 [&_hr]:border-border",
          "[&_a]:underline [&_a]:underline-offset-2",
          "[&_strong]:font-semibold",
          "[&_code]:rounded [&_code]:bg-muted [&_code]:px-1 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-[0.85em]",
          "[&_pre]:my-3 [&_pre]:overflow-x-auto [&_pre]:rounded-lg [&_pre]:bg-muted [&_pre]:p-3 [&_pre]:font-mono [&_pre]:text-xs"
          // Intentionally no generic [&_th]/[&_td] rules — they break MDXEditor table tool cells.
        )}
      />
    </div>
  )
}
