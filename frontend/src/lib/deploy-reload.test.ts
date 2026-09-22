import { describe, expect, it } from "vitest"

import {
  deploySignalChanged,
  isStaleChunkError,
} from "@/lib/deploy-reload"

describe("isStaleChunkError", () => {
  it("matches dynamic import failures", () => {
    expect(
      isStaleChunkError(
        new TypeError(
          "Failed to fetch dynamically imported module: https://chat.eldigen.com/assets/ChatPage-FvmsiUAG.js"
        )
      )
    ).toBe(true)
    expect(isStaleChunkError(new Error("Importing a module script failed."))).toBe(true)
    expect(isStaleChunkError(new Error("Loading chunk 12 failed."))).toBe(true)
  })

  it("ignores unrelated errors", () => {
    expect(isStaleChunkError(new Error("Network Error"))).toBe(false)
    expect(isStaleChunkError(null)).toBe(false)
  })
})

describe("deploySignalChanged", () => {
  it("detects frontend or backend build id changes", () => {
    expect(
      deploySignalChanged({ frontend: "a", backend: "x" }, { frontend: "b", backend: "x" })
    ).toBe(true)
    expect(
      deploySignalChanged({ frontend: "a", backend: "x" }, { frontend: "a", backend: "y" })
    ).toBe(true)
  })

  it("ignores missing sides and identical signals", () => {
    expect(deploySignalChanged({ frontend: "a" }, { backend: "x" })).toBe(false)
    expect(deploySignalChanged({ frontend: "a" }, { frontend: "a" })).toBe(false)
    expect(deploySignalChanged({}, { frontend: "a", backend: "x" })).toBe(false)
  })
})
