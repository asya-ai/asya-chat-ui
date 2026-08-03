import { memo, useEffect, useMemo, useRef, useState } from "react"
import type { CSSProperties } from "react"
import { useNavigate } from "react-router-dom"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import remarkBreaks from "remark-breaks"
import remarkMath from "remark-math"
import rehypeKatex from "rehype-katex"
import mermaid from "mermaid"
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter"
import { Copy, Pencil, RotateCcw, Trash2, Plus, X } from "lucide-react"

import type { I18nContextValue } from "@/lib/i18n-context"
import type { ActionInfoLevel } from "@/lib/storage"
import type { ChatMessage, ChatMessageAttachmentInput } from "@/lib/types"
import { shouldSubmitOnEnter } from "@/lib/chat-input"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"

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
  editingContent: string
  editingAttachments: ChatMessageAttachmentInput[]
  editAttachmentError?: string | null
  codeTheme: Record<string, CSSProperties>
  t: I18nContextValue["t"]
  getSourceLabel: (source: { url: string; title?: string | null; host?: string | null }) => string
  onStartEdit: (msg: ChatMessage) => void
  onDeleteFromMessage: (msg: ChatMessage) => void
  onRetryMessage: (msg: ChatMessage) => void
  onSaveEditedMessage: (msg: ChatMessage) => void
  onCancelEdit: () => void
  onEditContentChange: (value: string) => void
  onEditPasteAttachments: (event: React.ClipboardEvent<HTMLTextAreaElement>) => void
  onEditFilesSelected: (files: File[]) => void
  onEditDragEnter: (event: React.DragEvent<HTMLDivElement>) => void
  onEditDragOver: (event: React.DragEvent<HTMLDivElement>) => void
  onEditDragLeave: (event: React.DragEvent<HTMLDivElement>) => void
  onEditDrop: (event: React.DragEvent<HTMLDivElement>) => void
  onRemoveEditingAttachment: (index: number) => void
  onPreviewAttachment: (attachment: ChatMessageAttachmentInput) => void
}

let mermaidInitialized = false
let mermaidRenderId = 0

const nextMermaidRenderId = () => {
  mermaidRenderId += 1
  return `chatui-mermaid-${mermaidRenderId}`
}

const ensureMermaidInitialized = () => {
  if (mermaidInitialized) return
  mermaid.initialize({
    startOnLoad: false,
    securityLevel: "strict",
  })
  mermaidInitialized = true
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

const CopyTextButton = ({
  text,
  label,
  className = "",
  iconOnly = false,
}: {
  text: string
  label: string
  className?: string
  iconOnly?: boolean
}) => (
  <Button
    type="button"
    variant="ghost"
    size={iconOnly ? "icon" : "sm"}
    className={className}
    onClick={() => void copyToClipboard(text)}
    aria-label={label}
  >
    {iconOnly ? (
      <Copy aria-hidden="true" className="w-3.5 h-3.5" />
    ) : (
      label
    )}
  </Button>
)

const MermaidDiagram = ({
  chart,
  copyLabel,
  renderFailedLabel,
}: {
  chart: string
  copyLabel: string
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
        ensureMermaidInitialized()
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
        className="top-2 right-2 z-10 absolute bg-background/80 border border-muted-foreground/30 text-[10px] text-muted-foreground hover:text-foreground uppercase tracking-wide"
      />
      <div
        ref={containerRef}
        className="[&_svg]:w-full [&_svg]:min-w-max [&_svg]:h-auto overflow-x-auto"
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
  editingContent,
  editingAttachments,
  editAttachmentError,
  codeTheme,
  t,
  getSourceLabel,
  onStartEdit,
  onDeleteFromMessage,
  onRetryMessage,
  onSaveEditedMessage,
  onCancelEdit,
  onEditContentChange,
  onEditPasteAttachments,
  onEditFilesSelected,
  onEditDragEnter,
  onEditDragOver,
  onEditDragLeave,
  onEditDrop,
  onRemoveEditingAttachment,
  onPreviewAttachment,
}: MessageBubbleProps) => {
  const navigate = useNavigate()
  const editFileInputRef = useRef<HTMLInputElement | null>(null)
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
  const canCopyMessage = Boolean(msg.content.trim())
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
          <p className={isUser ? "m-0 leading-5" : "my-2.5 leading-6"} {...rest}>
            {children}
          </p>
        )
      },
      ul({ children, node, ...rest }: any) {
        void node
        return (
          <ul className="space-y-2 my-2.5 pl-6 list-disc" {...rest}>
            {children}
          </ul>
        )
      },
      ol({ children, node, ...rest }: any) {
        void node
        return (
          <ol className="space-y-2 my-2.5 pl-6 list-decimal" {...rest}>
            {children}
          </ol>
        )
      },
      li({ children, node, ...rest }: any) {
        void node
        return (
          <li className="leading-6" {...rest}>
            {children}
          </li>
        )
      },
      a({ children, node, ...rest }: any) {
        void node
        return (
          <a
            {...rest}
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
      h1({ children, node, ...rest }: any) {
        void node
        return (
          <h1 className="mt-4 mb-2 font-semibold text-xl" {...rest}>
            {children}
          </h1>
        )
      },
      h2({ children, node, ...rest }: any) {
        void node
        return (
          <h2 className="mt-3 mb-2 font-semibold text-lg" {...rest}>
            {children}
          </h2>
        )
      },
      h3({ children, node, ...rest }: any) {
        void node
        return (
          <h3 className="mt-3 mb-2 font-semibold text-base" {...rest}>
            {children}
          </h3>
        )
      },
      h4({ children, node, ...rest }: any) {
        void node
        return (
          <h4 className="mt-3 mb-2 font-semibold text-base" {...rest}>
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
            <table className="w-full border-collapse text-sm" {...rest}>
              {children}
            </table>
          </div>
        )
      },
      thead({ children, node, ...rest }: any) {
        void node
        return (
          <thead className="border-b border-border" {...rest}>
            {children}
          </thead>
        )
      },
      th({ children, node, ...rest }: any) {
        void node
        return (
          <th className="px-3 py-2 text-left font-medium" {...rest}>
            {children}
          </th>
        )
      },
      td({ children, node, ...rest }: any) {
        void node
        return (
          <td className="px-3 py-2 align-top" {...rest}>
            {children}
          </td>
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
              renderFailedLabel={t("chat_mermaid_render_failed")}
            />
          )
        }
        if (isBlockCode(codeContent, className)) {
          return (
            <div className={codeBlockClassName}>
              <CopyTextButton
                text={codeContent}
                label={t("chat_copy_code")}
                className="top-2 right-2 z-10 absolute bg-code/90 hover:bg-muted border border-border text-[10px] text-code-foreground/80 hover:text-code-foreground uppercase tracking-wide"
              />
              {match ? (
                <SyntaxHighlighter
                  {...rest}
                  style={codeTheme}
                  language={match[1]}
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
                  {codeContent}
                </SyntaxHighlighter>
              ) : (
                <pre className="m-0 p-4 pt-10 overflow-x-auto text-[13px] whitespace-pre-wrap">
                  <code className={className} {...rest}>
                    {codeContent}
                  </code>
                </pre>
              )}
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
  const renderMarkdown = (markdown: string, key?: string) => (
    <ReactMarkdown
      key={key}
      remarkPlugins={[remarkGfm, remarkBreaks, remarkMath]}
      rehypePlugins={[rehypeKatex]}
      components={markdownComponents}
    >
      {normalizeMathContent(markdown)}
    </ReactMarkdown>
  )
  const attachmentSrc = (attachment: {
    content_type: string
    data_base64?: string
    content_url?: string
  }) => {
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
  const canSaveEdit = Boolean(editingContent.trim() || editingAttachments.length > 0)

  return (
    <div
      className={`mx-auto flex w-full max-w-(--chat-content-width) ${
        isUser ? "justify-end" : "justify-start"
      }`}
    >
      <div className={`group ${isEditing || !isUser ? "w-full" : "max-w-full"}`}>
        <div
          className={`overflow-hidden rounded-lg wrap-break-word whitespace-normal ${
            isUser
              ? "bg-secondary p-2 text-base leading-5 font-semibold text-foreground"
              : hasEventHeader
                ? "bg-transparent p-2 text-sm leading-4.5 text-foreground"
                : "bg-transparent p-0 text-sm leading-4.5 text-foreground"
          }`}
        >
          {hasEventHeader ? (
            <div className="flex justify-between items-center gap-2">
              <p className="mb-1.5 text-xs font-medium opacity-70">
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
              <div>
                <p className="opacity-70 mb-1 text-xs uppercase">{t("chat_execution_code")}</p>
                <pre className="bg-background/40 p-2 rounded overflow-x-auto text-xs">
                  {codeEvent?.code ?? ""}
                </pre>
              </div>
              <div>
                <p className="opacity-70 mb-1 text-xs uppercase">{t("chat_execution_output")}</p>
                <pre className="bg-background/40 p-2 rounded overflow-x-auto text-xs">
                  {[
                    codeEvent?.output?.stdout,
                    codeEvent?.output?.stderr,
                    codeEvent?.output?.error
                      ? `${t("common_error")}: ${codeEvent.output.error}`
                      : null,
                    codeEvent?.output?.requires_approval
                      ? t("chat_execution_requires_approval")
                      : null,
                    codeEvent?.output?.timed_out
                      ? t("chat_execution_timed_out")
                      : null,
                    typeof codeEvent?.output?.exit_code === "number"
                      ? t("chat_execution_exit_code", {
                          code: codeEvent.output.exit_code,
                        })
                      : null,
                  ]
                    .filter(Boolean)
                    .join("\n") || t("chat_execution_no_output")}
                </pre>
              </div>
              {codeEvent?.output?.output_files &&
              codeEvent.output.output_files.length > 0 ? (
                <div className="space-y-2">
                  <p className="opacity-70 text-xs uppercase">
                    {t("chat_execution_outputs")}
                  </p>
                  <div className="flex flex-wrap gap-2">
                    {codeEvent.output.output_files
                      .filter((file) => (file.content_type ?? "").startsWith("image/"))
                      .map((file: { file_name: string; content_type: string; data_base64: string }, index: number) => (
                        <Button
                          key={`${file.file_name}-${index}`}
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="p-0 rounded-md w-auto h-auto overflow-hidden"
                          onClick={() =>
                            onPreviewAttachment({
                              file_name: file.file_name,
                              content_type: file.content_type,
                              data_base64: file.data_base64,
                            })
                          }
                        >
                          <img
                            src={`data:${file.content_type};base64,${file.data_base64}`}
                            alt={file.file_name}
                            className="bg-muted/50 rounded-md w-auto max-w-32 h-auto max-h-32 object-contain"
                          />
                        </Button>
                      ))}
                    {codeEvent.output.output_files
                      .filter((file) => !(file.content_type ?? "").startsWith("image/"))
                      .map((file: { file_name: string; content_type: string; data_base64: string }, index: number) => (
                        <a
                          key={`${file.file_name}-${index}`}
                          className="hover:bg-muted px-3 py-2 border rounded-md text-xs"
                          href={`data:${file.content_type};base64,${file.data_base64}`}
                          download={file.file_name}
                        >
                          {file.file_name}
                        </a>
                      ))}
                  </div>
                </div>
              ) : null}
            </details>
          ) : toolCallEvent ? (
            <div className="space-y-1 py-1 text-xs">
              <div className="opacity-80">
                {toolCallEvent.action_summary ||
                  toolCallEvent.input_preview ||
                  t("chat_running_tool_call")}
              </div>
              {toolCallEvent.output?.result_preview ? (
                <div className="text-muted-foreground">
                  {toolCallEvent.output.result_preview}
                </div>
              ) : null}
              {toolCallEvent.output?.error ? (
                <div className="text-destructive/90">
                  {t("common_error")}: {toolCallEvent.output.error}
                </div>
              ) : null}
              {toolCallEvent.output?.raw_output ? (
                <details className="pt-1">
                  <summary className="opacity-80 cursor-pointer">{t("chat_result")}</summary>
                  <pre className="bg-background/40 mt-1 p-2 rounded overflow-x-auto text-[11px] whitespace-pre-wrap">
                    {JSON.stringify(toolCallEvent.output.raw_output, null, 2)}
                  </pre>
                </details>
              ) : null}
            </div>
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
              <div>
                <p className="opacity-70 mb-1 text-xs uppercase">{t("chat_sources")}</p>
                <pre className="bg-background/40 p-2 rounded overflow-x-auto text-xs whitespace-pre-wrap">
                  {(urlAttachmentsEvent.urls ?? []).join("\n") || t("chat_no_urls_provided")}
                </pre>
              </div>
              <div>
                <p className="opacity-70 mb-1 text-xs uppercase">{t("chat_result")}</p>
                <pre className="bg-background/40 p-2 rounded overflow-x-auto text-xs whitespace-pre-wrap">
                  {urlAttachmentsEvent.output?.error
                    ? `${t("common_error")}: ${urlAttachmentsEvent.output.error}`
                    : (urlAttachmentsEvent.output?.results ?? [])
                        .map((row) =>
                          row.error
                            ? `- ${row.url ?? t("chat_unknown")}: ${t("common_error")} ${row.error}`
                            : `- ${row.file_name ?? t("chat_file")} (${row.content_type ?? t("chat_unknown")}, ${row.size_bytes ?? 0} ${t("chat_bytes_unit")})`
                        )
                        .join("\n") || t("chat_waiting_for_download")}
                </pre>
              </div>
            </details>
          ) : isContextSummaryEvent ? (
            <details className="space-y-3">
              <summary className="text-xs uppercase tracking-wide cursor-pointer">
                {t("chat_context_summarized")}
              </summary>
              <div>
                <p className="opacity-70 mb-1 text-xs uppercase">{t("chat_summary")}</p>
                <pre className="bg-background/40 p-2 rounded overflow-x-auto text-xs whitespace-pre-wrap">
                  {contextSummaryEvent?.summary ?? ""}
                </pre>
              </div>
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
                value={editingContent}
                onChange={(event) => onEditContentChange(event.target.value)}
                onPaste={onEditPasteAttachments}
                onKeyDown={(event) => {
                  if (!shouldSubmitOnEnter(event)) return
                  event.preventDefault()
                  if (canSaveEdit) {
                    onSaveEditedMessage(msg)
                  }
                }}
                rows={3}
                className="bg-muted min-h-32 max-h-[60vh] overflow-y-auto text-foreground"
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
                <Button size="sm" onClick={() => onSaveEditedMessage(msg)} disabled={!canSaveEdit}>
                  {t("chat_save")}
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
                    if (actionInfoLevel === "detailed") {
                      return (
                        <div key={`action-${index}`} className="space-y-2">
                          <span className="inline-flex px-2 py-0.5 border border-muted-foreground/30 rounded-full text-[10px] text-muted-foreground uppercase tracking-wide">
                            {part.label}
                          </span>
                          {actionAttachments.length > 0 ? (
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
                          ) : null}
                        </div>
                      )
                    }
                    return (
                      <div key={`action-${index}`} className="space-y-2">
                        <div className="flex items-start gap-1.5 text-xs text-muted-foreground leading-5">
                          <span aria-hidden="true" className="select-none opacity-50">
                            ›
                          </span>
                          <span className="min-w-0 wrap-break-word">{part.label}</span>
                        </div>
                        {actionAttachments.length > 0 ? (
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
                        ) : null}
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
                        className="inline-flex items-center justify-center gap-0.5 rounded-full bg-border px-1.5 py-0.5 text-xs leading-4 font-medium text-foreground"
                        role="status"
                        aria-live="polite"
                      >
                        <span
                          aria-hidden="true"
                          className="figma-icon size-3.5 animate-spin opacity-[0.79]"
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
                    ) : actionInfoLevel === "short" ? (
                      <div className="space-y-2 py-2" role="status" aria-live="polite">
                        {isThinking ? (
                          <div className="inline-flex items-center justify-center gap-0.5 rounded-full bg-border px-1.5 py-0.5 text-xs leading-4 font-medium text-foreground">
                            <span
                              aria-hidden="true"
                              className="figma-icon size-3.5 animate-spin opacity-[0.79]"
                              style={{ maskImage: "url('/icon-thinking.svg')" }}
                            />
                            <span>{t("chat_thinking")}</span>
                          </div>
                        ) : null}
                        {thinkingLabels.length > 0 ? (
                          <div className="space-y-1 text-xs text-muted-foreground">
                            {thinkingLabels.map((label, index) => (
                              <div
                                key={`${label}-${index}`}
                                className="flex items-start gap-1.5 leading-5"
                              >
                                <span aria-hidden="true" className="select-none opacity-50">
                                  ›
                                </span>
                                <span className="min-w-0 wrap-break-word">{label}</span>
                              </div>
                            ))}
                          </div>
                        ) : null}
                      </div>
                    ) : isThinking ? (
                      <div className="space-y-2 py-2" role="status" aria-live="polite">
                        <div className="inline-flex items-center justify-center gap-0.5 rounded-full bg-border px-1.5 py-0.5 text-xs leading-4 font-medium text-foreground">
                          <span
                            aria-hidden="true"
                            className="figma-icon size-3.5 animate-spin opacity-[0.79]"
                            style={{ maskImage: "url('/icon-thinking.svg')" }}
                          />
                          <span>{t("chat_thinking")}</span>
                        </div>
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
              {msg.sources && msg.sources.length > 0 ? (
                <div className="z-10 relative mt-3 overflow-hidden text-muted-foreground text-xs pointer-events-auto">
                  <span className="uppercase tracking-wide">{t("chat_sources")}</span>{" "}
                  <div className="flex flex-wrap gap-2 mt-2 max-w-full">
                    {msg.sources.map((source, index) => {
                      const sourceUrl = typeof source.url === "string" ? source.url : ""
                      const isInternal = sourceUrl.startsWith("/chat/")
                      return (
                        <a
                          key={`${sourceUrl || "source"}-${index}`}
                          href={sourceUrl || "#"}
                          {...(isInternal ? {} : { target: "_blank", rel: "noreferrer" })}
                          title={source.title ?? sourceUrl}
                          className="inline-flex px-2 py-0.5 border border-muted-foreground/30 rounded-full max-w-[240px] overflow-hidden text-[10px] text-muted-foreground hover:text-foreground text-ellipsis uppercase tracking-wide whitespace-nowrap cursor-pointer"
                          onClick={
                            isInternal
                              ? (e) => {
                                  e.preventDefault()
                                  navigate(sourceUrl)
                                }
                              : !sourceUrl
                                ? (e) => e.preventDefault()
                                : undefined
                          }
                        >
                          {getSourceLabel(source)}
                        </a>
                      )
                    })}
                  </div>
                </div>
              ) : null}
            </>
          )}
        </div>
        {!isEditing &&
        (canCopyMessage ||
          (actionsEnabled && (isUser || msg.generation_status === "failed"))) ? (
          <div
            className={`flex gap-2 opacity-100 md:opacity-0 md:group-hover:opacity-100 md:group-focus-within:opacity-100 mt-2 transition ${
              isUser ? "justify-end" : "justify-start"
            }`}
          >
            {canCopyMessage ? (
              <CopyTextButton
                text={msg.content}
                label={t("chat_copy_message")}
                iconOnly
                className="opacity-70 hover:opacity-100"
              />
            ) : null}
            {!isUser && actionsEnabled && msg.generation_status === "failed" ? (
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="px-2 h-7 text-xs"
                onClick={() => onRetryMessage(msg)}
              >
                <RotateCcw aria-hidden="true" className="mr-1 w-3.5 h-3.5" />
                {t("chat_retry")}
              </Button>
            ) : null}
            {isUser && actionsEnabled ? (
              <>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="opacity-70 hover:opacity-100"
                  onClick={() => onStartEdit(msg)}
                  aria-label={t("chat_edit_message")}
                >
                  <Pencil aria-hidden="true" className="w-3.5 h-3.5" />
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="opacity-70 hover:opacity-100"
                  onClick={() => onDeleteFromMessage(msg)}
                  aria-label={t("chat_delete")}
                >
                  <Trash2 aria-hidden="true" className="w-3.5 h-3.5" />
                </Button>
              </>
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
    if (prev.getSourceLabel !== next.getSourceLabel) return false
    if (prev.thinkingLabels.length !== next.thinkingLabels.length) return false
    for (let i = 0; i < prev.thinkingLabels.length; i += 1) {
      if (prev.thinkingLabels[i] !== next.thinkingLabels[i]) return false
    }
    // Editing-only props should trigger rerender only for edited message bubble.
    if (next.isEditing) {
      if (prev.isEditDragActive !== next.isEditDragActive) return false
      if (prev.editingContent !== next.editingContent) return false
      if (prev.editingAttachments !== next.editingAttachments) return false
      if (prev.editAttachmentError !== next.editAttachmentError) return false
    }
    return true
  }
)
