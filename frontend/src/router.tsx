import { createRouter } from "@tanstack/react-router"

import { routeTree } from "@/routes/routeTree"

export const router = createRouter({
  routeTree,
  defaultPreload: "intent",
  defaultNotFoundComponent: () => null,
})

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router
  }
  interface HistoryState {
    blockedSharedChat?: boolean
  }
}
