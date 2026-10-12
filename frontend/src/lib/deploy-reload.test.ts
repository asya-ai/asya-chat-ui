import { afterEach, describe, expect, it, vi } from "vitest"

import {
  deploySignalChanged,
  hardReloadOnce,
  installStaleAssetRecovery,
  isStaleChunkError,
} from "@/lib/deploy-reload"

const memoryStorage = () => {
  const store: Record<string, string> = {}
  return {
    getItem(key: string) {
      return store[key] ?? null
    },
    setItem(key: string, value: string) {
      store[key] = value
    },
  } as Storage
}

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
    expect(
      isStaleChunkError(
        new Error(
          'Expected a JavaScript module script but the server responded with a MIME type of "text/html"'
        )
      )
    ).toBe(true)
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

describe("hardReloadOnce", () => {
  it("reloads once then guards within the window", () => {
    const storage = memoryStorage()
    const reload = vi.fn()
    const win = { location: { reload } } as unknown as Window

    expect(hardReloadOnce(storage, win)).toBe(true)
    expect(reload).toHaveBeenCalledTimes(1)
    expect(hardReloadOnce(storage, win)).toBe(false)
    expect(reload).toHaveBeenCalledTimes(1)
  })
})

describe("installStaleAssetRecovery", () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("reloads on vite:preloadError and prevents default when reload starts", () => {
    const reload = vi.fn(() => true)
    const listeners = new Map<string, EventListener>()
    const win = {
      addEventListener(type: string, listener: EventListener) {
        listeners.set(type, listener)
      },
      removeEventListener(type: string) {
        listeners.delete(type)
      },
    } as unknown as Window

    const stop = installStaleAssetRecovery({ reload, win })
    const preventDefault = vi.fn()
    listeners.get("vite:preloadError")?.({ preventDefault } as unknown as Event)

    expect(reload).toHaveBeenCalledTimes(1)
    expect(preventDefault).toHaveBeenCalledTimes(1)
    stop()
  })

  it("does not preventDefault when reload is guarded", () => {
    const reload = vi.fn(() => false)
    const listeners = new Map<string, EventListener>()
    const win = {
      addEventListener(type: string, listener: EventListener) {
        listeners.set(type, listener)
      },
      removeEventListener(type: string) {
        listeners.delete(type)
      },
    } as unknown as Window

    const stop = installStaleAssetRecovery({ reload, win })
    const preventDefault = vi.fn()
    listeners.get("vite:preloadError")?.({ preventDefault } as unknown as Event)

    expect(reload).toHaveBeenCalledTimes(1)
    expect(preventDefault).not.toHaveBeenCalled()
    stop()
  })
})
