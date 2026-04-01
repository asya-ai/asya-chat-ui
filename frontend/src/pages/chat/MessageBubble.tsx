import { memo, useMemo, useRef } from "react"
import type { CSSProperties } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import remarkBreaks from "remark-breaks"
import remarkMath from "remark-math"
import rehypeKatex from "rehype-katex"
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter"
import { Pencil, RotateCcw, Trash2, Plus, X } from "lucide-react"

import type { I18nContextValue } from "@/lib/i18n-context"
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
  actionsEnabled: boolean
  isEditing: boolean
  editingContent: string
  editingAttachments: ChatMessageAttachmentInput[]
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
  onRemoveEditingAttachment: (index: number) => void
  onPreviewAttachment: (attachment: ChatMessageAttachmentInput) => void
}

const MessageBubbleComponent = ({
  msg,
  isUser,
  isCodeEvent,
  isThinking,
  thinkingLabels,
  currentStepLabel,
  currentToolLabel,
  actionsEnabled,
  isEditing,
  editingContent,
  editingAttachments,
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
  onRemoveEditingAttachment,
  onPreviewAttachment,
}: MessageBubbleProps) => {
  const editFileInputRef = useRef<HTMLInputElement | null>(null)
  const isContextSummaryEvent = msg.tool_event?.type === "context_summary"
  const codeEvent = msg.tool_event?.type === "code_execution" ? msg.tool_event : null
  const toolCallEvent = msg.tool_event?.type === "tool_call" ? msg.tool_event : null
  const chatViewEvent = msg.activity_event?.type === "chat_view" ? msg.activity_event : null
  const urlAttachmentsEvent =
    msg.tool_event?.type === "url_attachments" ? msg.tool_event : null
  const contextSummaryEvent =
    msg.tool_event?.type === "context_summary" ? msg.tool_event : null
  const content = useMemo(() => {
    // Normalize bracketed math blocks into KaTeX-friendly $$...$$
    return msg.content.replace(
      /\n\[\n([\s\S]*?)\n\]\n/g,
      (_, body) => `\n$$\n${body}\n$$\n`
    )
  }, [msg.content])
  const userLinkifiedContent = useMemo(() => {
    const urlRegex = /(https?:\/\/[^\s<>"')\]]+)/g
    const parts = msg.content.split(urlRegex)
    return parts.map((part, index) => {
      if (/^https?:\/\//.test(part)) {
        return (
          <a
            key={`user-link-${index}`}
            href={part}
            target="_blank"
            rel="noopener noreferrer"
            className="underline underline-offset-2 break-all"
          >
            {part}
          </a>
        )
      }
      return <span key={`user-text-${index}`}>{part}</span>
    })
  }, [msg.content])
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
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div className="group max-w-[85%]">
        <div
          className={`bg-muted px-4 py-2 rounded-lg overflow-hidden text-foreground text-sm break-words leading-relaxed ${
            isUser ? "whitespace-pre-wrap" : "whitespace-normal"
          }`}
        >
          <div className="flex justify-between items-center gap-2">
            <p
              className={`opacity-70 mb-1 text-xs uppercase ${
                isUser ? "ml-auto text-right" : ""
              }`}
            >
              {isCodeEvent
                ? t("chat_executing_code")
                : toolCallEvent
                  ? t("chat_tool_label", { name: toolCallEvent.tool_name })
                : chatViewEvent
                  ? t("chat_activity")
                : urlAttachmentsEvent
                  ? t("chat_downloading_attachments")
                : isContextSummaryEvent
                  ? t("chat_context_summarized")
                  : isUser
                    ? t("chat_you")
                    : msg.model_name || t("chat_assistant")}
            </p>
          </div>
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
                      .filter((file) => file.content_type.startsWith("image/"))
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
                            className="rounded-md max-w-32 max-h-32 w-auto h-auto object-contain bg-muted/50"
                          />
                        </Button>
                      ))}
                    {codeEvent.output.output_files
                      .filter((file) => !file.content_type.startsWith("image/"))
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
                {toolCallEvent.input_preview || t("chat_running_tool_call")}
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
            <div className="space-y-2">
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
                className="bg-muted text-foreground"
              />
              {editingAttachments.length > 0 ? (
                <div className="flex flex-wrap gap-2">
                  {editingAttachments.map((attachment, index) => {
                    const isImage = attachment.content_type.startsWith("image/")
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
              {isThinking ? (
                <div className="space-y-2 py-2" role="status" aria-live="polite">
                  {currentStepLabel || currentToolLabel ? (
                    <div className="text-[11px] text-muted-foreground uppercase tracking-wide">
                      {[currentStepLabel, currentToolLabel].filter(Boolean).join(" - ")}
                    </div>
                  ) : null}
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
                  <div className="flex flex-wrap gap-2">
                    {thinkingLabels.map((label) => (
                      <span
                        key={label}
                        className="px-2 py-0.5 border border-muted-foreground/30 rounded-full text-[10px] text-muted-foreground uppercase tracking-wide"
                      >
                        {label}
                      </span>
                    ))}
                  </div>
                </div>
              ) : (
                isUser ? (
                  <div className="leading-6 whitespace-pre-wrap break-words">
                    {userLinkifiedContent}
                  </div>
                ) : (
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm, remarkBreaks, remarkMath]}
                    rehypePlugins={[rehypeKatex]}
                    components={{
                      p({ children, node, ...rest }) {
                        void node
                        return (
                          <p className="my-2.5 leading-6" {...rest}>
                            {children}
                          </p>
                        )
                      },
                      ul({ children, node, ...rest }) {
                        void node
                        return (
                          <ul className="space-y-2 my-2.5 pl-6 list-disc" {...rest}>
                            {children}
                          </ul>
                        )
                      },
                      ol({ children, node, ...rest }) {
                        void node
                        return (
                          <ol className="space-y-2 my-2.5 pl-6 list-decimal" {...rest}>
                            {children}
                          </ol>
                        )
                      },
                      li({ children, node, ...rest }) {
                        void node
                        return (
                          <li className="leading-6" {...rest}>
                            {children}
                          </li>
                        )
                      },
                      a({ children, node, ...rest }) {
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
                      hr({ node, ...rest }) {
                        void node
                        return <hr className="my-3 border-muted-foreground/30" {...rest} />
                      },
                      h1({ children, node, ...rest }) {
                        void node
                        return (
                          <h1 className="mt-4 mb-2 font-semibold text-xl" {...rest}>
                            {children}
                          </h1>
                        )
                      },
                      h2({ children, node, ...rest }) {
                        void node
                        return (
                          <h2 className="mt-3 mb-2 font-semibold text-lg" {...rest}>
                            {children}
                          </h2>
                        )
                      },
                      h3({ children, node, ...rest }) {
                        void node
                        return (
                          <h3 className="mt-3 mb-2 font-semibold text-base" {...rest}>
                            {children}
                          </h3>
                        )
                      },
                      h4({ children, node, ...rest }) {
                        void node
                        return (
                          <h4 className="mt-3 mb-2 font-semibold text-base" {...rest}>
                            {children}
                          </h4>
                        )
                      },
                      h5({ children, node, ...rest }) {
                        void node
                        return (
                          <h5 className="mt-3 mb-2 font-semibold text-sm" {...rest}>
                            {children}
                          </h5>
                        )
                      },
                      h6({ children, node, ...rest }) {
                        void node
                        return (
                          <h6 className="mt-3 mb-2 font-semibold text-sm" {...rest}>
                            {children}
                          </h6>
                        )
                      },
                      table({ children, node, ...rest }) {
                        void node
                        return (
                          <div className="overflow-x-auto my-3">
                            <table className="w-full border border-muted-foreground/30 text-sm" {...rest}>
                              {children}
                            </table>
                          </div>
                        )
                      },
                      thead({ children, node, ...rest }) {
                        void node
                        return (
                          <thead className="bg-muted/40 text-foreground" {...rest}>
                            {children}
                          </thead>
                        )
                      },
                      tbody({ children, node, ...rest }) {
                        void node
                        return <tbody {...rest}>{children}</tbody>
                      },
                      tr({ children, node, ...rest }) {
                        void node
                        return (
                          <tr className="border-t border-muted-foreground/30" {...rest}>
                            {children}
                          </tr>
                        )
                      },
                      th({ children, node, ...rest }) {
                        void node
                        return (
                          <th className="px-3 py-2 text-left font-semibold" {...rest}>
                            {children}
                          </th>
                        )
                      },
                      td({ children, node, ...rest }) {
                        void node
                        return (
                          <td className="px-3 py-2 align-top" {...rest}>
                            {children}
                          </td>
                        )
                      },
                      code(props) {
                        const { className, children, ref: refProp, ...rest } = props
                        const inline = (props as { inline?: boolean }).inline
                        void refProp
                        const match = /language-(\w+)/.exec(className || "")
                        const content = String(children).replace(/\n$/, "")
                        if (!inline && match) {
                          return (
                            <div className="relative">
                              <Button
                                type="button"
                                variant="ghost"
                                size="sm"
                                className="top-2 right-2 absolute bg-background/80 border border-muted-foreground/30 text-[10px] text-muted-foreground hover:text-foreground uppercase tracking-wide"
                                onClick={() => navigator.clipboard.writeText(content)}
                              >
                                {t("common_copy")}
                              </Button>
                              <SyntaxHighlighter
                                {...rest}
                                style={codeTheme}
                                language={match[1]}
                                PreTag="div"
                              >
                                {content}
                              </SyntaxHighlighter>
                            </div>
                          )
                        }
                        return (
                          <code className={className} {...rest}>
                            {children}
                          </code>
                        )
                      },
                    }}
                  >
                    {content}
                  </ReactMarkdown>
                )
              )}
              {msg.attachments && msg.attachments.length > 0 ? (
                <div className="flex flex-wrap gap-2 mt-3">
                  {msg.attachments.map((attachment, index) => {
                    const isImage = attachment.content_type.startsWith("image/")
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
                            className="rounded-md max-w-32 max-h-32 w-auto h-auto object-contain bg-muted/50"
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
                    {msg.sources.map((source, index) => (
                      <a
                        key={`${source.url}-${index}`}
                        href={source.url}
                        target="_blank"
                        rel="noreferrer"
                        title={source.title ?? source.url}
                        className="inline-flex px-2 py-0.5 border border-muted-foreground/30 rounded-full max-w-[240px] overflow-hidden text-[10px] text-muted-foreground hover:text-foreground text-ellipsis uppercase tracking-wide whitespace-nowrap cursor-pointer"
                      >
                        {getSourceLabel(source)}
                      </a>
                    ))}
                  </div>
                </div>
              ) : null}
            </>
          )}
        </div>
        {!isUser && !isEditing && actionsEnabled && msg.generation_status === "failed" ? (
          <div className="flex justify-start gap-2 opacity-100 md:opacity-0 md:group-hover:opacity-100 md:group-focus-within:opacity-100 mt-2 transition">
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-7 px-2 text-xs"
              onClick={() => onRetryMessage(msg)}
            >
              <RotateCcw aria-hidden="true" className="w-3.5 h-3.5 mr-1" />
              {t("chat_retry")}
            </Button>
          </div>
        ) : null}
        {isUser && !isEditing && actionsEnabled ? (
          <div className="flex justify-end gap-2 opacity-100 md:opacity-0 md:group-hover:opacity-100 md:group-focus-within:opacity-100 mt-2 transition">
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
      if (prev.editingContent !== next.editingContent) return false
      if (prev.editingAttachments !== next.editingAttachments) return false
    }
    return true
  }
)
