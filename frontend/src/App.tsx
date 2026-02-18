import type { ReactNode } from "react"
import { Navigate, Route, Routes } from "react-router-dom"

import { useAuth } from "@/lib/auth-context"
import { ChatPage } from "@/pages/ChatPage"
import { InviteAcceptPage } from "@/pages/InviteAccept"
import { LoginPage } from "@/pages/Login"
import { MePage } from "@/pages/MePage"
import { OrgPage } from "@/pages/OrgPage"
import { RegisterPage } from "@/pages/Register"
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
    <Routes>
      <Route
        path="/"
        element={token ? <Navigate to="/chat" replace /> : <Navigate to="/login" replace />}
      />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route path="/invite" element={<InviteAcceptPage />} />
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
  )
}

export default App
