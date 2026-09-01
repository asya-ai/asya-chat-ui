// Repair Mermaid syntax that model output often gets wrong but GitHub/Mermaid >= 10.5 reject:
// - flowchart/graph node labels starting with '@' must be quoted — '[@' is lexed as edge-ID syntax.
// - rectangle labels containing nested '[' must be quoted.
// - sequenceDiagram message labels: ';' is a statement separator — replace with ',' in message text.
// Scope: chart body only (no ```mermaid fences).

const MSG =
  /^(\s*[A-Za-z_]\w*\s*(?:->>?[+-]?|-->>?|--?\)|--?x)\s*[A-Za-z_]\w*\s*:)(.*)$/
const LEADING_AT_SQUARE = /\[(@[^\]"]*)\]/g
const LEADING_AT_DIAMOND = /\{(@[^}"]*)\}/g
const LEADING_AT_ROUND = /\((@[^)"]*)\)/g
const SHAPE_SECOND_CHARS = new Set(["[", "(", "/", "\\"])

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
    const inner = line.slice(i + 1, j)
    if (depth === 0 && /[[\]]/.test(inner) && !inner.includes('"')) {
      out += `["${inner}"]`
      i = j + 1
    } else {
      out += ch
      i += 1
    }
  }
  return out
}

const quoteLeadingAtLabels = (line: string): string =>
  line
    .replace(LEADING_AT_SQUARE, '["$1"]')
    .replace(LEADING_AT_DIAMOND, '{"$1"}')
    .replace(LEADING_AT_ROUND, '("$1")')

const rewriteFlowLine = (line: string): string =>
  quoteLeadingAtLabels(quoteBracketedLabels(line))

const rewriteSequenceLine = (line: string): string => {
  const match = MSG.exec(line)
  if (!match) return line
  const head = match[1] ?? ""
  const rest = match[2] ?? ""
  return head + rest.replace(/;/g, ",")
}

/** Best-effort repairs for common invalid Mermaid emitted by models. Idempotent on valid charts. */
export const sanitizeMermaidChart = (body: string): string => {
  const lines = body.split(/\r?\n/)
  const first =
    lines.find((line) => line.trim() !== "" && !line.trim().startsWith("%%"))?.trim() ?? ""
  const isFlow = /^(flowchart|graph)\b/i.test(first)
  const isSequence = /^sequenceDiagram\b/i.test(first)
  const eol = /\r\n/.test(body) ? "\r\n" : "\n"

  return lines
    .map((line) => {
      if (isFlow) return rewriteFlowLine(line)
      if (isSequence) return rewriteSequenceLine(line)
      return line
    })
    .join(eol)
}
