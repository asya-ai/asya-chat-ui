import { useCallback, useEffect, useRef, useState } from "react"
import { toast } from "sonner"

import { ApiError, chatApi } from "@/lib/api"
import type { CoworkDocument, CoworkingToolEvent } from "@/lib/types"

const SAVE_DEBOUNCE_MS = 450

type PendingSave = {
  documentId: string
  content: string
  baseVersion: number
}

function sortCoworkDocuments(docs: CoworkDocument[]): CoworkDocument[] {
  return [...docs].sort((a, b) => {
    const aCreated = a.created_at || ""
    const bCreated = b.created_at || ""
    if (aCreated !== bCreated) return aCreated.localeCompare(bCreated)
    return a.document_id.localeCompare(b.document_id)
  })
}

type UseCoworkDocumentResult = {
  open: boolean
  setOpen: (open: boolean) => void
  document: CoworkDocument | null
  documents: CoworkDocument[]
  content: string
  saving: boolean
  writing: boolean
  conflict: boolean
  mobileTab: "chat" | "document"
  setMobileTab: (tab: "chat" | "document") => void
  handleContentChange: (value: string) => void
  handleCoworkingEvent: (event: CoworkingToolEvent) => void
  activateDocument: (documentId: string) => Promise<void>
  deleteDocument: (documentId: string) => Promise<void>
  downloadDocument: (options?: {
    presentationFormat?: "pdf" | "pptx"
    documentFormat?: "md" | "txt" | "pdf" | "docx"
    csvFormat?: "csv" | "xlsx"
  }) => Promise<void>
  reloadLatest: () => Promise<void>
  closePanel: () => void
}

export function useCoworkDocument(chatId: string | undefined): UseCoworkDocumentResult {
  const [open, setOpen] = useState(false)
  const [document, setDocument] = useState<CoworkDocument | null>(null)
  const [documents, setDocuments] = useState<CoworkDocument[]>([])
  const [content, setContent] = useState("")
  const [saving, setSaving] = useState(false)
  const [writing, setWriting] = useState(false)
  const [conflict, setConflict] = useState(false)
  const [mobileTab, setMobileTab] = useState<"chat" | "document">("chat")
  const versionRef = useRef(0)
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pendingSaveRef = useRef<PendingSave | null>(null)
  const documentIdRef = useRef<string | null>(null)
  const contentRef = useRef("")
  const chatIdRef = useRef(chatId)
  chatIdRef.current = chatId

  const clearPendingSave = useCallback(() => {
    if (saveTimerRef.current) {
      clearTimeout(saveTimerRef.current)
      saveTimerRef.current = null
    }
    pendingSaveRef.current = null
  }, [])

  const applyDocument = useCallback(
    (doc: CoworkDocument, options?: { openPanel?: boolean; clearPending?: boolean }) => {
      const openPanel = options?.openPanel ?? true
      if (options?.clearPending !== false) {
        clearPendingSave()
      }
      setDocument(doc)
      documentIdRef.current = doc.document_id
      versionRef.current = doc.version
      if (typeof doc.content === "string") {
        setContent(doc.content)
        contentRef.current = doc.content
      } else {
        setContent("")
        contentRef.current = ""
      }
      setConflict(false)
      if (openPanel) {
        setOpen(true)
        setMobileTab("document")
      }
    },
    [clearPendingSave]
  )

  const openPlaceholder = useCallback(
    (event: CoworkingToolEvent) => {
      // Starting a brand-new document — drop any pending edits for the previous one.
      clearPendingSave()
      const initialContent = typeof event.content === "string" ? event.content : ""
      const placeholder: CoworkDocument = {
        document_id: event.document_id || `pending-${event.id || "doc"}`,
        chat_id: chatId || "",
        title: event.title || "Untitled",
        file_name: event.file_name || "document.txt",
        format: (event.format as CoworkDocument["format"]) || "markdown",
        language: event.language ?? null,
        content: initialContent,
        version: event.version ?? 1,
        is_active: true,
        last_assistant_version: event.last_assistant_version ?? event.version ?? 1,
        user_edited: false,
      }
      setDocument(placeholder)
      documentIdRef.current = placeholder.document_id
      versionRef.current = placeholder.version
      setContent(initialContent)
      contentRef.current = initialContent
      setOpen(true)
      setMobileTab("document")
      setWriting(true)
    },
    [chatId, clearPendingSave]
  )

  const refreshList = useCallback(async (id: string) => {
    try {
      const list = await chatApi.listCoworkDocuments(id)
      setDocuments(sortCoworkDocuments(list))
    } catch {
      // ignore list failures
    }
  }, [])

  useEffect(() => {
    clearPendingSave()
    setDocument(null)
    setDocuments([])
    setContent("")
    contentRef.current = ""
    setOpen(false)
    setWriting(false)
    setConflict(false)
    setMobileTab("chat")
    documentIdRef.current = null
    versionRef.current = 0
    if (!chatId) return
    let cancelled = false
    void (async () => {
      try {
        const [active, list] = await Promise.all([
          chatApi.getActiveCoworkDocument(chatId),
          chatApi.listCoworkDocuments(chatId),
        ])
        if (cancelled || chatIdRef.current !== chatId) return
        setDocuments(sortCoworkDocuments(list))
        if (active) {
          const isDesktop =
            typeof window !== "undefined" && window.innerWidth >= 768
          applyDocument(active, { openPanel: isDesktop })
          if (!isDesktop) {
            setOpen(false)
            setMobileTab("chat")
          }
        }
      } catch {
        // no active doc is fine
      }
    })()
    return () => {
      cancelled = true
      clearPendingSave()
    }
  }, [chatId, applyDocument, clearPendingSave])

  const flushSave = useCallback(async () => {
    const pending = pendingSaveRef.current
    const activeChatId = chatIdRef.current
    if (!activeChatId || !pending) return
    if (pending.documentId.startsWith("pending-")) return

    pendingSaveRef.current = null
    if (saveTimerRef.current) {
      clearTimeout(saveTimerRef.current)
      saveTimerRef.current = null
    }

    const savingDocId = pending.documentId
    const savingContent = pending.content
    const savingVersion = pending.baseVersion
    setSaving(true)
    try {
      const updated = await chatApi.patchCoworkDocument(activeChatId, savingDocId, {
        content: savingContent,
        base_version: savingVersion,
      })
      // Only merge into UI if we are still viewing the same document/chat.
      if (
        chatIdRef.current === activeChatId &&
        documentIdRef.current === savingDocId
      ) {
        versionRef.current = updated.version
        setDocument((prev) =>
          prev && prev.document_id === savingDocId
            ? {
                ...prev,
                ...updated,
                content: savingContent,
              }
            : prev
        )
        setConflict(false)
      }
      if (chatIdRef.current === activeChatId) {
        void refreshList(activeChatId)
      }
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        if (documentIdRef.current === savingDocId) {
          setConflict(true)
          const detail = error.detail as
            | { document?: CoworkDocument; detail?: string }
            | undefined
          const latest = detail?.document
          if (latest && latest.document_id === savingDocId) {
            applyDocument(
              { ...latest, content: latest.content ?? savingContent },
              { openPanel: true }
            )
          }
          toast.error("Document was updated elsewhere. Reloaded latest version.")
        }
      } else if (documentIdRef.current === savingDocId) {
        toast.error(error instanceof Error ? error.message : "Failed to save document")
      }
    } finally {
      if (documentIdRef.current === savingDocId) {
        setSaving(false)
      }
    }
  }, [applyDocument, refreshList])

  const handleContentChange = useCallback(
    (value: string) => {
      const docId = documentIdRef.current
      if (!docId) return
      setContent(value)
      contentRef.current = value
      pendingSaveRef.current = {
        documentId: docId,
        content: value,
        baseVersion: versionRef.current,
      }
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
      saveTimerRef.current = setTimeout(() => {
        void flushSave()
      }, SAVE_DEBOUNCE_MS)
    },
    [flushSave]
  )

  const handleCoworkingEvent = useCallback(
    (event: CoworkingToolEvent) => {
      if (event.action === "close") {
        setWriting(false)
        setOpen(false)
        setMobileTab("chat")
        return
      }

      const eventDocId = event.document_id || null
      const currentDocId = documentIdRef.current
      const isNewOpen =
        event.action === "open" ||
        (event.output?.status === "writing" &&
          event.output?.tool_name === "start_coworking")

      // Never apply another document's content into the currently open editor.
      if (
        eventDocId &&
        currentDocId &&
        eventDocId !== currentDocId &&
        !isNewOpen &&
        !eventDocId.startsWith("pending-") &&
        !currentDocId.startsWith("pending-")
      ) {
        if (chatIdRef.current) void refreshList(chatIdRef.current)
        return
      }

      const isWriting =
        event.action === "writing" ||
        event.output?.status === "writing" ||
        (event.action === "open" && event.output?.status === "writing")

      if (isWriting && event.output?.status !== "ok" && event.output?.status !== "error") {
        if (event.action === "writing" && eventDocId && currentDocId === eventDocId) {
          setWriting(true)
          if (typeof event.content === "string") {
            clearPendingSave()
            setContent(event.content)
            contentRef.current = event.content
          } else if (typeof event.append_text === "string" && event.append_text.length > 0) {
            const next = `${contentRef.current}${event.append_text}`
            setContent(next)
            contentRef.current = next
          }
          setOpen(true)
          return
        }
        openPlaceholder(event)
        return
      }

      if (event.output?.status === "error") {
        setWriting(false)
        return
      }

      if (!eventDocId && event.action === "open") {
        openPlaceholder(event)
        return
      }

      const next: CoworkDocument = {
        document_id: eventDocId || currentDocId || "",
        chat_id: chatId || "",
        title: event.title || document?.title || "Untitled",
        file_name: event.file_name || document?.file_name || "document.txt",
        format: (event.format as CoworkDocument["format"]) || document?.format || "text",
        language: event.language ?? document?.language ?? null,
        content: typeof event.content === "string" ? event.content : contentRef.current,
        version: event.version ?? versionRef.current,
        is_active: true,
        last_assistant_version:
          event.last_assistant_version ?? document?.last_assistant_version ?? event.version ?? 1,
        user_edited: Boolean(event.user_edited),
      }
      if (!next.document_id || next.document_id.startsWith("pending-")) {
        if (typeof event.content === "string") {
          setContent(event.content)
          contentRef.current = event.content
        }
        setOpen(true)
        setMobileTab("document")
        return
      }

      // Local unsaved edits for THIS document win over stale assistant echoes.
      if (
        pendingSaveRef.current?.documentId === next.document_id &&
        event.version != null &&
        event.version <= versionRef.current
      ) {
        setDocument((prev) =>
          prev && prev.document_id === next.document_id
            ? {
                ...prev,
                last_assistant_version:
                  event.last_assistant_version ?? prev.last_assistant_version,
                user_edited: Boolean(event.user_edited),
              }
            : prev
        )
        setWriting(false)
        return
      }

      applyDocument(next, { openPanel: true })
      setWriting(false)
      if (chatId) void refreshList(chatId)
    },
    [applyDocument, chatId, document, openPlaceholder, refreshList, clearPendingSave]
  )

  const activateDocument = useCallback(
    async (documentId: string) => {
      if (!chatId) return
      if (documentId === documentIdRef.current) return

      // Flush edits for the document we are leaving, then hard-switch.
      if (saveTimerRef.current) {
        clearTimeout(saveTimerRef.current)
        saveTimerRef.current = null
      }
      if (pendingSaveRef.current) {
        await flushSave()
      }

      clearPendingSave()
      setWriting(false)
      const doc = await chatApi.activateCoworkDocument(chatId, documentId)
      if (chatIdRef.current !== chatId) return
      applyDocument(doc, { openPanel: true })
      void refreshList(chatId)
    },
    [applyDocument, chatId, clearPendingSave, flushSave, refreshList]
  )

  const deleteDocument = useCallback(
    async (documentId: string) => {
      if (!chatId || documentId.startsWith("pending-")) return

      const deletingCurrent = documentIdRef.current === documentId
      if (deletingCurrent) {
        if (saveTimerRef.current) {
          clearTimeout(saveTimerRef.current)
          saveTimerRef.current = null
        }
        // Drop pending edits for the document we are deleting.
        pendingSaveRef.current = null
      } else if (pendingSaveRef.current) {
        await flushSave()
      }

      try {
        const nextActive = await chatApi.deleteCoworkDocument(chatId, documentId)
        if (chatIdRef.current !== chatId) return
        await refreshList(chatId)
        if (!deletingCurrent) return

        clearPendingSave()
        setWriting(false)
        if (nextActive) {
          applyDocument(nextActive, { openPanel: true })
        } else {
          setDocument(null)
          documentIdRef.current = null
          versionRef.current = 0
          setContent("")
          contentRef.current = ""
          setOpen(false)
          setMobileTab("chat")
          setConflict(false)
        }
      } catch (error) {
        toast.error(error instanceof Error ? error.message : "Failed to delete document")
      }
    },
    [applyDocument, chatId, clearPendingSave, flushSave, refreshList]
  )

  const downloadDocument = useCallback(
    async (options?: {
      presentationFormat?: "pdf" | "pptx"
      documentFormat?: "md" | "txt" | "pdf" | "docx"
      csvFormat?: "csv" | "xlsx"
    }) => {
      if (!chatId || !documentIdRef.current || documentIdRef.current.startsWith("pending-")) return
      if (saveTimerRef.current) {
        clearTimeout(saveTimerRef.current)
        saveTimerRef.current = null
      }
      if (pendingSaveRef.current) {
        await flushSave()
      }

      const triggerDownload = (blob: Blob, fileName: string) => {
        const url = URL.createObjectURL(blob)
        const anchor = window.document.createElement("a")
        anchor.href = url
        anchor.download = fileName
        anchor.click()
        URL.revokeObjectURL(url)
      }

      try {
        const formatKey = (document?.format || "").toLowerCase()
        const isPresentation =
          formatKey === "presentation" || Boolean(options?.presentationFormat)
        const isDocument =
          formatKey === "markdown" ||
          formatKey === "text" ||
          Boolean(options?.documentFormat)
        const isCsv = formatKey === "csv" || Boolean(options?.csvFormat)

        if (isPresentation && !options?.documentFormat && !options?.csvFormat) {
          const format = options?.presentationFormat || "pdf"
          const toastId = toast.loading(
            format === "pptx" ? "Building PowerPoint…" : "Building PDF…"
          )
          try {
            const { exportCoworkPresentation } = await import(
              "@/pages/chat/exportCoworkPresentation"
            )
            const { blob, fileName } = await exportCoworkPresentation(contentRef.current, {
              format,
              fileName: document?.file_name || document?.title || "presentation",
            })
            triggerDownload(blob, fileName)
            toast.success("Download ready", { id: toastId })
          } catch (error) {
            toast.error(error instanceof Error ? error.message : "Download failed", {
              id: toastId,
            })
          }
          return
        }

        if (isCsv && !options?.documentFormat) {
          const format = options?.csvFormat || "csv"
          const toastId =
            format === "csv" ? null : toast.loading("Building Excel workbook…")
          try {
            const { exportCoworkCsv } = await import("@/pages/chat/exportCoworkCsv")
            const { blob, fileName } = await exportCoworkCsv(contentRef.current, {
              format,
              fileName: document?.file_name || document?.title || "data",
            })
            triggerDownload(blob, fileName)
            if (toastId) toast.success("Download ready", { id: toastId })
          } catch (error) {
            if (toastId) {
              toast.error(error instanceof Error ? error.message : "Download failed", {
                id: toastId,
              })
            } else {
              toast.error(error instanceof Error ? error.message : "Download failed")
            }
          }
          return
        }

        if (isDocument) {
          const defaultSource = formatKey === "text" ? "txt" : "md"
          const format = options?.documentFormat || defaultSource
          const toastId =
            format === "md" || format === "txt"
              ? null
              : toast.loading(format === "docx" ? "Building Word doc…" : "Building PDF…")
          try {
            const { exportCoworkMarkdown } = await import("@/pages/chat/exportCoworkMarkdown")
            const { blob, fileName } = await exportCoworkMarkdown(contentRef.current, {
              format,
              fileName: document?.file_name || document?.title || "document",
              title: document?.title || undefined,
            })
            triggerDownload(blob, fileName)
            if (toastId) toast.success("Download ready", { id: toastId })
          } catch (error) {
            if (toastId) {
              toast.error(error instanceof Error ? error.message : "Download failed", {
                id: toastId,
              })
            } else {
              toast.error(error instanceof Error ? error.message : "Download failed")
            }
          }
          return
        }

        const { blob, fileName } = await chatApi.downloadCoworkDocument(
          chatId,
          documentIdRef.current
        )
        triggerDownload(blob, fileName || document?.file_name || "document.txt")
      } catch (error) {
        toast.error(error instanceof Error ? error.message : "Download failed")
      }
    },
    [chatId, document?.file_name, document?.format, document?.title, flushSave]
  )

  const reloadLatest = useCallback(async () => {
    if (!chatId || !documentIdRef.current || documentIdRef.current.startsWith("pending-")) return
    const docId = documentIdRef.current
    const doc = await chatApi.getCoworkDocument(chatId, docId)
    if (documentIdRef.current !== docId) return
    applyDocument(doc, { openPanel: true })
  }, [applyDocument, chatId])

  const closePanel = useCallback(() => {
    setWriting(false)
    setOpen(false)
    setMobileTab("chat")
  }, [])

  return {
    open,
    setOpen,
    document,
    documents,
    content,
    saving,
    writing,
    conflict,
    mobileTab,
    setMobileTab,
    handleContentChange,
    handleCoworkingEvent,
    activateDocument,
    deleteDocument,
    downloadDocument,
    reloadLatest,
    closePanel,
  }
}
