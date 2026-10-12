import { createRootRoute, createRoute, lazyRouteComponent, redirect } from "@tanstack/react-router"

import App from "@/App"
import { tokenStore } from "@/lib/storage"

const requireAuth = () => {
  if (!tokenStore.get()) {
    throw redirect({ to: "/login" })
  }
}

const rootRoute = createRootRoute({
  component: App,
})

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  beforeLoad: () => {
    throw redirect({ to: tokenStore.get() ? "/chat/{-$chatId}" : "/login" })
  },
})

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/login",
  beforeLoad: ({ location }) => {
    const token = tokenStore.get()
    if (!token) return
    // Embed SSO popup: keep /login?embed=1 so the page can postMessage to the iframe.
    const params = new URLSearchParams(location.searchStr)
    if (params.get("embed") === "1") return
    throw redirect({ to: "/chat/{-$chatId}" })
  },
  component: lazyRouteComponent(() =>
    import("@/pages/Login").then((mod) => ({ default: mod.LoginPage }))
  ),
})

const ssoCallbackRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/sso-callback",
  component: lazyRouteComponent(() =>
    import("@/pages/SsoCallback").then((mod) => ({ default: mod.SsoCallbackPage }))
  ),
})

const registerRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/register",
  component: lazyRouteComponent(() =>
    import("@/pages/Register").then((mod) => ({ default: mod.RegisterPage }))
  ),
})

const inviteRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/invite",
  component: lazyRouteComponent(() =>
    import("@/pages/InviteAccept").then((mod) => ({ default: mod.InviteAcceptPage }))
  ),
})

const resetPasswordRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/reset-password",
  component: lazyRouteComponent(() =>
    import("@/pages/ResetPassword").then((mod) => ({ default: mod.ResetPasswordPage }))
  ),
})

const settingsRedirectRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/settings",
  beforeLoad: () => {
    throw redirect({ to: "/settings/me" })
  },
})

const agentsRedirectRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/agents",
  beforeLoad: () => {
    throw redirect({ to: "/projects" })
  },
})

const settingsAgentsRedirectRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/settings/agents",
  beforeLoad: () => {
    throw redirect({ to: "/projects" })
  },
})

const authedRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: "_authed",
  beforeLoad: requireAuth,
})

const chatRoute = createRoute({
  getParentRoute: () => authedRoute,
  path: "/chat/{-$chatId}",
  component: lazyRouteComponent(() =>
    import("@/pages/ChatPage").then((mod) => ({ default: mod.ChatPage }))
  ),
})

const historyRoute = createRoute({
  getParentRoute: () => authedRoute,
  path: "/history",
  component: lazyRouteComponent(() =>
    import("@/pages/ChatPage").then((mod) => ({ default: mod.ChatPage }))
  ),
})

const sharedRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/shared/$shareToken",
  component: lazyRouteComponent(() =>
    import("@/pages/SharedChatPage").then((mod) => ({ default: mod.SharedChatPage }))
  ),
})

const projectsRoute = createRoute({
  getParentRoute: () => authedRoute,
  path: "/projects",
  component: lazyRouteComponent(() =>
    import("@/pages/projects/ProjectsPage").then((mod) => ({ default: mod.ProjectsPage }))
  ),
})

const projectRoute = createRoute({
  getParentRoute: () => authedRoute,
  path: "/projects/$projectId",
  component: lazyRouteComponent(() =>
    import("@/pages/projects/ProjectPage").then((mod) => ({ default: mod.ProjectPage }))
  ),
})

const usageRoute = createRoute({
  getParentRoute: () => authedRoute,
  path: "/usage",
  component: lazyRouteComponent(() =>
    import("@/pages/UsagePage").then((mod) => ({ default: mod.UsagePage }))
  ),
})

const settingsOrganisationsRoute = createRoute({
  getParentRoute: () => authedRoute,
  path: "/settings/organisations",
  component: lazyRouteComponent(() =>
    import("@/pages/OrgPage").then((mod) => ({ default: mod.OrgPage }))
  ),
})

const settingsOrganisationRoute = createRoute({
  getParentRoute: () => authedRoute,
  path: "/settings/organisation",
  component: lazyRouteComponent(() =>
    import("@/pages/OrgPage").then((mod) => ({ default: mod.OrgPage }))
  ),
})

const settingsUsersRoute = createRoute({
  getParentRoute: () => authedRoute,
  path: "/settings/users",
  component: lazyRouteComponent(() =>
    import("@/pages/OrgPage").then((mod) => ({ default: mod.OrgPage }))
  ),
})

const settingsTeamsRoute = createRoute({
  getParentRoute: () => authedRoute,
  path: "/settings/teams",
  component: lazyRouteComponent(() =>
    import("@/pages/TeamsPage").then((mod) => ({ default: mod.TeamsPage }))
  ),
})

const settingsModelsRoute = createRoute({
  getParentRoute: () => authedRoute,
  path: "/settings/models",
  component: lazyRouteComponent(() =>
    import("@/pages/OrgPage").then((mod) => ({ default: mod.OrgPage }))
  ),
})

const settingsDiagnosisRoute = createRoute({
  getParentRoute: () => authedRoute,
  path: "/settings/diagnosis",
  component: lazyRouteComponent(() =>
    import("@/pages/DiagnosisPage").then((mod) => ({ default: mod.DiagnosisPage }))
  ),
})

const settingsProvidersRoute = createRoute({
  getParentRoute: () => authedRoute,
  path: "/settings/providers",
  component: lazyRouteComponent(() =>
    import("@/pages/InstanceProvidersPage").then((mod) => ({
      default: mod.InstanceProvidersPage,
    }))
  ),
})

const settingsWhitelabelRoute = createRoute({
  getParentRoute: () => authedRoute,
  path: "/settings/whitelabel",
  component: lazyRouteComponent(() =>
    import("@/pages/WhitelabelPage").then((mod) => ({
      default: mod.WhitelabelPage,
    }))
  ),
})

const settingsIntegrationsRoute = createRoute({
  getParentRoute: () => authedRoute,
  path: "/settings/integrations",
  component: lazyRouteComponent(() =>
    import("@/pages/IntegrationsPage").then((mod) => ({
      default: mod.IntegrationsPage,
    }))
  ),
})

const settingsMeRoute = createRoute({
  getParentRoute: () => authedRoute,
  path: "/settings/me",
  component: lazyRouteComponent(() =>
    import("@/pages/MePage").then((mod) => ({ default: mod.MePage }))
  ),
})

const pitchPatternsEmbedRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/embed/pitchpatterns",
  component: lazyRouteComponent(() =>
    import("@/pages/embed/PitchPatternsEmbedPage").then((mod) => ({
      default: mod.PitchPatternsEmbedPage,
    }))
  ),
})

const notFoundRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "$",
  beforeLoad: () => {
    throw redirect({ to: "/" })
  },
})

export const routeTree = rootRoute.addChildren([
  indexRoute,
  loginRoute,
  ssoCallbackRoute,
  registerRoute,
  inviteRoute,
  resetPasswordRoute,
  settingsRedirectRoute,
  agentsRedirectRoute,
  settingsAgentsRedirectRoute,
  pitchPatternsEmbedRoute,
  sharedRoute,
  authedRoute.addChildren([
    chatRoute,
    historyRoute,
    projectsRoute,
    projectRoute,
    usageRoute,
    settingsOrganisationsRoute,
    settingsOrganisationRoute,
    settingsUsersRoute,
    settingsTeamsRoute,
    settingsModelsRoute,
    settingsProvidersRoute,
    settingsWhitelabelRoute,
    settingsIntegrationsRoute,
    settingsDiagnosisRoute,
    settingsMeRoute,
  ]),
  notFoundRoute,
])
