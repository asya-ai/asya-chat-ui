import * as XLSX from "@e965/xlsx"

export type CsvExportFormat = "csv" | "xlsx"

const fileStemFromName = (fileName: string) => {
  const base = fileName.trim() || "data"
  return base.replace(/\.(csv|xlsx|xls)$/i, "") || "data"
}

/** Minimal CSV parser: supports commas, quotes, and newlines inside quotes. */
const parseCsv = (raw: string): string[][] => {
  const text = raw.replace(/^\uFEFF/, "")
  const rows: string[][] = []
  let row: string[] = []
  let cell = ""
  let inQuotes = false

  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i]
    const next = text[i + 1]
    if (inQuotes) {
      if (ch === '"' && next === '"') {
        cell += '"'
        i += 1
      } else if (ch === '"') {
        inQuotes = false
      } else {
        cell += ch
      }
      continue
    }
    if (ch === '"') {
      inQuotes = true
      continue
    }
    if (ch === ",") {
      row.push(cell)
      cell = ""
      continue
    }
    if (ch === "\n") {
      row.push(cell)
      rows.push(row)
      row = []
      cell = ""
      continue
    }
    if (ch === "\r") continue
    cell += ch
  }
  if (cell.length > 0 || row.length > 0) {
    row.push(cell)
    rows.push(row)
  }
  return rows.length > 0 ? rows : [[""]]
}

export const exportCoworkCsv = async (
  content: string,
  options: { format: CsvExportFormat; fileName?: string }
): Promise<{ blob: Blob; fileName: string }> => {
  const stem = fileStemFromName(options.fileName || "data")
  if (options.format === "csv") {
    return {
      blob: new Blob([content], { type: "text/csv;charset=utf-8" }),
      fileName: `${stem}.csv`,
    }
  }

  const rows = parseCsv(content)
  const workbook = XLSX.utils.book_new()
  const sheet = XLSX.utils.aoa_to_sheet(rows)
  XLSX.utils.book_append_sheet(workbook, sheet, "Sheet1")
  const buffer = XLSX.write(workbook, { bookType: "xlsx", type: "array" }) as ArrayBuffer
  return {
    blob: new Blob([buffer], {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }),
    fileName: `${stem}.xlsx`,
  }
}
