import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { BrowserRouter } from "react-router-dom"

import { AuthProvider } from "@/lib/auth-context"
import { I18nProvider } from "@/lib/i18n-context"
import { applyTheme, getTheme } from "@/lib/theme"
import { TooltipProvider } from "@/components/ui/tooltip"
import App from "@/App"
import "@/index.css"

applyTheme(getTheme())

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <I18nProvider>
          <TooltipProvider>
            <App />
          </TooltipProvider>
        </I18nProvider>
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>
)
