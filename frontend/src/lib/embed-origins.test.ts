import { describe, expect, it } from "vitest"

import {
  normalizeEmbedOriginPattern,
  originAllowedByEmbedList,
  parseEmbedOrigins,
  ppConnectApiKeyFromMessage,
} from "@/lib/embed-origins"

describe("embed origins", () => {
  it("parses comma/space lists", () => {
    expect(parseEmbedOrigins("https://a.com, https://b.com")).toEqual([
      "https://a.com",
      "https://b.com",
    ])
  })

  it("normalizes bare wildcard and host patterns", () => {
    expect(normalizeEmbedOriginPattern("*.pitchpatterns.com")).toBe(
      "https://*.pitchpatterns.com"
    )
    expect(normalizeEmbedOriginPattern("https://*.pitchpatterns.com")).toBe(
      "https://*.pitchpatterns.com"
    )
    expect(normalizeEmbedOriginPattern("dev.pitchpatterns.com")).toBe(
      "https://dev.pitchpatterns.com"
    )
  })

  it("matches exact origins", () => {
    expect(
      originAllowedByEmbedList("https://app.pitchpatterns.com", [
        "https://app.pitchpatterns.com",
      ])
    ).toBe(true)
  })

  it("matches https://*.host and bare *.host for subdomains", () => {
    for (const list of [
      ["https://*.pitchpatterns.com"],
      ["*.pitchpatterns.com"],
    ]) {
      expect(originAllowedByEmbedList("https://app.pitchpatterns.com", list)).toBe(
        true
      )
      expect(originAllowedByEmbedList("https://dev.pitchpatterns.com", list)).toBe(
        true
      )
      expect(
        originAllowedByEmbedList("https://foo.bar.pitchpatterns.com", list)
      ).toBe(true)
      expect(originAllowedByEmbedList("https://pitchpatterns.com", list)).toBe(
        false
      )
      expect(originAllowedByEmbedList("http://app.pitchpatterns.com", list)).toBe(
        false
      )
      expect(originAllowedByEmbedList("https://evil.com", list)).toBe(false)
    }
  })

  it("reads ppConnect api key aliases", () => {
    expect(ppConnectApiKeyFromMessage({ apiKey: " a " })).toBe("a")
    expect(ppConnectApiKeyFromMessage({ api_key: "b" })).toBe("b")
    expect(ppConnectApiKeyFromMessage({ key: "c" })).toBe("c")
    expect(ppConnectApiKeyFromMessage({})).toBe("")
  })
})
