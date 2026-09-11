/**
 * remark-math only understands `$` / `$$`. Models often emit LaTeX with
 * `\[...\]` / `\(...\)`. CommonMark treats `\[` as an escaped `[`, so those
 * blocks render as literal "[ ... ]" with raw TeX — exactly the broken chat UI.
 */

const INLINE_CODE_RE = /(?<!\\)(`+)([\s\S]*?)\1/g

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
  // Inline math: \( ... \) → $ ... $
  next = next.replace(/\\\(([\s\S]*?)\\\)/g, (_, body: string) => `$${body.trim()}$`)
  return next
}

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

  return output.join("\n")
}

export const normalizeMathContent = (content: string): string =>
  mapOutsideFences(content, (segment) =>
    mapOutsideInlineCode(segment, (body) => {
      const withDelimiters = convertLatexDelimiters(body)
      const withBrackets = convertBareBracketMathBlocks(withDelimiters)
      // KaTeX rejects some arrow glyphs inside \text{...}; unwrap those.
      return withBrackets.replace(/\\text\{([→\-–—]+)\}/g, (_match, value: string) => value)
    })
  )

