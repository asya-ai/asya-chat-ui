import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { RouterProvider } from "@tanstack/react-router"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

import { AuthProvider } from "@/lib/auth-context"
import { BrandingProvider } from "@/lib/branding"
import { installStaleAssetRecovery } from "@/lib/deploy-reload"
import { I18nProvider } from "@/lib/i18n-context"
import { applyTheme, getTheme } from "@/lib/theme"
import { TooltipProvider } from "@/components/ui/tooltip"
import { router } from "@/router"
import "katex/dist/katex.min.css"
import "@/index.css"

installStaleAssetRecovery()

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
    },
  },
})

applyTheme(getTheme())

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <I18nProvider>
          <BrandingProvider>
            <TooltipProvider>
              <RouterProvider router={router} />
            </TooltipProvider>
          </BrandingProvider>
        </I18nProvider>
      </AuthProvider>
    </QueryClientProvider>
  </StrictMode>
)
