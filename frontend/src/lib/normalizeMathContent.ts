/**
 * remark-math understands `$` / `$$`. Chat renders with
 * `singleDollarTextMath: false` so currency like `$20` stays prose; we normalize
 * model delimiters (`\[…\]`, `\(...\)`, math-looking `$…$`) to `$$…$$`.
 * CommonMark treats `\[` as an escaped `[`, so without this those blocks
 * render as literal "[ ... ]" with raw TeX.
 */

const INLINE_CODE_RE = /(?<!\\)(`+)([\s\S]*?)\1/g

/** Interior of `$…$` that is clearly LaTeX (not currency). */
const LOOKS_LIKE_MATH_RE = /\\[A-Za-z]|[=^_]|[<>≤≥≠∈∉⊂⊃∪∩±∞∑∫√]/

/** Shell / prose between dollars — never promote (quotes, pipes, paths). */
const SHELL_OR_PROSE_RE = /["'`|;]|\/[A-Za-z]/

const mapOutsideFences = (content: string, map: (segment: string) => string): string => {
  const lines = content.split(/\r?\n/)
  const parts: string[] = []
  let buffer: string[] = []
  let fence: string[] | null = null

  const flushBuffer = () => {
    if (buffer.length === 0) return
    parts.push(map(buffer.join("\n")))
    buffer = []
  }

  for (const line of lines) {
    if (line.trim().startsWith("```")) {
      if (fence) {
        parts.push([...fence, line].join("\n"))
        fence = null
      } else {
        flushBuffer()
        fence = [line]
      }
      continue
    }
    if (fence) {
      fence.push(line)
    } else {
      buffer.push(line)
    }
  }

  if (fence) {
    // Unclosed fence while streaming — leave untouched.
    parts.push(fence.join("\n"))
  } else {
    flushBuffer()
  }

  return parts.join("\n")
}

const mapOutsideInlineCode = (text: string, map: (segment: string) => string): string => {
  const stubs: string[] = []
  const withStubs = text.replace(INLINE_CODE_RE, (match) => {
    const index = stubs.length
    stubs.push(match)
    return `\u0000CODE${index}\u0000`
  })
  return map(withStubs).replace(/\u0000CODE(\d+)\u0000/g, (_, index: string) => stubs[Number(index)] ?? "")
}

const convertLatexDelimiters = (text: string): string => {
  // Display math: \[ ... \] → $$ ... $$
  let next = text.replace(/\\\[([\s\S]*?)\\\]/g, (_, body: string) => {
    const trimmed = body.trim()
    // Keep display math on its own lines when the model used a multiline block.
    return body.includes("\n") ? `$$\n${trimmed}\n$$` : `$$${trimmed}$$`
  })
  // Inline math: \( ... \) → $$ ... $$ (works with singleDollarTextMath: false)
  next = next.replace(/\\\(([\s\S]*?)\\\)/g, (_, body: string) => `$$${body.trim()}$$`)
  return next
}

const looksLikeLatex = (body: string): boolean => /\\[A-Za-z]/.test(body)

/** Models sometimes drop the backslash and emit bare [ ... ] block math. */
const convertBareBracketMathBlocks = (text: string): string => {
  const lines = text.split(/\r?\n/)
  const output: string[] = []
  let mathLines: string[] | null = null

  for (const line of lines) {
    const trimmed = line.trim()
    if (trimmed === "[" && !mathLines) {
      mathLines = []
      continue
    }
    if (trimmed === "]" && mathLines) {
      const body = mathLines.join("\n").trim()
      if (looksLikeLatex(body)) {
        output.push("$$", body, "$$")
      } else {
        output.push("[", ...mathLines, "]")
      }
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

  return output.join("\n")
}

const isMathLookingSingleDollar = (body: string): boolean => {
  const trimmed = body.trim()
  if (!trimmed || trimmed.includes("\n")) return false
  // Currency / plain amounts: $20, $8/month, $1,000.50
  if (/^\d[\d,]*(?:\.\d+)?(?:\/[A-Za-z]+)?$/.test(trimmed)) return false
  // Shell / paths between paired dollars (e.g. "$IMAGE" … "$DISK")
  if (SHELL_OR_PROSE_RE.test(trimmed)) return false
  // Env-style identifiers: $IMAGE$, $DISK$, $HOME$ — keep as prose
  if (/^[A-Z][A-Z0-9_]*$/.test(trimmed) && trimmed.length >= 2) return false
  if (LOOKS_LIKE_MATH_RE.test(trimmed)) return true
  // Short alphabetic token: $x$, $alpha$, $E$ (not SCREAMING_SNAKE)
  return /^[A-Za-z][A-Za-z0-9]{0,11}$/.test(trimmed)
}

/**
 * With singleDollarTextMath off, promote clearly-math `$…$` to `$$…$$`.
 * Leave currency and unpaired `$` alone. Skip existing `$$` spans.
 */
const promoteSingleDollarMath = (text: string): string => {
  let result = ""
  let i = 0
  while (i < text.length) {
    const ch = text[i]
    if (ch === "\\" && text[i + 1] === "$") {
      result += "\\$"
      i += 2
      continue
    }
    if (ch === "$" && text[i + 1] === "$") {
      const close = text.indexOf("$$", i + 2)
      if (close === -1) {
        result += text.slice(i)
        break
      }
      result += text.slice(i, close + 2)
      i = close + 2
      continue
    }
    if (ch === "$") {
      const close = text.indexOf("$", i + 1)
      if (close !== -1 && text[close + 1] !== "$") {
        const body = text.slice(i + 1, close)
        if (isMathLookingSingleDollar(body)) {
          result += `$$${body.trim()}$$`
          i = close + 1
          continue
        }
      }
    }
    result += ch
    i += 1
  }
  return result
}

export const normalizeMathContent = (content: string): string =>
  mapOutsideFences(content, (segment) =>
    mapOutsideInlineCode(segment, (body) => {
      const withDelimiters = convertLatexDelimiters(body)
      const withBrackets = convertBareBracketMathBlocks(withDelimiters)
      // KaTeX rejects some arrow glyphs inside \text{...}; unwrap those.
      const cleaned = withBrackets.replace(/\\text\{([→\-–—]+)\}/g, (_match, value: string) => value)
      return promoteSingleDollarMath(cleaned)
    })
  )
