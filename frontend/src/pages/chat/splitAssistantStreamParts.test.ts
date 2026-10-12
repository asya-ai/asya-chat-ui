import { describe, expect, it } from "vitest"

import { getFinalAnswerText, splitAssistantStreamParts } from "@/pages/chat/MessageBubble"
import type { ChatMessage } from "@/lib/types"

describe("splitAssistantStreamParts", () => {
  it("keeps all parts as thoughts while generating", () => {
    const parts = [
      { type: "text" as const, text: "I'll look that up." },
      { type: "action" as const, label: "Thoughts" },
      { type: "text" as const, text: "Checking sources…" },
    ]
    expect(splitAssistantStreamParts(parts, true)).toEqual({
      thoughtParts: parts,
      finalTextParts: [],
    })
  })

  it("puts intermediate speech in thoughts and only trailing text after last action as final", () => {
    const parts = [
      { type: "text" as const, text: "I'll check headlines." },
      { type: "action" as const, label: "Thoughts" },
      { type: "action" as const, label: "Looking up reuters" },
      { type: "text" as const, text: "I'll verify next." },
      { type: "action" as const, label: "Exploring kathmandupost" },
      { type: "text" as const, text: "Here is the rundown." },
    ]
    const split = splitAssistantStreamParts(parts, false)
    expect(split.thoughtParts).toEqual(parts.slice(0, 5))
    expect(split.finalTextParts).toEqual([{ type: "text", text: "Here is the rundown." }])
  })

  it("treats plain text with no actions as the final answer", () => {
    const parts = [{ type: "text" as const, text: "Hello." }]
    expect(splitAssistantStreamParts(parts, false)).toEqual({
      thoughtParts: [],
      finalTextParts: parts,
    })
  })
})

describe("getFinalAnswerText", () => {
  it("ignores intermediate speech between tools", () => {
    const msg = {
      content: "fallback",
      stream_parts: [
        { type: "text", text: "I'll check." },
        { type: "action", label: "Thoughts" },
        { type: "text", text: "Still working." },
        { type: "action", label: "Search" },
        { type: "text", text: "Final answer." },
      ],
    } as ChatMessage
    expect(getFinalAnswerText(msg)).toBe("Final answer.")
  })
})
