import { useEffect, useMemo, useState } from "react"
import { useLocation } from "@tanstack/react-router"

import { authApi } from "@/lib/api"
import { useI18n } from "@/lib/i18n-context"

export type SettingsNavItem = {
  label: string
  href: string
  visible?: boolean
  active?: boolean
}

export type SettingsNavGroup = {
  label: string
  items: SettingsNavItem[]
}

const isPathActive = (pathname: string, href: string) => {
  if (href === "/settings/me") return pathname.startsWith("/settings/me")
  if (href === "/usage") return pathname.startsWith("/usage")
  return pathname.startsWith(href)
}

export const useSettingsAuth = () => {
  const [isAdmin, setIsAdmin] = useState(false)
  const [isSuperAdmin, setIsSuperAdmin] = useState(false)
  const [authChecked, setAuthChecked] = useState(false)

  useEffect(() => {
    authApi
      .me()
      .then((me) => {
        setIsAdmin(me.is_admin)
        setIsSuperAdmin(me.is_super_admin)
        setAuthChecked(true)
      })
      .catch(() => {
        setAuthChecked(true)
      })
  }, [])

  return { isAdmin, isSuperAdmin, authChecked }
}

export const useSettingsNav = (options: { isAdmin: boolean; isSuperAdmin: boolean }) => {
  const location = useLocation()
  const { t } = useI18n()
  const pathname = location.pathname

  return useMemo(() => {
    const item = (label: string, href: string, visible = true): SettingsNavItem => ({
      label,
      href,
      visible,
      active: isPathActive(pathname, href),
    })

    const groups: SettingsNavGroup[] = [
      {
        label: t("settings_nav_personal"),
        items: [item(t("me_settings"), "/settings/me")],
      },
      {
        label: t("settings_nav_organization"),
        items: [
          item(t("org_section_users"), "/settings/users", options.isAdmin || options.isSuperAdmin),
          item(t("org_section_teams"), "/settings/teams", options.isAdmin || options.isSuperAdmin),
          item(t("org_section_orgs"), "/settings/organisation", options.isSuperAdmin),
          item(t("org_section_models"), "/settings/models", options.isSuperAdmin),
          item(t("usage_title"), "/usage", options.isAdmin || options.isSuperAdmin),
        ],
      },
      {
        label: t("settings_nav_system"),
        items: [
          item(t("instance_providers_title"), "/settings/providers", options.isSuperAdmin),
          item(t("diagnosis_title"), "/settings/diagnosis", options.isSuperAdmin),
        ],
      },
    ]

    return groups
      .map((group) => ({
        ...group,
        items: group.items.filter((entry) => entry.visible !== false),
      }))
      .filter((group) => group.items.length > 0)
  }, [options.isAdmin, options.isSuperAdmin, pathname, t])
}
