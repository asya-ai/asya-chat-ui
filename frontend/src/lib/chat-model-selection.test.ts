import { describe, expect, it } from "vitest"

import {
  resolveChatModelSelection,
  selectableChatModelsFrom,
} from "@/lib/chat-model-selection"
import type { ChatModel } from "@/lib/types"

const model = (overrides: Partial<ChatModel> & Pick<ChatModel, "id">): ChatModel => ({
  provider: "openai",
  model_name: "gpt-test",
  display_name: "Test",
  is_active: true,
  display_order: 0,
  ...overrides,
})

describe("resolveChatModelSelection", () => {
  it("keeps a valid current selection", () => {
    const models = [model({ id: "a" }), model({ id: "b" })]
    expect(resolveChatModelSelection(models, "b", "a")).toBe("b")
  })

  it("falls back to stored id when current is stale", () => {
    const models = [model({ id: "a" }), model({ id: "b" })]
    expect(resolveChatModelSelection(models, "missing", "b")).toBe("b")
  })

  it("defaults to first available model", () => {
    const models = [
      model({ id: "a", is_available: false }),
      model({ id: "b" }),
    ]
    expect(resolveChatModelSelection(models, undefined, "missing")).toBe("b")
  })

  it("excludes embedding models from the picker pool", () => {
    const models = [
      model({ id: "emb", model_name: "text-embedding-3-small" }),
      model({ id: "chat" }),
    ]
    const selectable = selectableChatModelsFrom(models)
    expect(selectable.map((m) => m.id)).toEqual(["chat"])
    expect(resolveChatModelSelection(selectable, undefined, "emb")).toBe("chat")
  })
})