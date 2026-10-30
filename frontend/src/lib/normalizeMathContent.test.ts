import { describe, expect, it } from "vitest"
import { normalizeMathContent } from "./normalizeMathContent"

describe("normalizeMathContent", () => {
  it("converts \\[ \\] display math to $$", () => {
    const input = "For LiDAR:\n\n\\[\nR = \\frac{ct}{2}\n\\]\n"
    expect(normalizeMathContent(input)).toBe("For LiDAR:\n\n$$\nR = \\frac{ct}{2}\n$$\n")
  })

  it("converts single-line \\[ \\] display math", () => {
    expect(normalizeMathContent("eq \\[R=1\\] done")).toBe("eq $$R=1$$ done")
  })

  it("converts \\( \\) inline math to $$", () => {
    expect(normalizeMathContent("delta is \\(\\Delta t\\) ps")).toBe("delta is $$\\Delta t$$ ps")
  })

  it("converts bare [ ] block math when body looks like LaTeX", () => {
    const input =
      "For direct time-of-flight LiDAR:\n\n[\nR = \\frac{ct}{2}\n]\n\nA **5 cm** error:\n\n[\n\\Delta t \\approx 333\\text{ ps}\n]\n"
    expect(normalizeMathContent(input)).toBe(
      "For direct time-of-flight LiDAR:\n\n$$\nR = \\frac{ct}{2}\n$$\n\nA **5 cm** error:\n\n$$\n\\Delta t \\approx 333\\text{ ps}\n$$\n"
    )
  })

  it("leaves bare [ ] blocks alone when body is not LaTeX", () => {
    const input = "Note:\n\n[\nsome aside without tex\n]\n"
    expect(normalizeMathContent(input)).toBe(input)
  })

  it("leaves code fences untouched", () => {
    const input = "before\n\n```js\nconst x = '\\[not math\\]'\n```\n\nafter \\[a=1\\]"
    expect(normalizeMathContent(input)).toBe(
      "before\n\n```js\nconst x = '\\[not math\\]'\n```\n\nafter $$a=1$$"
    )
  })

  it("leaves inline code untouched", () => {
    expect(normalizeMathContent("use `\\[x\\]` for display")).toBe("use `\\[x\\]` for display")
  })

  it("unwraps arrow glyphs inside \\text{}", () => {
    expect(normalizeMathContent("x \\text{→} y")).toBe("x → y")
  })

  it("leaves already-dollar math alone", () => {
    const input = "$$\nR = 1\n$$"
    expect(normalizeMathContent(input)).toBe(input)
  })

  it("promotes math-looking single-dollar spans to $$", () => {
    expect(normalizeMathContent("area $x^2$ and $\\frac{1}{2}$")).toBe(
      "area $$x^2$$ and $$\\frac{1}{2}$$"
    )
  })

  it("leaves currency dollar amounts as literal text", () => {
    const input =
      "**Plus $8/month** or **Premium $30/month**, before tax. Keep legacy $20 Pro; its new $30 Premium costs more."
    expect(normalizeMathContent(input)).toBe(input)
  })

  it("does not let a leading currency amount swallow a later formula", () => {
    expect(normalizeMathContent("The $50 fee scales as $\\pi r^2$.")).toBe(
      "The $50 fee scales as $$\\pi r^2$$."
    )
  })

  it("leaves shell variable dollars as literal text", () => {
    const input = 'bzip2 -dc "$IMAGE" | sudo dd of="/dev/r$DISK" bs=4m'
    expect(normalizeMathContent(input)).toBe(input)
  })

  it("promotes simple equality math", () => {
    expect(normalizeMathContent("solve $a = b$")).toBe("solve $$a = b$$")
  })
})
