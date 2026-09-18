import { describe, expect, it } from "vitest"

import {
  classifyCoworkContent,
  looksLikeBinaryOffice,
  looksLikeRtf,
  readableCoworkContent,
  rtfToPlainText,
} from "@/pages/chat/normalizeCoworkContent"

describe("normalizeCoworkContent", () => {
  it("detects RTF payloads", () => {
    expect(looksLikeRtf("{\\rtf1\\ansi Hello}")).toBe(true)
    expect(looksLikeRtf("  {\\rtf1\\ansi\\deff0 Hello}")).toBe(true)
    expect(looksLikeRtf("# Markdown heading")).toBe(false)
  })

  it("strips RTF down to readable text", () => {
    const rtf =
      "{\\rtf1\\ansi\\ansicpg65001\\deff0" +
      "{\\fonttbl{\\f0 Times New Roman;}}" +
      "{\\stylesheet{\\s0 Normal;}}" +
      "\\f0\\fs24 Liguma priek\\u353?mets\\par" +
      "\\b Pasutitajs\\b0 un Piegadatajs\\par}"
    const plain = rtfToPlainText(rtf)
    expect(plain).toContain("Liguma")
    expect(plain).toContain("Pasutitajs")
    expect(plain).toContain("Piegadatajs")
    expect(plain).not.toMatch(/\\rtf|fonttbl|stylesheet/)
  })

  it("passes through non-RTF content", () => {
    expect(rtfToPlainText("Already plain.")).toBe("Already plain.")
    expect(readableCoworkContent("# Hello")).toBe("# Hello")
  })

  it("classifies binary office blobs", () => {
    const zipish = "PK\u0003\u0004" + "x".repeat(64)
    expect(looksLikeBinaryOffice(zipish)).toBe(true)
    expect(classifyCoworkContent(zipish)).toBe("binary-office")
    expect(readableCoworkContent(zipish)).toBe("")
  })
})
