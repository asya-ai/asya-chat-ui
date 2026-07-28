import type { ReactNode } from "react"
import { Navigate, Route, Routes, useLocation } from "react-router-dom"
import { ErrorBoundary } from "react-error-boundary"
import { Suspense, lazy, useEffect } from "react"

import { useAuth } from "@/lib/auth-context"
import { Button } from "@/components/ui/button"
import { useI18n } from "@/lib/i18n-context"

const ChatPage = lazy(() => import("@/pages/ChatPage").then((mod) => ({ default: mod.ChatPage })))
const ProjectsPage = lazy(() =>
  import("@/pages/projects/ProjectsPage").then((mod) => ({ default: mod.ProjectsPage }))
)
const ProjectPage = lazy(() =>
  import("@/pages/projects/ProjectPage").then((mod) => ({ default: mod.ProjectPage }))
)
const InviteAcceptPage = lazy(() =>
  import("@/pages/InviteAccept").then((mod) => ({ default: mod.InviteAcceptPage }))
)
const LoginPage = lazy(() => import("@/pages/Login").then((mod) => ({ default: mod.LoginPage })))
const MePage = lazy(() => import("@/pages/MePage").then((mod) => ({ default: mod.MePage })))
const OrgPage = lazy(() => import("@/pages/OrgPage").then((mod) => ({ default: mod.OrgPage })))
const RegisterPage = lazy(() =>
  import("@/pages/Register").then((mod) => ({ default: mod.RegisterPage }))
)
const ResetPasswordPage = lazy(() =>
  import("@/pages/ResetPassword").then((mod) => ({ default: mod.ResetPasswordPage }))
)
const SsoCallbackPage = lazy(() =>
  import("@/pages/SsoCallback").then((mod) => ({ default: mod.SsoCallbackPage }))
)
const UsagePage = lazy(() =>
  import("@/pages/UsagePage").then((mod) => ({ default: mod.UsagePage }))
)

const RequireAuth = ({ children }: { children: ReactNode }) => {
  const { token } = useAuth()
  if (!token) {
    return <Navigate to="/login" replace />
  }
  return children
}

const App = () => {
  const { token } = useAuth()
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
          <p className="text-sm text-muted-foreground max-w-md">
            {message}
          </p>
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
        <Routes>
        <Route
          path="/"
          element={token ? <Navigate to="/chat" replace /> : <Navigate to="/login" replace />}
        />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/sso-callback" element={<SsoCallbackPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route path="/invite" element={<InviteAcceptPage />} />
        <Route path="/reset-password" element={<ResetPasswordPage />} />
        <Route
          path="/settings"
          element={<Navigate to="/settings/me" replace />}
        />
        <Route
          path="/settings/organisations"
          element={
            <RequireAuth>
              <OrgPage />
            </RequireAuth>
          }
        />
        <Route
          path="/settings/organisation"
          element={
            <RequireAuth>
              <OrgPage />
            </RequireAuth>
          }
        />
        <Route
          path="/settings/users"
          element={
            <RequireAuth>
              <OrgPage />
            </RequireAuth>
          }
        />
        <Route
          path="/settings/models"
          element={
            <RequireAuth>
              <OrgPage />
            </RequireAuth>
          }
        />
        <Route
          path="/settings/me"
          element={
            <RequireAuth>
              <MePage />
            </RequireAuth>
          }
        />
        <Route
          path="/chat"
          element={
            <RequireAuth>
              <ChatPage />
            </RequireAuth>
          }
        />
        <Route
          path="/chat/:chatId"
          element={
            <RequireAuth>
              <ChatPage />
            </RequireAuth>
          }
        />
        <Route
          path="/history"
          element={
            <RequireAuth>
              <ChatPage />
            </RequireAuth>
          }
        />
        <Route
          path="/projects"
          element={
            <RequireAuth>
              <ProjectsPage />
            </RequireAuth>
          }
        />
        <Route
          path="/projects/:projectId"
          element={
            <RequireAuth>
              <ProjectPage />
            </RequireAuth>
          }
        />
        <Route path="/settings/agents" element={<Navigate to="/projects" replace />} />
        <Route path="/agents" element={<Navigate to="/projects" replace />} />
        <Route
          path="/shared/:shareToken"
          element={
            <RequireAuth>
              <ChatPage />
            </RequireAuth>
          }
        />
        <Route
          path="/usage"
          element={
            <RequireAuth>
              <UsagePage />
            </RequireAuth>
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </ErrorBoundary>
  )
}

export default App
