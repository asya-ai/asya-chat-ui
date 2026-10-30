import { useEffect, useMemo, useState } from "react"
import { useNavigate } from "@tanstack/react-router"
import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts"

import { analyticsApi, authApi, orgApi, usageApi } from "@/lib/api"
import { orgStore } from "@/lib/storage"
import { useI18n } from "@/lib/i18n-context"
import { SettingsShell } from "@/components/SettingsShell"
import { SettingsPage } from "@/components/settings/SettingsPanel"
import type {
  AnalyticsDailyPoint,
  AnalyticsFeatureRow,
  AnalyticsOrgRow,
  AnalyticsOverview,
  AnalyticsUserRow,
  Org,
} from "@/lib/types"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"

const getCurrentMonth = () => {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`
}

type UserFilter = "all" | "active" | "dormant"

export const AnalyticsPage = () => {
  const navigate = useNavigate()
  const { t, locale } = useI18n()
  const [authChecked, setAuthChecked] = useState(false)
  const [isSuperAdmin, setIsSuperAdmin] = useState(false)
  const [isAdmin, setIsAdmin] = useState(false)
  const orgId = orgStore.get()
  const [orgs, setOrgs] = useState<Org[]>([])
  const [selectedOrgId, setSelectedOrgId] = useState<string | null>("all")
  const [monthOptions, setMonthOptions] = useState<string[]>([])
  const [selectedMonth, setSelectedMonth] = useState<string>(getCurrentMonth)
  const [loading, setLoading] = useState(false)
  const [overview, setOverview] = useState<AnalyticsOverview | null>(null)
  const [daily, setDaily] = useState<AnalyticsDailyPoint[]>([])
  const [users, setUsers] = useState<AnalyticsUserRow[]>([])
  const [features, setFeatures] = useState<AnalyticsFeatureRow[]>([])
  const [byOrg, setByOrg] = useState<AnalyticsOrgRow[]>([])
  const [userFilter, setUserFilter] = useState<UserFilter>("all")

  const scopeOptions = useMemo(() => {
    if (!isSuperAdmin) return []
    return [{ id: "all", name: t("usage_entire_instance") }, ...orgs]
  }, [isSuperAdmin, orgs, t])

  const scopedOrgId = isSuperAdmin
    ? selectedOrgId === "all"
      ? null
      : selectedOrgId
    : orgId

  useEffect(() => {
    authApi
      .me()
      .then((me) => {
        setIsSuperAdmin(me.is_super_admin)
        setIsAdmin(me.is_admin)
      })
      .catch(() => null)
      .finally(() => setAuthChecked(true))
  }, [])

  useEffect(() => {
    if (!authChecked) return
    if (!isSuperAdmin && !isAdmin) {
      navigate({ to: "/settings/me" })
    }
  }, [authChecked, isAdmin, isSuperAdmin, navigate])

  useEffect(() => {
    if (!isSuperAdmin) return
    orgApi.list().then(setOrgs).catch(() => setOrgs([]))
  }, [isSuperAdmin])

  useEffect(() => {
    if (!authChecked) return
    if (!isSuperAdmin && !orgId) {
      navigate({ to: "/settings" })
      return
    }
    setLoading(true)
    usageApi
      .months(scopedOrgId ?? null)
      .then((months) => {
        setMonthOptions(
          months.includes(selectedMonth) ? months : [selectedMonth, ...months]
        )
      })
      .catch(() => setMonthOptions([selectedMonth]))

    Promise.all([
      analyticsApi.overview(scopedOrgId ?? null, selectedMonth),
      analyticsApi.daily(scopedOrgId ?? null, selectedMonth),
      analyticsApi.users(scopedOrgId ?? null, selectedMonth),
      analyticsApi.features(scopedOrgId ?? null, selectedMonth),
    ])
      .then(([overviewRow, dailyRows, userRows, featurePayload]) => {
        setOverview(overviewRow)
        setDaily(dailyRows ?? [])
        setUsers(userRows ?? [])
        setFeatures(featurePayload?.features ?? [])
        setByOrg(featurePayload?.by_org ?? [])
      })
      .catch(() => {
        setOverview(null)
        setDaily([])
        setUsers([])
        setFeatures([])
        setByOrg([])
      })
      .finally(() => setLoading(false))
  }, [authChecked, isSuperAdmin, navigate, orgId, scopedOrgId, selectedMonth])

  const filteredUsers = useMemo(() => {
    if (userFilter === "active") return users.filter((row) => !row.dormant)
    if (userFilter === "dormant") return users.filter((row) => row.dormant)
    return users
  }, [userFilter, users])

  const chartConfig = {
    active_users: { label: t("analytics_active_users"), color: "var(--chart-1)" },
    chats_created: { label: t("analytics_chats"), color: "var(--chart-2)" },
  } satisfies ChartConfig

  const formatDay = (value: string) => {
    const date = new Date(`${value}T00:00:00Z`)
    return date.toLocaleDateString(locale ?? "en", { month: "short", day: "numeric" })
  }

  const formatLastActive = (value?: string | null) => {
    if (!value) return "—"
    const date = new Date(value)
    if (Number.isNaN(date.getTime())) return "—"
    return date.toLocaleString(locale ?? "en", {
      month: "short",
      day: "numeric",
      year: "numeric",
    })
  }

  const kpi = (
    label: string,
    value: string | number | null | undefined
  ) => (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-muted-foreground text-sm font-medium">{label}</CardTitle>
      </CardHeader>
      <CardContent>
        {loading && overview == null ? (
          <Skeleton className="h-8 w-16" />
        ) : (
          <p className="text-2xl font-semibold tabular-nums">{value ?? "—"}</p>
        )}
      </CardContent>
    </Card>
  )

  return (
    <SettingsShell
      title={t("analytics_title")}
      actions={
        <>
          {isSuperAdmin ? (
            <Select
              value={selectedOrgId ?? "all"}
              onValueChange={(value) => setSelectedOrgId(value)}
            >
              <SelectTrigger className="w-56">
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
          <Select value={selectedMonth} onValueChange={setSelectedMonth}>
            <SelectTrigger className="w-40">
              <SelectValue placeholder={t("usage_filter_month")} />
            </SelectTrigger>
            <SelectContent>
              {monthOptions.map((month) => {
                const [year, monthPart] = month.split("-")
                const monthIndex = Number(monthPart) - 1
                const dateLabel = new Date(
                  Number(year),
                  Number.isFinite(monthIndex) ? monthIndex : 0,
                  1
                ).toLocaleString(locale ?? "en", { month: "long", year: "numeric" })
                return (
                  <SelectItem key={month} value={month}>
                    {dateLabel}
                  </SelectItem>
                )
              })}
            </SelectContent>
          </Select>
        </>
      }
    >
      <SettingsPage wide>
        <div className="flex flex-col gap-6">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            {kpi(t("analytics_kpi_active"), overview?.active_users)}
            {kpi(t("analytics_kpi_dau_avg"), overview?.dau_avg)}
            {kpi(t("analytics_kpi_chats"), overview?.chats_created)}
            {kpi(
              t("analytics_kpi_messages"),
              overview
                ? overview.user_messages + overview.assistant_messages
                : null
            )}
            {kpi(t("analytics_kpi_dormant"), overview?.dormant_users)}
          </div>

          <Card>
            <CardHeader>
              <CardTitle>{t("analytics_engagement")}</CardTitle>
            </CardHeader>
            <CardContent>
              {loading && daily.length === 0 ? (
                <Skeleton className="h-56 w-full" />
              ) : daily.length === 0 ? (
                <p className="text-muted-foreground text-sm">{t("analytics_empty")}</p>
              ) : (
                <ChartContainer config={chartConfig} className="h-56 w-full">
                  <BarChart data={daily} accessibilityLayer>
                    <CartesianGrid vertical={false} />
                    <XAxis
                      dataKey="date"
                      tickLine={false}
                      axisLine={false}
                      tickFormatter={formatDay}
                      minTickGap={24}
                    />
                    <YAxis allowDecimals={false} width={36} />
                    <ChartTooltip content={<ChartTooltipContent />} />
                    <ChartLegend content={<ChartLegendContent />} />
                    <Bar
                      dataKey="active_users"
                      fill="var(--color-active_users)"
                      radius={2}
                    />
                    <Bar
                      dataKey="chats_created"
                      fill="var(--color-chats_created)"
                      radius={2}
                    />
                  </BarChart>
                </ChartContainer>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between gap-3 space-y-0">
              <CardTitle>{t("analytics_users")}</CardTitle>
              <Select
                value={userFilter}
                onValueChange={(value) => setUserFilter(value as UserFilter)}
              >
                <SelectTrigger className="w-36">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">{t("analytics_users_all")}</SelectItem>
                  <SelectItem value="active">{t("analytics_users_active")}</SelectItem>
                  <SelectItem value="dormant">{t("analytics_users_dormant")}</SelectItem>
                </SelectContent>
              </Select>
            </CardHeader>
            <CardContent>
              {loading && users.length === 0 ? (
                <Skeleton className="h-40 w-full" />
              ) : filteredUsers.length === 0 ? (
                <p className="text-muted-foreground text-sm">{t("analytics_empty")}</p>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>{t("analytics_user")}</TableHead>
                      <TableHead>{t("analytics_last_active")}</TableHead>
                      <TableHead>{t("analytics_chats")}</TableHead>
                      <TableHead>{t("analytics_messages")}</TableHead>
                      <TableHead>{t("analytics_status")}</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {filteredUsers.map((row) => (
                      <TableRow key={row.user_id}>
                        <TableCell>{row.name}</TableCell>
                        <TableCell>{formatLastActive(row.last_active_at)}</TableCell>
                        <TableCell className="tabular-nums">{row.chats}</TableCell>
                        <TableCell className="tabular-nums">{row.messages}</TableCell>
                        <TableCell>
                          {row.dormant
                            ? t("analytics_status_dormant")
                            : t("analytics_status_active")}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>{t("analytics_features")}</CardTitle>
            </CardHeader>
            <CardContent>
              {loading && features.length === 0 ? (
                <Skeleton className="h-40 w-full" />
              ) : features.length === 0 ? (
                <p className="text-muted-foreground text-sm">{t("analytics_empty")}</p>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>{t("analytics_feature")}</TableHead>
                      <TableHead>{t("analytics_unique_users")}</TableHead>
                      <TableHead>{t("analytics_events")}</TableHead>
                      <TableHead />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {features.map((row) => (
                      <TableRow key={row.feature}>
                        <TableCell>{row.label}</TableCell>
                        <TableCell className="tabular-nums">{row.unique_users}</TableCell>
                        <TableCell className="tabular-nums">{row.event_count}</TableCell>
                        <TableCell className="text-muted-foreground text-xs">
                          {row.forward_looking ? t("analytics_forward_looking") : null}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>

          {isSuperAdmin && scopedOrgId == null && byOrg.length > 0 ? (
            <Card>
              <CardHeader>
                <CardTitle>{t("analytics_by_org")}</CardTitle>
              </CardHeader>
              <CardContent>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>{t("analytics_org")}</TableHead>
                      <TableHead>{t("analytics_active_users")}</TableHead>
                      <TableHead>{t("analytics_chats")}</TableHead>
                      <TableHead>{t("analytics_top_feature")}</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {byOrg.map((row) => (
                      <TableRow key={row.org_id}>
                        <TableCell>{row.name}</TableCell>
                        <TableCell className="tabular-nums">{row.active_users}</TableCell>
                        <TableCell className="tabular-nums">{row.chats_created}</TableCell>
                        <TableCell>{row.top_feature ?? "—"}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          ) : null}
        </div>
      </SettingsPage>
    </SettingsShell>
  )
}
