import { describe, expect, it } from "vitest"

import type { ChatMessage } from "@/lib/types"

import {
  collectChatImageUrlMap,
  resolveCoworkPresentationMarkdown,
} from "./resolveCoworkPresentationImages"

describe("resolveCoworkPresentationMarkdown", () => {
  it("rewrites bare generated file names to attachment content URLs", () => {
    const messages: ChatMessage[] = [
      {
        id: "a1",
        role: "assistant",
        content: "",
        created_at: "2026-01-01T00:00:00Z",
        attachments: [
          {
            id: "att-1",
            file_name: "generated-32e96fcd8d45.png",
            content_type: "image/png",
            content_url: "/api/chats/c1/messages/a1/attachments/att-1/content",
          },
        ],
      },
    ]
    const map = collectChatImageUrlMap(messages)
    const md = "![caption](generated-32e96fcd8d45.png)"
    expect(resolveCoworkPresentationMarkdown(md, map)).toBe(
      "![caption](/api/chats/c1/messages/a1/attachments/att-1/content)"
    )
  })
})
