// Repair Mermaid syntax that model output often gets wrong:
// - flowchart/graph labels starting with '@' must be quoted — '[@' is edge-ID syntax.
// - rectangle labels with nested '[' must be quoted.
// - bare '&' in labels → '#amp;' (Mermaid HTML-label entity form).
// - curly/smart quotes → ASCII " so quoted labels actually parse.
// - unquoted labels containing , & / ( ) # ; " get quoted.
// - sequenceDiagram message labels: ';' is a statement separator → ','.
// - models often emit multiple diagram roots in one fence — split before render.
// Scope: chart body only (no ```mermaid fences).

const MSG =
  /^(\s*[A-Za-z_]\w*\s*(?:->>?[+-]?|-->>?|--?\)|--?x)\s*[A-Za-z_]\w*\s*:)(.*)$/
const LEADING_AT_SQUARE = /\[(@[^\]"]*)\]/g
const LEADING_AT_DIAMOND = /\{(@[^}"]*)\}/g
const LEADING_AT_ROUND = /\((@[^)"]*)\)/g
const SHAPE_SECOND_CHARS = new Set(["[", "(", "/", "\\"])
const NEEDS_QUOTE = /[,&/#;()"']/

/** Diagram-type keywords Mermaid detectType understands (longest-first for prefix checks). */
const DIAGRAM_KEYWORDS = [
  "sequencediagram",
  "statediagram-v2",
  "statediagram",
  "classdiagram",
  "erdiagram",
  "gitgraph",
  "quadrantchart",
  "sankey-beta",
  "block-beta",
  "xychart-beta",
  "c4context",
  "flowchart",
  "mindmap",
  "timeline",
  "journey",
  "gantt",
  "graph",
  "pie",
] as const

export const MERMAID_DIAGRAM_START =
  /^(?:graph|flowchart|sequenceDiagram|classDiagram|stateDiagram(?:-v2)?|erDiagram|journey|gantt|pie|gitGraph|mindmap|timeline|quadrantChart|sankey-beta|block-beta|xychart-beta|C4Context)\b/i

const firstContentLine = (body: string): string => {
  for (const line of body.split(/\r?\n/)) {
    const trimmed = line.trim()
    if (trimmed !== "" && !trimmed.startsWith("%%")) return trimmed
  }
  return ""
}

const normalizeQuotes = (text: string): string =>
  text.replace(/[\u201C\u201D\u00AB\u00BB\u201E]/g, '"').replace(/[\u2018\u2019]/g, "'")

/** Replace bare & with Mermaid's #amp; entity; leave existing entities alone. */
const escapeAmps = (text: string): string =>
  text.replace(/&(?!(?:amp|lt|gt|quot|apos|#(?:\d+|x[\da-f]+)|#[a-z]+);)/gi, "#amp;")

const quoteBracketedLabels = (line: string): string => {
  let out = ""
  let i = 0
  let inQuote = false
  while (i < line.length) {
    const ch = line[i]
    if (ch === '"') inQuote = !inQuote
    const prev = line[i - 1]
    const next = line[i + 1]
    const isRectOpen =
      ch === "[" &&
      !inQuote &&
      prev !== undefined &&
      /\w/.test(prev) &&
      next !== undefined &&
      next !== '"' &&
      !SHAPE_SECOND_CHARS.has(next)
    if (!isRectOpen) {
      out += ch
      i += 1
      continue
    }
    let depth = 0
    let j = i
    for (; j < line.length; j += 1) {
      if (line[j] === "[") depth += 1
      else if (line[j] === "]") {
        depth -= 1
        if (depth === 0) break
      }
    }
    if (depth !== 0) {
      out += ch
      i += 1
      continue
    }
    const inner = line.slice(i + 1, j)
    if ((/[[\]]/.test(inner) || NEEDS_QUOTE.test(inner)) && !inner.includes('"')) {
      out += `["${escapeAmps(inner)}"]`
      i = j + 1
    } else {
      out += ch
      i += 1
    }
  }
  return out
}

/** Escape & inside already-quoted "..." label segments. */
const escapeAmpsInQuotes = (line: string): string => {
  let out = ""
  let i = 0
  while (i < line.length) {
    if (line[i] !== '"') {
      out += line[i]
      i += 1
      continue
    }
    let j = i + 1
    while (j < line.length && line[j] !== '"') j += 1
    const inner = line.slice(i + 1, j)
    out += `"${escapeAmps(inner)}"`
    i = j < line.length ? j + 1 : j
  }
  return out
}

const quoteLeadingAtLabels = (line: string): string =>
  line
    .replace(LEADING_AT_SQUARE, '["$1"]')
    .replace(LEADING_AT_DIAMOND, '{"$1"}')
    .replace(LEADING_AT_ROUND, '("$1")')

const rewriteFlowLine = (line: string): string =>
  escapeAmpsInQuotes(quoteLeadingAtLabels(quoteBracketedLabels(line)))

const rewriteSequenceLine = (line: string): string => {
  const match = MSG.exec(line)
  if (!match) return escapeAmps(line)
  const head = match[1] ?? ""
  const rest = match[2] ?? ""
  return head + escapeAmps(rest.replace(/;/g, ","))
}

/**
 * True while streamed fence body is still a proper prefix of a diagram keyword
 * (e.g. "flow" → "flowchart"). Mermaid errors these as "No diagram type detected".
 */
export const isIncompleteMermaidPrefix = (body: string): boolean => {
  const first = firstContentLine(body).toLowerCase()
  if (!first || MERMAID_DIAGRAM_START.test(first)) return false
  return DIAGRAM_KEYWORDS.some(
    (keyword) => keyword.startsWith(first) && first.length < keyword.length
  )
}

/** Split one fence that contains multiple diagram roots into separate chart bodies. */
export const splitMermaidCharts = (body: string): string[] => {
  const normalized = body.replace(/\r\n/g, "\n")
  const lines = normalized.split("\n")
  const chunks: string[][] = []
  let current: string[] = []

  const flush = () => {
    const text = current.join("\n").trim()
    if (text) chunks.push(current)
    current = []
  }

  for (const line of lines) {
    const trimmed = line.trim()
    const startsDiagram =
      trimmed !== "" &&
      !trimmed.startsWith("%%") &&
      MERMAID_DIAGRAM_START.test(trimmed)
    if (startsDiagram && current.some((entry) => entry.trim() !== "")) {
      flush()
    }
    current.push(line)
  }
  flush()

  return chunks.map((chunk) => chunk.join("\n").trim()).filter(Boolean)
}

/** Best-effort repairs for common invalid Mermaid emitted by models. Idempotent on valid charts. */
export const sanitizeMermaidChart = (body: string): string => {
  const normalized = normalizeQuotes(body)
  const lines = normalized.split(/\r?\n/)
  const first = firstContentLine(normalized)
  const isFlow = /^(flowchart|graph)\b/i.test(first)
  const isSequence = /^sequenceDiagram\b/i.test(first)
  const eol = /\r\n/.test(body) ? "\r\n" : "\n"

  return lines
    .map((line) => {
      if (isFlow) return rewriteFlowLine(line)
      if (isSequence) return rewriteSequenceLine(line)
      return escapeAmps(line)
    })
    .join(eol)
}
