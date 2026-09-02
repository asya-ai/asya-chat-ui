import { describe, expect, it } from "vitest"
import { getSlashPromptTrigger } from "./chat-input"

describe("getSlashPromptTrigger", () => {
  it("detects slash at start of input", () => {
    expect(getSlashPromptTrigger("/summ", 5)).toEqual({ start: 0, query: "summ" })
  })

  it("detects slash after whitespace", () => {
    expect(getSlashPromptTrigger("hello /foo", 10)).toEqual({ start: 6, query: "foo" })
  })

  it("returns empty query for bare slash", () => {
    expect(getSlashPromptTrigger("/", 1)).toEqual({ start: 0, query: "" })
  })

  it("returns null when slash is not active", () => {
    expect(getSlashPromptTrigger("hello world", 11)).toBeNull()
  })

  it("returns null after whitespace following slash command", () => {
    expect(getSlashPromptTrigger("/foo bar", 8)).toBeNull()
  })
})
