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

  it("converts \\( \\) inline math to $", () => {
    expect(normalizeMathContent("delta is \\(\\Delta t\\) ps")).toBe("delta is $\\Delta t$ ps")
  })

  it("converts bare [ ] block math (backslash already dropped)", () => {
    const input =
      "For direct time-of-flight LiDAR:\n\n[\nR = \\frac{ct}{2}\n]\n\nA **5 cm** error:\n\n[\n\\Delta t \\approx 333\\text{ ps}\n]\n"
    expect(normalizeMathContent(input)).toBe(
      "For direct time-of-flight LiDAR:\n\n$$\nR = \\frac{ct}{2}\n$$\n\nA **5 cm** error:\n\n$$\n\\Delta t \\approx 333\\text{ ps}\n$$\n"
    )
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
})
