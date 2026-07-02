import { Fragment, useEffect, useMemo, useState, type SetStateAction } from "react"
import { useLocation, useNavigate } from "react-router-dom"

import { authApi, orgApi, usageApi } from "@/lib/api"
import { orgStore } from "@/lib/storage"
import { useI18n } from "@/lib/i18n-context"
import { SettingsShell } from "@/components/SettingsShell"
import type { Org, UsageSlice } from "@/lib/types"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
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
  const orgId = orgStore.get()
  const [selectedOrgId, setSelectedOrgId] = useState<string | null>(orgId)
  const [monthOptions, setMonthOptions] = useState<string[]>([])
  const [selectedMonth, setSelectedMonth] = useState<string>(getCurrentMonth)
  const [expandedModels, setExpandedModels] = useState<Set<string>>(() => new Set())
  const [expandedUsers, setExpandedUsers] = useState<Set<string>>(() => new Set())
  const [expandedOrgs, setExpandedOrgs] = useState<Set<string>>(() => new Set())
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

  const scopedOrgId = isSuperAdmin
    ? selectedOrgId === "all"
      ? null
      : selectedOrgId
    : orgId

  useEffect(() => {
    if (!isSuperAdmin && !orgId) {
      navigate("/settings")
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
    ])
      .then(([modelRows, userRows, orgRows]) => {
        setRowsByModel(modelRows ?? [])
        setRowsByUser(userRows ?? [])
        setRowsByOrg(orgRows ?? [])
      })
      .catch(() => {
        setRowsByModel([])
        setRowsByUser([])
        setRowsByOrg([])
      })
  }, [isSuperAdmin, navigate, orgId, scopedOrgId, selectedMonth])

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
              const isExpandable = Boolean(setExpandedRows && breakdown.length)
              const isExpanded = Boolean(options?.expandedRows?.has(row.key))
              return (
                <Fragment key={row.key}>
                  <TableRow key={row.key}>
                    <TableCell>
                      {isExpandable ? (
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          className="h-auto justify-start gap-2 px-0"
                          onClick={() => {
                            if (setExpandedRows) toggleExpanded(row.key, setExpandedRows)
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
                  {isExpanded
                    ? sortRows(breakdown, sort).map((child, index) => (
                        <TableRow key={`${row.key}-${child.key}-${index}`}>
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
                      ))
                    : null}
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
          <Button variant="outline" onClick={() => navigate("/chat")}>
            {t("common_back_to_chat")}
          </Button>
        </div>
      }
    >
      <div className="flex flex-col gap-6">
        {renderTable(t("usage_block_models"), rowsByModel, sortModel, setSortModel, {
          showCost: true,
          expandedRows: expandedModels,
          setExpandedRows: setExpandedModels,
        })}
        {renderTable(t("usage_block_users"), rowsByUser, sortUser, setSortUser, {
          showCost: true,
          expandedRows: expandedUsers,
          setExpandedRows: setExpandedUsers,
        })}
        {isSuperAdmin ? (
          renderTable(t("usage_block_orgs"), rowsByOrg, sortOrg, setSortOrg, {
            showCost: true,
            expandedRows: expandedOrgs,
            setExpandedRows: setExpandedOrgs,
          })
        ) : null}
      </div>
    </SettingsShell>
  )
}
