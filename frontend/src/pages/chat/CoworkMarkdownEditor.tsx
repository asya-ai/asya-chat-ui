import { useEffect, useMemo, useRef, useState } from "react"
import {
  BlockTypeSelect,
  BoldItalicUnderlineToggles,
  CreateLink,
  DiffSourceToggleWrapper,
  InsertTable,
  ListsToggle,
  MDXEditor,
  Separator,
  StrikeThroughSupSubToggles,
  UndoRedo,
  codeBlockPlugin,
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

const normalizeMarkdownForMdx = (markdown: string): string => {
  const fences: string[] = []
  let next = markdown.replace(/```[\s\S]*?```/g, (block) => {
    fences.push(block)
    return `\0FENCE${fences.length - 1}\0`
  })

  for (const tag of MDX_VOID_TAGS) {
    // <br> / <br >
    next = next.replace(new RegExp(`<${tag}\\s*>`, "gi"), `<${tag} />`)
    // <img src="..."> (not already self-closing)
    next = next.replace(
      new RegExp(`<${tag}(\\s+[^>]*?)\\s*>`, "gi"),
      (match, attrs: string) => {
        if (attrs.trimEnd().endsWith("/")) return match
        return `<${tag}${attrs} />`
      }
    )
  }

  return next.replace(/\0FENCE(\d+)\0/g, (_, index) => fences[Number(index)] ?? "")
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

export const CoworkMarkdownEditor = ({
  value,
  readOnly = false,
  className,
  allowThematicBreak = true,
  onChange,
}: CoworkMarkdownEditorProps) => {
  const editorRef = useRef<MDXEditorMethods>(null)
  const lastEmittedRef = useRef(value)
  const theme = useAppTheme()
  const isDark = theme === "dark"
  const markdownForEditor = useMemo(() => normalizeMarkdownForMdx(value), [value])

  const plugins = useMemo(
    () => [
      headingsPlugin({ allowedHeadingLevels: [1, 2, 3, 4] }),
      listsPlugin(),
      quotePlugin(),
      ...(allowThematicBreak ? [thematicBreakPlugin()] : []),
      linkPlugin(),
      linkDialogPlugin(),
      tablePlugin(),
      codeBlockPlugin({ defaultCodeBlockLanguage: "text" }),
      markdownShortcutPlugin(),
      diffSourcePlugin({ viewMode: "rich-text", diffMarkdown: "" }),
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
    [allowThematicBreak, readOnly]
  )

  useEffect(() => {
    const editor = editorRef.current
    if (!editor) return
    if (markdownForEditor === lastEmittedRef.current) return
    editor.setMarkdown(markdownForEditor)
    lastEmittedRef.current = markdownForEditor
  }, [markdownForEditor])

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

  return (
    <div className={cn("flex min-h-0 flex-1 flex-col overflow-hidden bg-background", className)}>
      <MDXEditor
        ref={editorRef}
        markdown={markdownForEditor}
        readOnly={readOnly}
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
