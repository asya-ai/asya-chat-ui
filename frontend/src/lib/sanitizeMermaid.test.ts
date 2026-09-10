import { describe, expect, it } from "vitest"
import {
  isIncompleteMermaidPrefix,
  sanitizeMermaidChart,
  splitMermaidCharts,
} from "./sanitizeMermaid"

describe("sanitizeMermaidChart", () => {
  it("quotes square labels that start with @", () => {
    const input = "flowchart TD\n  C[@splinetool/viewer]"
    expect(sanitizeMermaidChart(input)).toBe(
      'flowchart TD\n  C["@splinetool/viewer"]'
    )
  })

  it("quotes diamond and round labels that start with @", () => {
    const input = "flowchart LR\n  A{@scope/pkg}\n  B(@scope/pkg)"
    expect(sanitizeMermaidChart(input)).toBe(
      'flowchart LR\n  A{"@scope/pkg"}\n  B("@scope/pkg")'
    )
  })

  it("leaves already-quoted @ labels unchanged", () => {
    const input = 'flowchart TD\n  C["@splinetool/viewer"]'
    expect(sanitizeMermaidChart(input)).toBe(input)
  })

  it("quotes rectangle labels with nested brackets", () => {
    const input = "flowchart TD\n  Vosk[return []]"
    expect(sanitizeMermaidChart(input)).toBe('flowchart TD\n  Vosk["return []"]')
  })

  it("replaces semicolons in sequence diagram message text", () => {
    const input = "sequenceDiagram\n  A->>B: hello; world"
    expect(sanitizeMermaidChart(input)).toBe(
      "sequenceDiagram\n  A->>B: hello, world"
    )
  })

  it("repairs the common 3D stack decision flowchart", () => {
    const input = `flowchart TD
    A[Need web-based 3D] --> B{Keep a Spline-created scene?}
    B -->|Yes, simple embed| C[@splinetool/viewer]
    B -->|Yes, React integration| D[@splinetool/react-spline]
    B -->|No / rebuilding scene| E{Primary stack?}
    E -->|React| F[React Three Fiber + Drei]`
    const output = sanitizeMermaidChart(input)
    expect(output).toContain('C["@splinetool/viewer"]')
    expect(output).toContain('D["@splinetool/react-spline"]')
    expect(output).toContain("F[React Three Fiber + Drei]")
  })

  it("escapes ampersands in quoted labels", () => {
    const input =
      'flowchart TD\n  A["4 Sep demo"] --> B["Robot navigation, safety & connectivity"]'
    expect(sanitizeMermaidChart(input)).toContain(
      'B["Robot navigation, safety #amp; connectivity"]'
    )
  })

  it("quotes unquoted labels that contain commas or slashes", () => {
    const input =
      "flowchart TD\n  A[Demo runbook, roles] --> B[UI/design handover]"
    const output = sanitizeMermaidChart(input)
    expect(output).toContain('A["Demo runbook, roles"]')
    expect(output).toContain('B["UI/design handover"]')
  })

  it("normalizes curly quotes around labels", () => {
    const input = "flowchart TD\n  A[\u201Chello & world\u201D]"
    expect(sanitizeMermaidChart(input)).toBe('flowchart TD\n  A["hello #amp; world"]')
  })
})

describe("splitMermaidCharts", () => {
  it("keeps a single diagram intact", () => {
    const input = `flowchart LR
  A["PDF / image"] --> B["OCR"]`
    expect(splitMermaidCharts(input)).toEqual([input])
  })

  it("splits multiple flowchart roots in one fence", () => {
    const input = `flowchart LR
  A["PDF / image"] --> B["Render"]
  B --> C["OCR"]

flowchart TD
  A["PDF intake"] --> B{"Born-digital?"}
  B -->|"Yes"| C["Extract"]`
    const parts = splitMermaidCharts(input)
    expect(parts).toHaveLength(2)
    expect(parts[0]).toMatch(/^flowchart LR/)
    expect(parts[0]).toContain('A["PDF / image"]')
    expect(parts[1]).toMatch(/^flowchart TD/)
    expect(parts[1]).toContain("PDF intake")
  })
})

describe("isIncompleteMermaidPrefix", () => {
  it("detects streamed diagram keyword prefixes", () => {
    expect(isIncompleteMermaidPrefix("flow")).toBe(true)
    expect(isIncompleteMermaidPrefix("flowc")).toBe(true)
    expect(isIncompleteMermaidPrefix("seq")).toBe(true)
    expect(isIncompleteMermaidPrefix("flowchart")).toBe(false)
    expect(isIncompleteMermaidPrefix("flowchart LR")).toBe(false)
    expect(isIncompleteMermaidPrefix("graph TD")).toBe(false)
    expect(isIncompleteMermaidPrefix("not a diagram")).toBe(false)
  })
})
