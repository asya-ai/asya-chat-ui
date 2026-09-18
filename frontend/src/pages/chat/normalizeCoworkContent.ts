/**
 * Normalize cowork panel content so office-like payloads (RTF / binary DOCX)
 * render as readable text instead of raw markup or mojibake.
 */

const RTF_HEAD = /^\s*\{\\rtf\d/i

/** True when content is Rich Text Format source. */
export const looksLikeRtf = (value: string): boolean => RTF_HEAD.test(value)

/**
 * Heuristic for ZIP-based Office binaries (DOCX/XLSX/PPTX) that leaked into a
 * text field — usually starts with the PK zip signature.
 */
export const looksLikeBinaryOffice = (value: string): boolean => {
  if (!value) return false
  if (value.startsWith("PK\u0003\u0004") || value.startsWith("PK\x03\x04")) return true
  // Mojibake / latin1 decode of a zip often still begins with "PK".
  if (value.startsWith("PK") && value.includes("word/") && value.includes("[Content_Types].xml")) {
    return true
  }
  let control = 0
  const sample = value.slice(0, 512)
  for (let i = 0; i < sample.length; i += 1) {
    const code = sample.charCodeAt(i)
    if (code === 0 || (code < 9 && code !== 9 && code !== 10 && code !== 13)) control += 1
  }
  return sample.length > 32 && control / sample.length > 0.08
}

const SPECIAL_CHARS: Record<string, string> = {
  lquote: "\u2018",
  rquote: "\u2019",
  ldblquote: "\u201C",
  rdblquote: "\u201D",
  bullet: "\u2022",
  endash: "\u2013",
  emdash: "\u2014",
  tab: "\t",
  line: "\n",
  softline: "\n",
  par: "\n",
  cell: "\t",
  row: "\n",
  nestcell: "\t",
  nestrow: "\n",
}

/**
 * Strip RTF control words down to the human-readable plaintext.
 * Not a full RTF renderer — enough for cowork side-panel preview.
 */
export const rtfToPlainText = (input: string): string => {
  if (!looksLikeRtf(input)) return input

  let i = 0
  let depth = 0
  let skipDepth = -1
  const out: string[] = []

  const isAlpha = (ch: string) => (ch >= "a" && ch <= "z") || (ch >= "A" && ch <= "Z")

  while (i < input.length) {
    const ch = input[i]

    if (ch === "{") {
      depth += 1
      i += 1
      // Skip destination groups like {\fonttbl...}, {\*\...}
      if (skipDepth < 0 && i < input.length) {
        if (input[i] === "\\") {
          if (
            input.startsWith("\\*", i) ||
            input.startsWith("\\fonttbl", i) ||
            input.startsWith("\\colortbl", i) ||
            input.startsWith("\\stylesheet", i) ||
            input.startsWith("\\info", i) ||
            input.startsWith("\\header", i) ||
            input.startsWith("\\footer", i) ||
            input.startsWith("\\pict", i) ||
            input.startsWith("\\object", i) ||
            input.startsWith("\\datastore", i)
          ) {
            skipDepth = depth
          }
        }
      }
      continue
    }

    if (ch === "}") {
      if (skipDepth === depth) skipDepth = -1
      depth = Math.max(0, depth - 1)
      i += 1
      continue
    }

    if (skipDepth >= 0 && depth >= skipDepth) {
      i += 1
      continue
    }

    if (ch === "\\") {
      i += 1
      if (i >= input.length) break
      const esc = input[i]

      // Escaped literal
      if (esc === "\\" || esc === "{" || esc === "}") {
        out.push(esc)
        i += 1
        continue
      }

      // Hex byte \'hh
      if (esc === "'") {
        const hex = input.slice(i + 1, i + 3)
        if (/^[0-9a-fA-F]{2}$/.test(hex)) {
          out.push(String.fromCharCode(parseInt(hex, 16)))
          i += 3
          continue
        }
      }

      // Control word: \word[-]?[0-9]*[ ]?
      if (isAlpha(esc)) {
        let j = i
        while (j < input.length && isAlpha(input[j])) j += 1
        const word = input.slice(i, j).toLowerCase()
        let k = j
        if (k < input.length && (input[k] === "-" || (input[k] >= "0" && input[k] <= "9"))) {
          if (input[k] === "-") k += 1
          while (k < input.length && input[k] >= "0" && input[k] <= "9") k += 1
        }
        const numStr = input.slice(j, k)
        // Optional space delimiter after control word
        if (k < input.length && input[k] === " ") k += 1
        i = k

        if (word === "u") {
          let code = Number.parseInt(numStr || "0", 10)
          if (Number.isFinite(code)) {
            if (code < 0) code += 65536
            out.push(String.fromCharCode(code))
          }
          // Consume the ANSI fallback char if present
          if (i < input.length && input[i] !== "\\" && input[i] !== "{" && input[i] !== "}") {
            i += 1
          }
          continue
        }

        if (word in SPECIAL_CHARS) {
          out.push(SPECIAL_CHARS[word])
          continue
        }

        // Ignored presentational / destination words
        continue
      }

      // Unknown single-char control — skip
      i += 1
      continue
    }

    if (ch === "\r") {
      i += 1
      continue
    }
    if (ch === "\n") {
      // RTF newlines are insignificant outside \par
      i += 1
      continue
    }

    out.push(ch)
    i += 1
  }

  return out
    .join("")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim()
}

export type CoworkContentKind = "text" | "rtf" | "binary-office"

export const classifyCoworkContent = (value: string): CoworkContentKind => {
  if (looksLikeRtf(value)) return "rtf"
  if (looksLikeBinaryOffice(value)) return "binary-office"
  return "text"
}

/** Content safe to show in the cowork document preview. */
export const readableCoworkContent = (value: string): string => {
  const kind = classifyCoworkContent(value)
  if (kind === "rtf") return rtfToPlainText(value)
  if (kind === "binary-office") return ""
  return value
}
