import { Fragment, useEffect, useMemo, useState, type SetStateAction } from "react"
import { useLocation, useNavigate } from "@tanstack/react-router"
import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts"

import { authApi, orgApi, usageApi } from "@/lib/api"
import { orgStore } from "@/lib/storage"
import { useI18n } from "@/lib/i18n-context"
import { SettingsShell } from "@/components/SettingsShell"
import type { Org, UsageDailyPoint, UsageSlice } from "@/lib/types"
import { Button } from "@/components/ui/button"
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

export const UsagePage = () => {
  const navigate = useNavigate()
  const location = useLocation()
  const [rowsByModel, setRowsByModel] = useState<UsageSlice[]>([])
  const [rowsByUser, setRowsByUser] = useState<UsageSlice[]>([])
  const [rowsByOrg, setRowsByOrg] = useState<UsageSlice[]>([])
  const [orgs, setOrgs] = useState<Org[]>([])
  const [isSuperAdmin, setIsSuperAdmin] = useState(false)
  const [isAdmin, setIsAdmin] = useState(false)
  const [authChecked, setAuthChecked] = useState(false)
  const orgId = orgStore.get()
  const [selectedOrgId, setSelectedOrgId] = useState<string | null>("all")
  const [monthOptions, setMonthOptions] = useState<string[]>([])
  const [selectedMonth, setSelectedMonth] = useState<string>(getCurrentMonth)
  const [expandedModels, setExpandedModels] = useState<Set<string>>(() => new Set())
  const [expandedUsers, setExpandedUsers] = useState<Set<string>>(() => new Set())
  const [expandedOrgs, setExpandedOrgs] = useState<Set<string>>(() => new Set())
  const [dailyPoints, setDailyPoints] = useState<UsageDailyPoint[]>([])
  const [rowDaily, setRowDaily] = useState<Record<string, UsageDailyPoint[]>>({})
  const [rowDailyLoading, setRowDailyLoading] = useState<Set<string>>(() => new Set())
  const { t, locale } = useI18n()

  type SortKey =
    | "key"
    | "input_tokens"
    | "output_tokens"
    | "cached_tokens"
    | "thinking_tokens"
    | "total_tokens"
    | "cost_usd"

  type SortState = { key: SortKey; dir: "asc" | "desc" }

  const [sortModel, setSortModel] = useState<SortState>({
    key: "total_tokens",
    dir: "desc",
  })
  const [sortUser, setSortUser] = useState<SortState>({
    key: "total_tokens",
    dir: "desc",
  })
  const [sortOrg, setSortOrg] = useState<SortState>({
    key: "total_tokens",
    dir: "desc",
  })

  const scopeOptions = useMemo(() => {
    if (!isSuperAdmin) return []
    return [{ id: "all", name: t("usage_entire_instance") }, ...orgs]
  }, [isSuperAdmin, orgs, t])

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
    if (!isSuperAdmin) return
    orgApi.list().then(setOrgs).catch(() => null)
  }, [isSuperAdmin])

  const scopedOrgId = isSuperAdmin
    ? selectedOrgId === "all"
      ? null
      : selectedOrgId
    : orgId

  useEffect(() => {
    if (!authChecked) return
    if (!isSuperAdmin && !orgId) {
      navigate({ to: "/settings" })
      return
    }
    const month = selectedMonth === "all" ? undefined : selectedMonth
    usageApi
      .months(scopedOrgId ?? null)
      .then((months) => {
        setMonthOptions(
          selectedMonth === "all" || months.includes(selectedMonth)
            ? months
            : [selectedMonth, ...months]
        )
      })
      .catch(() => setMonthOptions([]))
    Promise.all([
      usageApi.summary(scopedOrgId ?? null, "model", month),
      usageApi.summary(scopedOrgId ?? null, "user", month),
      isSuperAdmin ? usageApi.summary(null, "org", month) : Promise.resolve([]),
      usageApi.daily(scopedOrgId ?? null, month),
    ])
      .then(([modelRows, userRows, orgRows, dailyRows]) => {
        setRowsByModel(modelRows ?? [])
        setRowsByUser(userRows ?? [])
        setRowsByOrg(orgRows ?? [])
        setDailyPoints(dailyRows ?? [])
        setRowDaily({})
        setRowDailyLoading(new Set())
      })
      .catch(() => {
        setRowsByModel([])
        setRowsByUser([])
        setRowsByOrg([])
        setDailyPoints([])
        setRowDaily({})
        setRowDailyLoading(new Set())
      })
  }, [authChecked, isSuperAdmin, navigate, orgId, scopedOrgId, selectedMonth])

  const sortRows = (rows: UsageSlice[], sort: SortState) => {
    const sorted = [...rows]
    sorted.sort((a, b) => {
      if (sort.key === "key") {
        const result = a.key.localeCompare(b.key)
        return sort.dir === "asc" ? result : -result
      }
      const rawAValue = a[sort.key]
      const rawBValue = b[sort.key]
      const aValue = typeof rawAValue === "number" ? rawAValue : -1
      const bValue = typeof rawBValue === "number" ? rawBValue : -1
      const result = aValue - bValue
      return sort.dir === "asc" ? result : -result
    })
    return sorted
  }

  const nextSortState = (current: SortState, key: SortKey): SortState => {
    if (current.key === key) {
      return { key, dir: current.dir === "asc" ? "desc" : "asc" }
    }
    return { key, dir: key === "key" ? "asc" : "desc" }
  }

  const renderSortableHead = (
    label: string,
    sort: SortState,
    onSort: (next: SortState) => void,
    key: SortKey
  ) => {
    const isActive = sort.key === key
    const indicator = isActive ? (sort.dir === "asc" ? "▲" : "▼") : ""
    return (
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="flex items-center gap-1 h-auto px-0"
        onClick={() => onSort(nextSortState(sort, key))}
      >
        <span>{label}</span>
        <span className="text-xs text-muted-foreground">{indicator}</span>
      </Button>
    )
  }

  const formatTokens = (value: number) =>
    new Intl.NumberFormat(locale ?? "en", { maximumFractionDigits: 0 }).format(value)

  const formatCost = (value: number | null | undefined) => {
    if (value === null || value === undefined) return "—"
    return new Intl.NumberFormat(locale ?? "en", {
      style: "currency",
      currency: "USD",
      minimumFractionDigits: value > 0 && value < 0.01 ? 4 : 2,
      maximumFractionDigits: value > 0 && value < 0.01 ? 4 : 2,
    }).format(value)
  }

  const formatCompact = (value: number) =>
    new Intl.NumberFormat(locale ?? "en", {
      notation: "compact",
      maximumFractionDigits: 1,
    }).format(value)

  const formatDay = (value: string) => {
    const parsed = new Date(`${value}T00:00:00Z`)
    if (Number.isNaN(parsed.getTime())) return value
    return parsed.toLocaleDateString(locale ?? "en", {
      month: "short",
      day: "numeric",
      timeZone: "UTC",
    })
  }

  const tokenChartConfig = {
    input_tokens: { label: t("usage_input"), color: "var(--chart-1)" },
    output_tokens: { label: t("usage_output"), color: "var(--chart-2)" },
    cached_tokens: { label: t("usage_cached"), color: "var(--chart-3)" },
    thinking_tokens: { label: t("usage_thinking"), color: "var(--chart-4)" },
  } satisfies ChartConfig

  const costChartConfig = {
    cost_usd: { label: t("usage_cost"), color: "var(--chart-5)" },
  } satisfies ChartConfig

  const monthParam = selectedMonth === "all" ? undefined : selectedMonth

  const rowDailyCacheKey = (kind: "model" | "user" | "org", row: UsageSlice) => {
    const orgId = kind === "org" ? row.id : scopedOrgId
    return `${kind}:${row.id ?? row.key}:${orgId ?? "all"}:${monthParam ?? "all"}`
  }

  const ensureRowDaily = (kind: "model" | "user" | "org", row: UsageSlice) => {
    if (!row.id) return
    const cacheKey = rowDailyCacheKey(kind, row)
    if (cacheKey in rowDaily || rowDailyLoading.has(cacheKey)) return
    const orgId = kind === "org" ? row.id : scopedOrgId ?? null
    setRowDailyLoading((current) => {
      const next = new Set(current)
      next.add(cacheKey)
      return next
    })
    usageApi
      .daily(orgId, monthParam, {
        userId: kind === "user" ? row.id : undefined,
        modelId: kind === "model" ? row.id : undefined,
      })
      .then((points) => {
        setRowDaily((current) => ({ ...current, [cacheKey]: points ?? [] }))
      })
      .catch(() => {
        setRowDaily((current) => ({ ...current, [cacheKey]: [] }))
      })
      .finally(() => {
        setRowDailyLoading((current) => {
          const next = new Set(current)
          next.delete(cacheKey)
          return next
        })
      })
  }

  const renderDailyCharts = (points: UsageDailyPoint[] | undefined, compact = false) => {
    if (points === undefined) {
      return <Skeleton className={compact ? "h-56 w-full" : "h-70 w-full"} />
    }
    if (points.length === 0) {
      return <p className="text-muted-foreground">{t("usage_no_daily_data")}</p>
    }
    const chartClassName = compact ? "aspect-auto h-56 w-full" : "aspect-auto h-70 w-full"
    return (
      <div className={compact ? "grid gap-4 xl:grid-cols-2" : "grid gap-6 xl:grid-cols-2"}>
        <div className="flex flex-col gap-2">
          <div className="text-muted-foreground">{t("usage_chart_tokens")}</div>
          <ChartContainer config={tokenChartConfig} className={chartClassName}>
            <BarChart accessibilityLayer data={points}>
              <CartesianGrid vertical={false} />
              <XAxis
                dataKey="date"
                tickLine={false}
                axisLine={false}
                tickMargin={8}
                minTickGap={24}
                tickFormatter={formatDay}
              />
              <YAxis
                tickLine={false}
                axisLine={false}
                width={48}
                tickFormatter={formatCompact}
              />
              <ChartTooltip
                content={
                  <ChartTooltipContent
                    labelFormatter={(_, payload) => {
                      const day = payload?.[0]?.payload?.date
                      return typeof day === "string" ? formatDay(day) : ""
                    }}
                  />
                }
              />
              <ChartLegend content={<ChartLegendContent />} />
              <Bar dataKey="input_tokens" stackId="tokens" fill="var(--color-input_tokens)" />
              <Bar dataKey="output_tokens" stackId="tokens" fill="var(--color-output_tokens)" />
              <Bar dataKey="cached_tokens" stackId="tokens" fill="var(--color-cached_tokens)" />
              <Bar
                dataKey="thinking_tokens"
                stackId="tokens"
                fill="var(--color-thinking_tokens)"
                radius={[4, 4, 0, 0]}
              />
            </BarChart>
          </ChartContainer>
        </div>
        <div className="flex flex-col gap-2">
          <div className="text-muted-foreground">{t("usage_chart_cost")}</div>
          <ChartContainer config={costChartConfig} className={chartClassName}>
            <BarChart accessibilityLayer data={points}>
              <CartesianGrid vertical={false} />
              <XAxis
                dataKey="date"
                tickLine={false}
                axisLine={false}
                tickMargin={8}
                minTickGap={24}
                tickFormatter={formatDay}
              />
              <YAxis
                tickLine={false}
                axisLine={false}
                width={48}
                tickFormatter={(value) => formatCost(Number(value))}
              />
              <ChartTooltip
                content={
                  <ChartTooltipContent
                    labelFormatter={(_, payload) => {
                      const day = payload?.[0]?.payload?.date
                      return typeof day === "string" ? formatDay(day) : ""
                    }}
                    formatter={(value) => (
                      <div className="flex flex-1 items-center justify-between gap-8">
                        <span className="text-muted-foreground">{t("usage_cost")}</span>
                        <span className="font-mono font-medium text-foreground tabular-nums">
                          {typeof value === "number" ? formatCost(value) : "—"}
                        </span>
                      </div>
                    )}
                  />
                }
              />
              <Bar dataKey="cost_usd" fill="var(--color-cost_usd)" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ChartContainer>
        </div>
      </div>
    )
  }

  const toggleExpanded = (
    key: string,
    setExpandedRows: (value: SetStateAction<Set<string>>) => void
  ) => {
    setExpandedRows((current) => {
      const next = new Set(current)
      if (next.has(key)) {
        next.delete(key)
      } else {
        next.add(key)
      }
      return next
    })
  }

  const renderTable = (
    title: string,
    rows: UsageSlice[],
    sort: SortState,
    onSort: (next: SortState) => void,
    options?: {
      showCost?: boolean
      expandedRows?: Set<string>
      setExpandedRows?: (value: SetStateAction<Set<string>>) => void
      dailyKind?: "model" | "user" | "org"
    }
  ) => (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{renderSortableHead(t("usage_key"), sort, onSort, "key")}</TableHead>
              <TableHead>
                {renderSortableHead(t("usage_input"), sort, onSort, "input_tokens")}
              </TableHead>
              <TableHead>
                {renderSortableHead(t("usage_output"), sort, onSort, "output_tokens")}
              </TableHead>
              <TableHead>
                {renderSortableHead(t("usage_cached"), sort, onSort, "cached_tokens")}
              </TableHead>
              <TableHead>
                {renderSortableHead(t("usage_thinking"), sort, onSort, "thinking_tokens")}
              </TableHead>
              <TableHead>
                {renderSortableHead(t("usage_total"), sort, onSort, "total_tokens")}
              </TableHead>
              {options?.showCost ? (
                <TableHead>
                  {renderSortableHead(t("usage_cost"), sort, onSort, "cost_usd")}
                </TableHead>
              ) : null}
            </TableRow>
          </TableHeader>
          <TableBody>
            {sortRows(rows, sort).map((row) => {
              const breakdown = row.breakdown ?? []
              const setExpandedRows = options?.setExpandedRows
              const rowIdentity = row.id ?? row.key
              const canShowDaily = Boolean(options?.dailyKind && row.id)
              const isExpandable = Boolean(setExpandedRows && (breakdown.length || canShowDaily))
              const isExpanded = Boolean(options?.expandedRows?.has(rowIdentity))
              const dailyCacheKey = options?.dailyKind
                ? rowDailyCacheKey(options.dailyKind, row)
                : ""
              const colSpan = options?.showCost ? 7 : 6
              return (
                <Fragment key={rowIdentity}>
                  <TableRow>
                    <TableCell>
                      {isExpandable ? (
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          className="h-auto justify-start gap-2 px-0"
                          onClick={() => {
                            if (!setExpandedRows) return
                            if (!isExpanded && options?.dailyKind) {
                              ensureRowDaily(options.dailyKind, row)
                            }
                            toggleExpanded(rowIdentity, setExpandedRows)
                          }}
                        >
                          <span className="text-muted-foreground">
                            {isExpanded ? "▾" : "▸"}
                          </span>
                          <span>{row.key}</span>
                        </Button>
                      ) : (
                        row.key
                      )}
                    </TableCell>
                    <TableCell>{formatTokens(row.input_tokens)}</TableCell>
                    <TableCell>{formatTokens(row.output_tokens)}</TableCell>
                    <TableCell>{formatTokens(row.cached_tokens)}</TableCell>
                    <TableCell>{formatTokens(row.thinking_tokens)}</TableCell>
                    <TableCell>{formatTokens(row.total_tokens)}</TableCell>
                    {options?.showCost ? <TableCell>{formatCost(row.cost_usd)}</TableCell> : null}
                  </TableRow>
                  {isExpanded ? (
                    <>
                      {canShowDaily ? (
                        <TableRow>
                          <TableCell colSpan={colSpan}>
                            {renderDailyCharts(rowDaily[dailyCacheKey], true)}
                          </TableCell>
                        </TableRow>
                      ) : null}
                      {sortRows(breakdown, sort).map((child, index) => (
                        <TableRow key={`${rowIdentity}-${child.key}-${index}`}>
                          <TableCell className="pl-8 text-muted-foreground">
                            {child.key}
                          </TableCell>
                          <TableCell>{formatTokens(child.input_tokens)}</TableCell>
                          <TableCell>{formatTokens(child.output_tokens)}</TableCell>
                          <TableCell>{formatTokens(child.cached_tokens)}</TableCell>
                          <TableCell>{formatTokens(child.thinking_tokens)}</TableCell>
                          <TableCell>{formatTokens(child.total_tokens)}</TableCell>
                          {options?.showCost ? (
                            <TableCell>{formatCost(child.cost_usd)}</TableCell>
                          ) : null}
                        </TableRow>
                      ))}
                    </>
                  ) : null}
                </Fragment>
              )
            })}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  )

  const navItems = [
    { label: t("me_settings"), href: "/settings/me", active: location.pathname.startsWith("/settings/me") },
    {
      label: t("org_section_users"),
      href: "/settings/users",
      visible: isAdmin,
      active: location.pathname.startsWith("/settings/users"),
    },
    {
      label: t("org_section_teams"),
      href: "/settings/teams",
      visible: isAdmin,
      active: location.pathname.startsWith("/settings/teams"),
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
      label: t("diagnosis_title"),
      href: "/settings/diagnosis",
      visible: isSuperAdmin,
      active: location.pathname.startsWith("/settings/diagnosis"),
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
        <div className="flex flex-wrap items-center justify-end gap-2">
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
          <Select value={selectedMonth} onValueChange={(value) => setSelectedMonth(value)}>
            <SelectTrigger className="w-40">
              <SelectValue placeholder={t("usage_filter_month")} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t("usage_all_months")}</SelectItem>
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
          <Button variant="outline" onClick={() => navigate({ to: "/chat/{-$chatId}" })}>
            {t("common_back_to_chat")}
          </Button>
        </div>
      }
    >
      <div className="flex flex-col gap-6">
        <Card>
          <CardHeader>
            <CardTitle>
              {scopedOrgId
                ? orgs.find((org) => org.id === scopedOrgId)?.name ?? t("usage_entire_org")
                : t("usage_entire_instance")}
            </CardTitle>
          </CardHeader>
          <CardContent>{renderDailyCharts(dailyPoints)}</CardContent>
        </Card>
        {renderTable(t("usage_block_models"), rowsByModel, sortModel, setSortModel, {
          showCost: true,
          expandedRows: expandedModels,
          setExpandedRows: setExpandedModels,
          dailyKind: "model",
        })}
        {renderTable(t("usage_block_users"), rowsByUser, sortUser, setSortUser, {
          showCost: true,
          expandedRows: expandedUsers,
          setExpandedRows: setExpandedUsers,
          dailyKind: "user",
        })}
        {isSuperAdmin ? (
          renderTable(t("usage_block_orgs"), rowsByOrg, sortOrg, setSortOrg, {
            showCost: true,
            expandedRows: expandedOrgs,
            setExpandedRows: setExpandedOrgs,
            dailyKind: "org",
          })
        ) : null}
      </div>
    </SettingsShell>
  )
}
