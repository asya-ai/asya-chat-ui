/**
 * Client-side Marp → PDF/PPTX export was removed.
 * Presentation export now goes through chatApi.exportCoworkPresentation (backend/scraper).
 * This stub overwrites the old pptxgenjs-based module on syncs that do not delete files.
 */
export type PresentationExportFormat = "pdf" | "pptx"
