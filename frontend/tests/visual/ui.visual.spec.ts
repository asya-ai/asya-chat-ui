import { expect, test, type Page } from "playwright/test"
import { readFileSync } from "node:fs"
import pixelmatch from "pixelmatch"
import { PNG } from "pngjs"

const themes = ["light", "dark"] as const

const populatedChats = [
  {
    id: "chat-1",
    title: "Quarterly product strategy",
    model_id: "model-1",
    created_at: "2026-07-28T17:00:00.000Z",
    last_activity_at: "2026-07-28T18:30:00.000Z",
  },
  {
    id: "chat-2",
    title: "API migration checklist",
    model_id: "model-1",
    created_at: "2026-07-28T15:00:00.000Z",
    last_activity_at: "2026-07-28T16:00:00.000Z",
  },
  {
    id: "chat-3",
    title: "Design system review",
    model_id: "model-1",
    created_at: "2026-07-27T12:00:00.000Z",
    last_activity_at: "2026-07-27T13:00:00.000Z",
  },
]

const populatedMessages: Record<string, unknown[]> = {
  "chat-1": [
    {
      id: "message-user-1",
      role: "user",
      content: "Please review the launch plan and identify the highest-risk assumptions.",
      created_at: "2026-07-28T18:29:00.000Z",
      attachments: [
        {
          id: "attachment-pdf",
          file_name: "launch-plan.pdf",
          content_type: "application/pdf",
          content_url: "data:application/pdf;base64,JVBERi0xLjQK",
        },
        {
          id: "attachment-image",
          file_name: "launch-preview.jpeg",
          content_type: "image/jpeg",
          content_url: "/figma-profile.jpeg",
        },
      ],
    },
    {
      id: "message-assistant-1",
      role: "assistant",
      content:
        "## Launch review\n\nThe largest risk is **activation**. Validate onboarding completion and first-week retention before increasing acquisition spend.\n\n1. Test the onboarding flow.\n2. Confirm analytics coverage.\n3. Prepare a rollback plan.",
      created_at: "2026-07-28T18:30:00.000Z",
      model_id: "model-1",
      model_name: "gpt-5.3-chat-latest",
      generation_status: "completed",
    },
  ],
  "chat-2": [
    {
      id: "message-user-2",
      role: "user",
      content: "What should move first in the API migration?",
      created_at: "2026-07-28T15:59:00.000Z",
    },
    {
      id: "message-assistant-2",
      role: "assistant",
      content:
        "Start with the compatibility layer, then migrate read-only endpoints before write paths.",
      created_at: "2026-07-28T16:00:00.000Z",
      model_id: "model-1",
      model_name: "gpt-5.3-chat-latest",
      generation_status: "completed",
    },
  ],
  "chat-3": [],
}

const applyTheme = async (page: Page, theme: (typeof themes)[number]) => {
  await page.addInitScript((value) => {
    localStorage.setItem("chatui_theme", value)
    localStorage.setItem("chatui_locale", "en")
  }, theme)
}

const waitForStableUi = async (page: Page) => {
  await page.evaluate(async () => {
    await document.fonts.ready
  })
}

const mockAuthenticatedApi = async (
  page: Page,
  options: {
    chats?: unknown[]
    messagesByChat?: Record<string, unknown[] | (() => unknown[])>
  } = {}
) => {
  const chats = options.chats ?? []
  const messagesByChat = options.messagesByChat ?? {}
  await page.addInitScript(() => {
    localStorage.setItem("chatui_token", "visual-test-token")
    localStorage.setItem("chatui_org", "org-1")
    localStorage.setItem("chatui_model", "model-1")
  })

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url())
    let body: unknown = []

    if (url.pathname.endsWith("/auth/me")) {
      body = {
        id: "user-1",
        email: "bonifacijs@example.com",
        username: "bonifacijs",
        display_name: "Bonifācijs Bono Bigs",
        avatar_url: "/figma-profile.jpeg",
        is_super_admin: true,
        is_admin: true,
        memory_enabled: false,
        locale: "en",
      }
    } else if (url.pathname.endsWith("/orgs/mine")) {
      body = [{ id: "org-1", name: "Asya SIA" }]
    } else if (url.pathname.endsWith("/models")) {
      body = [
        {
          id: "model-1",
          provider: "openai",
          model_name: "gpt-5.3-chat-latest",
          display_name: "gpt-5.3-chat-latest",
          is_available: true,
          supports_image_output: false,
        },
        {
          id: "image-model",
          provider: "openai",
          model_name: "gpt-image-1",
          display_name: "Image",
          is_available: true,
          supports_image_output: true,
        },
      ]
    } else if (url.pathname.endsWith("/chats") && route.request().method() === "GET") {
      body = chats
    } else if (url.pathname.endsWith("/uploads")) {
      body = {
        id: "upload-1",
        file_name: "notes.txt",
        content_type: "text/plain",
        size_bytes: 24,
        created_at: "2026-07-28T18:31:00.000Z",
      }
    } else {
      const messageMatch = url.pathname.match(/\/chats\/([^/]+)\/messages$/)
      if (messageMatch && route.request().method() === "GET") {
        const fixture = messagesByChat[decodeURIComponent(messageMatch[1])]
        body = typeof fixture === "function" ? fixture() : fixture ?? []
      }
    }

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body),
    })
  })
}

for (const theme of themes) {
  test(`login · ${theme}`, async ({ page }) => {
    await applyTheme(page, theme)
    await page.goto("/login")
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible()
    await waitForStableUi(page)
    await expect(page).toHaveScreenshot(`login-${theme}.png`, { fullPage: true })
  })

  test(`empty chat · ${theme}`, async ({ page }) => {
    await applyTheme(page, theme)
    await mockAuthenticatedApi(page)
    await page.goto("/chat")
    await expect(
      page.getByRole("heading", { name: "Hey, Bonifācijs. How can I help you?" })
    ).toBeVisible()
    await expect(page.getByPlaceholder("Ask anything")).toBeVisible()
    await waitForStableUi(page)
    await expect(page).toHaveScreenshot(`empty-chat-${theme}.png`, { fullPage: true })
  })

  test(`non-empty chat · ${theme}`, async ({ page }) => {
    await applyTheme(page, theme)
    await mockAuthenticatedApi(page, {
      chats: populatedChats,
      messagesByChat: populatedMessages,
    })
    await page.goto("/chat/chat-1")
    await expect(page.getByRole("heading", { name: "Launch review" })).toBeVisible()
    await expect(page.getByText("launch-plan.pdf", { exact: true })).toBeVisible()
    await waitForStableUi(page)
    await expect(page).toHaveScreenshot(`non-empty-chat-${theme}.png`, { fullPage: true })
  })

  test(`chat history interaction · ${theme}`, async ({ page }, testInfo) => {
    await applyTheme(page, theme)
    await mockAuthenticatedApi(page, {
      chats: populatedChats,
      messagesByChat: populatedMessages,
    })
    await page.goto("/chat/chat-1")
    await expect(page.getByRole("heading", { name: "Launch review" })).toBeVisible()

    if (testInfo.project.name === "mobile") {
      await page.getByRole("button", { name: "Toggle Sidebar" }).click()
    }
    const sidebar =
      testInfo.project.name === "mobile" ? page.getByRole("dialog") : page.locator("aside")
    await sidebar.getByRole("button", { name: "History", exact: true }).click()
    await expect(page).toHaveURL(/\/history$/)

    const historyItem = page.getByText("API migration checklist", { exact: true })
    await expect(historyItem).toBeVisible()
    await expect(page).toHaveScreenshot(`chat-history-${theme}.png`, { fullPage: true })

    await historyItem.click()
    await expect(page).toHaveURL(/\/chat\/chat-2$/)
    await expect(
      page.getByText(
        "Start with the compatibility layer, then migrate read-only endpoints before write paths."
      )
    ).toBeVisible()
  })

  test(`chat attachments · ${theme}`, async ({ page }) => {
    await applyTheme(page, theme)
    await mockAuthenticatedApi(page, {
      chats: populatedChats,
      messagesByChat: populatedMessages,
    })
    await page.goto("/chat/chat-1")
    await expect(page.getByRole("heading", { name: "Launch review" })).toBeVisible()

    await page.locator('main input[type="file"]').setInputFiles({
      name: "notes.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("Deterministic attachment"),
    })
    await expect(page.getByText("notes.txt", { exact: true })).toBeVisible()
    await expect(page).toHaveScreenshot(`draft-attachment-${theme}.png`, { fullPage: true })

    await page.getByAltText("launch-preview.jpeg").click()
    const preview = page.getByRole("dialog")
    await expect(preview.getByAltText("launch-preview.jpeg")).toBeVisible()
    await expect(page).toHaveScreenshot(`attachment-preview-${theme}.png`, {
      fullPage: true,
    })
  })

  test(`chat streaming and stop · ${theme}`, async ({ page }) => {
    await applyTheme(page, theme)
    await mockAuthenticatedApi(page, {
      chats: populatedChats,
      messagesByChat: populatedMessages,
    })
    await page.routeWebSocket("**/api/chats/chat-2/ws", (webSocket) => {
      webSocket.onMessage(() => {
        webSocket.send(JSON.stringify({ user_message_id: "message-user-stream" }))
        webSocket.send(
          JSON.stringify({
            task_id: "task-stream",
            assistant_message_id: "message-assistant-stream",
          })
        )
        webSocket.send(
          JSON.stringify({
            delta: "Reviewing the rollout dependencies…",
            task_id: "task-stream",
          })
        )
      })
    })
    await page.goto("/chat/chat-2")
    const composer = page.getByPlaceholder("Ask anything")
    await composer.fill("Summarize the next rollout step.")
    await page.getByRole("button", { name: "Send" }).click()
    await expect(page.getByText("Reviewing the rollout dependencies…")).toBeVisible()
    await expect(page.getByRole("button", { name: "Stop" })).toBeVisible()
    const messageList = page.locator('[aria-live="polite"]')
    await messageList.evaluate((element) => {
      element.scrollTo({ top: element.scrollHeight, behavior: "auto" })
    })
    await expect
      .poll(() =>
        messageList.evaluate((element) =>
          Math.abs(element.scrollHeight - element.clientHeight - element.scrollTop)
        )
      )
      .toBeLessThan(2)
    await expect(page).toHaveScreenshot(`streaming-chat-${theme}.png`, { fullPage: true })

    await page.getByRole("button", { name: "Stop" }).click()
    await expect(page.getByRole("button", { name: "Stop" })).toBeHidden()
  })

  test(`failed chat send · ${theme}`, async ({ page }) => {
    let failed = false
    const failedMessages = () =>
      failed
        ? [
            ...populatedMessages["chat-2"],
            {
              id: "message-user-failed",
              role: "user",
              content: "Generate the final launch brief.",
              created_at: "2026-07-28T18:32:00.000Z",
            },
            {
              id: "message-assistant-failed",
              role: "assistant",
              content: "The model is temporarily unavailable.",
              created_at: "2026-07-28T18:32:01.000Z",
              model_id: "model-1",
              model_name: "gpt-5.3-chat-latest",
              generation_status: "failed",
            },
          ]
        : populatedMessages["chat-2"]

    await applyTheme(page, theme)
    await mockAuthenticatedApi(page, {
      chats: populatedChats,
      messagesByChat: {
        ...populatedMessages,
        "chat-2": failedMessages,
      },
    })
    await page.routeWebSocket("**/api/chats/chat-2/ws", (webSocket) => {
      webSocket.onMessage(() => {
        failed = true
        webSocket.send(JSON.stringify({ error: "The model is temporarily unavailable." }))
      })
    })
    await page.goto("/chat/chat-2")
    const composer = page.getByPlaceholder("Ask anything")
    await composer.fill("Generate the final launch brief.")
    await page.getByRole("button", { name: "Send" }).click()
    await expect(page.getByText("The model is temporarily unavailable.")).toBeVisible()
    await expect(page).toHaveScreenshot(`failed-chat-${theme}.png`, { fullPage: true })
  })

  test(`Figma parity · ${theme}`, async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop", "Figma source frames are desktop-only")
    await applyTheme(page, theme)
    await mockAuthenticatedApi(page)
    await page.goto("/chat")
    await expect(
      page.getByRole("heading", { name: "Hey, Bonifācijs. How can I help you?" })
    ).toBeVisible()
    await waitForStableUi(page)

    const actual = PNG.sync.read(await page.screenshot({ fullPage: true }))
    const expected = PNG.sync.read(
      readFileSync(new URL(`./figma/empty-chat-${theme}.png`, import.meta.url))
    )

    expect({ width: actual.width, height: actual.height }).toEqual({
      width: expected.width,
      height: expected.height,
    })

    // Canvas is intentionally absent from this product. Exclude only its Figma row.
    for (let y = 217; y < 253; y += 1) {
      for (let x = 8; x < 221; x += 1) {
        const offset = (y * actual.width + x) * 4
        actual.data.copy(expected.data, offset, offset, offset + 4)
      }
    }

    const diff = new PNG({ width: actual.width, height: actual.height })
    const differentPixels = pixelmatch(
      actual.data,
      expected.data,
      diff.data,
      actual.width,
      actual.height,
      { threshold: 0.1, includeAA: false }
    )
    const differentRatio = differentPixels / (actual.width * actual.height)

    if (differentRatio > 0.0045) {
      await testInfo.attach(`figma-diff-${theme}`, {
        body: PNG.sync.write(diff),
        contentType: "image/png",
      })
    }
    expect(differentRatio).toBeLessThanOrEqual(0.0045)
  })
}
