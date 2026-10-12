import { describe, expect, it } from "vitest"
import {
  formatInlineMarkdown,
  inlineMarkdownToRuns,
  markdownToExportHtml,
  parseMarkdownBlocks,
} from "./exportCoworkMarkdown"

describe("parseMarkdownBlocks", () => {
  it("keeps fenced code blocks intact", () => {
    const md = [
      "Close these first:",
      "",
      "```bash",
      "kill 212398 216596",
      "```",
      "",
      "Then unmount.",
    ].join("\n")

    expect(parseMarkdownBlocks(md)).toEqual([
      { type: "paragraph", text: "Close these first:" },
      { type: "code", language: "bash", code: "kill 212398 216596" },
      { type: "paragraph", text: "Then unmount." },
    ])
  })

  it("preserves multi-line fence content and whitespace", () => {
    const md = "```\n(base) asya@host$\n  fuser -vm /mnt\n```"
    expect(parseMarkdownBlocks(md)).toEqual([
      {
        type: "code",
        language: "",
        code: "(base) asya@host$\n  fuser -vm /mnt",
      },
    ])
  })

  it("parses lists, headings, quotes, and rules", () => {
    const md = [
      "## Steps",
      "",
      "1. Stop apps",
      "2. Unmount",
      "",
      "- leftover",
      "",
      "> note",
      "",
      "---",
    ].join("\n")

    expect(parseMarkdownBlocks(md)).toEqual([
      { type: "heading", level: 2, text: "Steps" },
      { type: "list", ordered: true, items: ["Stop apps", "Unmount"] },
      { type: "list", ordered: false, items: ["leftover"] },
      { type: "blockquote", text: "note" },
      { type: "hr" },
    ])
  })

  it("parses standalone image lines", () => {
    const md = [
      "Intro",
      "",
      "![Weekly progress infographic](generated-1f182a3e6227.png)",
      "",
      "Outro",
    ].join("\n")
    expect(parseMarkdownBlocks(md)).toEqual([
      { type: "paragraph", text: "Intro" },
      {
        type: "image",
        alt: "Weekly progress infographic",
        src: "generated-1f182a3e6227.png",
      },
      { type: "paragraph", text: "Outro" },
    ])
  })
})

describe("markdownToExportHtml", () => {
  it("renders fences as pre/code without leaking backticks", () => {
    const html = markdownToExportHtml(
      "Use:\n\n```bash\nsudo umount /mnt\n```\n\nDone."
    )
    expect(html).toContain("<pre><code class=\"language-bash\">sudo umount /mnt</code></pre>")
    expect(html).not.toContain("```")
    expect(html).toContain("<p>Use:</p>")
    expect(html).toContain("<p>Done.</p>")
  })

  it("formats inline bold, italic, code, links, and citations", () => {
    const html = formatInlineMarkdown(
      "Close *eog* and **mousepad** with `kill` [1] see [docs](https://example.com)."
    )
    expect(html).toBe(
      'Close <em>eog</em> and <strong>mousepad</strong> with <code>kill</code> <sup>[1]</sup> see <a href="https://example.com">docs</a>.'
    )
  })

  it("escapes HTML inside code fences", () => {
    const html = markdownToExportHtml("```html\n<script>x</script>\n```")
    expect(html).toContain("&lt;script&gt;x&lt;/script&gt;")
    expect(html).not.toContain("<script>")
  })

  it("renders images as img tags instead of raw markdown", () => {
    const html = markdownToExportHtml(
      "![Weekly progress infographic](generated-1f182a3e6227.png)"
    )
    expect(html).toContain("<img src=")
    expect(html).toContain("generated-1f182a3e6227.png")
    expect(html).toContain('alt="Weekly progress infographic"')
    expect(html).not.toContain("![Weekly progress")
  })
})

describe("inlineMarkdownToRuns", () => {
  it("does not turn inline code into label (undefined)", () => {
    const serialized = JSON.stringify(
      inlineMarkdownToRuns("Close `eog` and *mousepad*.", "Calibri")
    )
    expect(serialized).toContain("eog")
    expect(serialized).toContain("mousepad")
    expect(serialized).not.toContain("undefined")
  })

  it("keeps link labels and urls", () => {
    const serialized = JSON.stringify(
      inlineMarkdownToRuns("See [docs](https://example.com).", "Calibri")
    )
    expect(serialized).toContain("docs (https://example.com)")
    expect(serialized).not.toContain("undefined")
  })
})
