import { useRef } from "react"
import type { CSSProperties } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import remarkBreaks from "remark-breaks"
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter"
import { Pencil, Trash2, Plus, X } from "lucide-react"

import type { I18nContextValue } from "@/lib/i18n-context"
import type { ChatMessage, ChatMessageAttachmentInput } from "@/lib/types"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"

type MessageBubbleProps = {
  msg: ChatMessage
  isUser: boolean
  isCodeEvent: boolean
  isThinking: boolean
  thinkingLabels: string[]
  editingMessageId: string | null
  editingContent: string
  editingAttachments: ChatMessageAttachmentInput[]
  codeTheme: Record<string, CSSProperties>
  t: I18nContextValue["t"]
  getSourceLabel: (source: { url: string; title?: string | null; host?: string | null }) => string
  onStartEdit: (msg: ChatMessage) => void
  onDeleteFromMessage: (msg: ChatMessage) => void
  onSaveEditedMessage: (msg: ChatMessage) => void
  onCancelEdit: () => void
  onEditContentChange: (value: string) => void
  onEditPasteAttachments: (event: React.ClipboardEvent<HTMLTextAreaElement>) => void
  onEditFilesSelected: (files: File[]) => void
  onRemoveEditingAttachment: (index: number) => void
  onPreviewAttachment: (attachment: ChatMessageAttachmentInput) => void
}

export const MessageBubble = ({
  msg,
  isUser,
  isCodeEvent,
  isThinking,
  thinkingLabels,
  editingMessageId,
  editingContent,
  editingAttachments,
  codeTheme,
  t,
  getSourceLabel,
  onStartEdit,
  onDeleteFromMessage,
  onSaveEditedMessage,
  onCancelEdit,
  onEditContentChange,
  onEditPasteAttachments,
  onEditFilesSelected,
  onRemoveEditingAttachment,
  onPreviewAttachment,
}: MessageBubbleProps) => {
  const editFileInputRef = useRef<HTMLInputElement | null>(null)
  const isEditing = editingMessageId === msg.id

  const handleEditPickFiles = () => {
    editFileInputRef.current?.click()
  }

  const handleEditFilesSelected = (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? [])
    if (files.length === 0) return
    onEditFilesSelected(files)
    event.target.value = ""
  }

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div className="group max-w-[85%]">
        <div
          className={`bg-muted px-4 py-2 rounded-lg overflow-hidden text-foreground text-sm wrap-break-word leading-relaxed ${
            isUser ? "whitespace-pre-wrap" : "whitespace-normal"
          }`}
        >
          <div className="flex justify-between items-center gap-2">
            <p
              className={`opacity-70 mb-1 text-xs uppercase ${
                isUser ? "ml-auto text-right" : ""
              }`}
            >
              {isCodeEvent ? t("chat_executing_code") : isUser ? t("chat_you") : msg.model_name || t("chat_assistant")}
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
                  {msg.tool_event?.code ?? ""}
                </pre>
              </div>
              <div>
                <p className="opacity-70 mb-1 text-xs uppercase">{t("chat_execution_output")}</p>
                <pre className="bg-background/40 p-2 rounded overflow-x-auto text-xs">
                  {[
                    msg.tool_event?.output?.stdout,
                    msg.tool_event?.output?.stderr,
                    msg.tool_event?.output?.error
                      ? `Error: ${msg.tool_event.output.error}`
                      : null,
                    msg.tool_event?.output?.requires_approval
                      ? t("chat_execution_requires_approval")
                      : null,
                    msg.tool_event?.output?.timed_out
                      ? t("chat_execution_timed_out")
                      : null,
                    typeof msg.tool_event?.output?.exit_code === "number"
                      ? t("chat_execution_exit_code", {
                          code: msg.tool_event.output.exit_code,
                        })
                      : null,
                  ]
                    .filter(Boolean)
                    .join("\n") || t("chat_execution_no_output")}
                </pre>
              </div>
              {msg.tool_event?.output?.output_files &&
              msg.tool_event.output.output_files.length > 0 ? (
                <div className="space-y-2">
                  <p className="opacity-70 text-xs uppercase">
                    {t("chat_execution_outputs")}
                  </p>
                  <div className="flex flex-wrap gap-2">
                    {msg.tool_event.output.output_files
                      .filter((file) => file.content_type.startsWith("image/"))
                      .map((file, index) => (
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
                            className="rounded-md w-20 h-20 object-cover"
                          />
                        </Button>
                      ))}
                    {msg.tool_event.output.output_files
                      .filter((file) => !file.content_type.startsWith("image/"))
                      .map((file, index) => (
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
          ) : isEditing ? (
            <div className="space-y-2">
              <Textarea
                value={editingContent}
                onChange={(event) => onEditContentChange(event.target.value)}
                onPaste={onEditPasteAttachments}
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
                              src={`data:${attachment.content_type};base64,${attachment.data_base64}`}
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
                          className="-top-2 -right-2 absolute bg-background shadow rounded-full w-6 h-6"
                          onClick={() => onRemoveEditingAttachment(index)}
                        >
                          <X className="w-3 h-3" />
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
                  <Button variant="ghost" size="icon" onClick={handleEditPickFiles}>
                    <Plus className="w-5 h-5" />
                  </Button>
                </div>
              </div>
              <div className="flex gap-2">
                <Button size="sm" onClick={() => onSaveEditedMessage(msg)}>
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
                <div className="space-y-2 py-2">
                  <div className="flex items-center gap-1">
                    <span
                      className="bg-muted-foreground/60 rounded-full w-2 h-2 animate-bounce"
                      style={{ animationDelay: "0ms" }}
                    />
                    <span
                      className="bg-muted-foreground/60 rounded-full w-2 h-2 animate-bounce"
                      style={{ animationDelay: "150ms" }}
                    />
                    <span
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
                <ReactMarkdown
                  remarkPlugins={[remarkGfm, remarkBreaks]}
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
                  {msg.content}
                </ReactMarkdown>
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
                            })
                          }
                        >
                          <img
                            src={`data:${attachment.content_type};base64,${attachment.data_base64}`}
                            alt={attachment.file_name}
                            className="rounded-md w-20 h-20 object-cover"
                          />
                        </Button>
                      )
                    }
                    return (
                      <a
                        key={`${attachment.file_name}-${index}`}
                        className="hover:bg-muted px-3 py-2 border rounded-md text-xs"
                        href={`data:${attachment.content_type};base64,${attachment.data_base64}`}
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
        {isUser && !isEditing ? (
          <div className="flex justify-end gap-2 opacity-0 group-hover:opacity-100 mt-2 transition">
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="opacity-70 hover:opacity-100"
              onClick={() => onStartEdit(msg)}
            >
              <Pencil className="w-3.5 h-3.5" />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="opacity-70 hover:opacity-100"
              onClick={() => onDeleteFromMessage(msg)}
            >
              <Trash2 className="w-3.5 h-3.5" />
            </Button>
          </div>
        ) : null}
      </div>
    </div>
  )
}
