import { ErrorBoundary } from "react-error-boundary"
import { Suspense, useEffect } from "react"
import { Outlet, useLocation } from "@tanstack/react-router"

import { Button } from "@/components/ui/button"
import { Toaster } from "@/components/ui/sonner"
import { useI18n } from "@/lib/i18n-context"

const App = () => {
  const { t } = useI18n()
  const location = useLocation()

  useEffect(() => {
    const appTitle = t("app_title")
    const path = location.pathname
    if (path.startsWith("/chat") || path === "/history" || path.startsWith("/shared/")) {
      return
    }
    if (path.startsWith("/settings/")) {
      document.title = t("app_title_settings", { app: appTitle })
      return
    }
    if (path === "/usage") {
      document.title = t("app_title_usage", { app: appTitle })
      return
    }
    if (path === "/login") {
      document.title = t("app_title_login", { app: appTitle })
      return
    }
    if (path === "/register") {
      document.title = t("app_title_register", { app: appTitle })
      return
    }
    if (path === "/reset-password") {
      document.title = t("app_title_reset_password", { app: appTitle })
      return
    }
    if (path === "/invite") {
      document.title = t("app_title_invite", { app: appTitle })
      return
    }
    document.title = appTitle
  }, [location.pathname, t])

  return (
    <ErrorBoundary
      fallbackRender={({ error, resetErrorBoundary }) => {
        const message = error instanceof Error ? error.message : t("common_error")
        return (
          <div className="flex flex-col items-center justify-center min-h-screen gap-4 p-6 text-center">
            <h1 className="text-lg font-semibold">{t("app_error_title")}</h1>
            <p className="text-sm text-muted-foreground max-w-md">{message}</p>
            <div className="flex gap-2">
              <Button onClick={() => resetErrorBoundary()}>{t("common_try_again")}</Button>
              <Button variant="outline" onClick={() => window.location.reload()}>
                {t("common_reload")}
              </Button>
            </div>
          </div>
        )
      }}
    >
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:top-4 focus:left-4 focus:z-50 focus:rounded-md focus:bg-background focus:px-3 focus:py-2 focus:text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
      >
        {t("common_skip_to_main")}
      </a>
      <Suspense fallback={null}>
        <Outlet />
      </Suspense>
      <Toaster />
    </ErrorBoundary>
  )
}

export default App
