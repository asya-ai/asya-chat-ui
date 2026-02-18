import { useEffect, useMemo, useState } from "react"
import { useLocation, useNavigate } from "react-router-dom"

import { authApi, orgApi, usageApi } from "@/lib/api"
import { orgStore } from "@/lib/storage"
import { useI18n } from "@/lib/i18n-context"
import { LanguageSelect } from "@/components/LanguageSelect"
import { SettingsShell } from "@/components/SettingsShell"
import type { Org, UsageSlice } from "@/lib/types"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"

export const UsagePage = () => {
  const navigate = useNavigate()
  const location = useLocation()
  const [rows, setRows] = useState<UsageSlice[]>([])
  const [groupBy, setGroupBy] = useState<
    "model" | "user" | "org" | "month" | "user_month" | "model_month"
  >("model")
  const [orgs, setOrgs] = useState<Org[]>([])
  const [isSuperAdmin, setIsSuperAdmin] = useState(false)
  const [isAdmin, setIsAdmin] = useState(false)
  const orgId = orgStore.get()
  const [selectedOrgId, setSelectedOrgId] = useState<string | null>(orgId)
  const { t } = useI18n()

  const scopeOptions = useMemo(() => {
    if (!isSuperAdmin) return []
    return [{ id: "all", name: t("usage_all_orgs") }, ...orgs]
  }, [isSuperAdmin, orgs, t])

  useEffect(() => {
    authApi
      .me()
      .then((me) => {
        setIsSuperAdmin(me.is_super_admin)
        setIsAdmin(me.is_admin)
      })
      .catch(() => null)
  }, [])

  useEffect(() => {
    if (!isSuperAdmin) return
    orgApi.list().then(setOrgs).catch(() => null)
  }, [isSuperAdmin])

  useEffect(() => {
    if (!isSuperAdmin && !orgId) {
      navigate("/settings")
      return
    }
    const scopedOrgId = isSuperAdmin
      ? selectedOrgId === "all"
        ? null
        : selectedOrgId
      : orgId
    usageApi.summary(scopedOrgId ?? null, groupBy).then(setRows)
  }, [groupBy, navigate, orgId, isSuperAdmin, selectedOrgId])

  const navItems = [
    { label: t("me_settings"), href: "/settings/me", active: false },
    {
      label: t("org_section_users"),
      href: "/settings/users",
      visible: isAdmin,
      active: location.pathname.startsWith("/settings/users"),
    },
    {
      label: t("org_section_orgs"),
      href: "/settings/organisation",
      visible: isSuperAdmin,
      active: location.pathname.startsWith("/settings/organisation"),
    },
    {
      label: t("org_section_models"),
      href: "/settings/models",
      visible: isSuperAdmin,
      active: location.pathname.startsWith("/settings/models"),
    },
    {
      label: t("usage_title"),
      href: "/usage",
      visible: isAdmin,
      active: location.pathname.startsWith("/usage"),
    },
  ]

  return (
    <SettingsShell
      title={t("usage_title")}
      items={navItems}
      actions={
        <div className="flex items-center gap-2">
          <LanguageSelect />
          <Button variant="outline" onClick={() => navigate("/chat")}>
            {t("common_back_to_chat")}
          </Button>
        </div>
      }
    >
      <div className="flex flex-wrap gap-2">
        <Button
          variant={groupBy === "model" ? "default" : "outline"}
          onClick={() => setGroupBy("model")}
        >
          {t("usage_by_model")}
        </Button>
        <Button
          variant={groupBy === "user" ? "default" : "outline"}
          onClick={() => setGroupBy("user")}
        >
          {t("usage_by_user")}
        </Button>
        <Button
          variant={groupBy === "month" ? "default" : "outline"}
          onClick={() => setGroupBy("month")}
        >
          {t("usage_by_month")}
        </Button>
        <Button
          variant={groupBy === "user_month" ? "default" : "outline"}
          onClick={() => setGroupBy("user_month")}
        >
          {t("usage_by_user_month")}
        </Button>
        <Button
          variant={groupBy === "model_month" ? "default" : "outline"}
          onClick={() => setGroupBy("model_month")}
        >
          {t("usage_by_model_month")}
        </Button>
        {isSuperAdmin ? (
          <Button
            variant={groupBy === "org" ? "default" : "outline"}
            onClick={() => setGroupBy("org")}
          >
            {t("usage_by_org")}
          </Button>
        ) : null}
        {isSuperAdmin ? (
          <Select
            value={selectedOrgId ?? "all"}
            onValueChange={(value) => setSelectedOrgId(value)}
          >
            <SelectTrigger className="w-64">
              <SelectValue placeholder={t("usage_scope_placeholder")} />
            </SelectTrigger>
            <SelectContent>
              {scopeOptions.map((org) => (
                <SelectItem key={org.id} value={org.id}>
                  {org.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : null}
      </div>
      <Card>
        <CardHeader>
          <CardTitle>{t("usage_breakdown")}</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("usage_key")}</TableHead>
                <TableHead>{t("usage_input")}</TableHead>
                <TableHead>{t("usage_output")}</TableHead>
                <TableHead>{t("usage_cached")}</TableHead>
                <TableHead>{t("usage_thinking")}</TableHead>
                <TableHead>{t("usage_total")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={row.key}>
                  <TableCell>{row.key}</TableCell>
                  <TableCell>{row.input_tokens}</TableCell>
                  <TableCell>{row.output_tokens}</TableCell>
                  <TableCell>{row.cached_tokens}</TableCell>
                  <TableCell>{row.thinking_tokens}</TableCell>
                  <TableCell>{row.total_tokens}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </SettingsShell>
  )
}
