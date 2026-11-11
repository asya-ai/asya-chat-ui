import { describe, expect, it } from "vitest"

import type { ChatMessage } from "@/lib/types"

import {
  collectChatImageUrlMap,
  mergeAttachmentListIntoImageUrlMap,
  resolveCoworkPresentationMarkdown,
  resolveMarkdownImageSrc,
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

  it("uses preview_url when content_url is missing", () => {
    const map = mergeAttachmentListIntoImageUrlMap({}, [
      {
        file_name: "generated-abc.png",
        content_type: "image/png",
        preview_url: "blob:preview",
      },
    ])
    expect(map["generated-abc.png"]).toBe("blob:preview")
  })

  it("falls back to the sole message image when src is caption text", () => {
    const url = "/api/chats/attachments/x/content?token=t"
    const resolved = resolveMarkdownImageSrc("Attēls ar pievienoto personu", null, {
      fallbackUrl: url,
    })
    expect(resolved).toBe(url)
  })
})
