import type { ReactNode } from "react"
import { Navigate, Route, Routes } from "react-router-dom"
import { ErrorBoundary } from "react-error-boundary"

import { useAuth } from "@/lib/auth-context"
import { Button } from "@/components/ui/button"
import { ChatPage } from "@/pages/ChatPage"
import { InviteAcceptPage } from "@/pages/InviteAccept"
import { LoginPage } from "@/pages/Login"
import { MePage } from "@/pages/MePage"
import { OrgPage } from "@/pages/OrgPage"
import { RegisterPage } from "@/pages/Register"
import { ResetPasswordPage } from "@/pages/ResetPassword"
import { SsoCallbackPage } from "@/pages/SsoCallback"
import { UsagePage } from "@/pages/UsagePage"

const RequireAuth = ({ children }: { children: ReactNode }) => {
  const { token } = useAuth()
  if (!token) {
    return <Navigate to="/login" replace />
  }
  return children
}

const App = () => {
  const { token } = useAuth()
  return (
    <ErrorBoundary
      fallbackRender={({ error, resetErrorBoundary }) => {
        const message = error instanceof Error ? error.message : "Unexpected error"
        return (
        <div className="flex flex-col items-center justify-center min-h-screen gap-4 p-6 text-center">
          <h1 className="text-lg font-semibold">Something went wrong</h1>
          <p className="text-sm text-muted-foreground max-w-md">
            {message}
          </p>
          <div className="flex gap-2">
            <Button onClick={() => resetErrorBoundary()}>Try again</Button>
            <Button variant="outline" onClick={() => window.location.reload()}>
              Reload
            </Button>
          </div>
        </div>
        )
      }}
    >
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
          path="/usage"
          element={
            <RequireAuth>
              <UsagePage />
            </RequireAuth>
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </ErrorBoundary>
  )
}

export default App
