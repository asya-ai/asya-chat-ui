import { useEffect, useMemo, useState } from "react"
import { useLocation, useNavigate } from "@tanstack/react-router"

import { authApi, systemDiagnosisApi } from "@/lib/api"
import { useI18n } from "@/lib/i18n-context"
import { SettingsShell } from "@/components/SettingsShell"
import type {
  DependencyCheck,
  DiskUsageInfo,
  EnvKeyDiagnosis,
  McpServerCheck,
  ProviderSnapshot,
  ResourceMetric,
  SystemDiagnosis,
} from "@/lib/types"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"

const CATEGORY_ORDER = [
  "core",
  "database",
  "providers",
  "smtp",
  "files",
  "exec",
  "web",
  "agents",
  "runtime",
] as const

type DiagnosisStatus = EnvKeyDiagnosis["status"]

const statusVariant = (status: DiagnosisStatus): "success" | "destructive" | "warning" => {
  if (status === "ok") return "success"
  if (status === "invalid") return "destructive"
  return "warning"
}

const resourceStatusVariant = (
  status: ResourceMetric["status"]
): "success" | "destructive" | "warning" | "secondary" => {
  if (status === "ok") return "success"
  if (status === "invalid") return "destructive"
  if (status === "warning") return "warning"
  return "secondary"
}

const formatLatency = (value: number | null | undefined) => {
  if (value == null || !Number.isFinite(value)) return "—"
  if (value < 10) return `${value.toFixed(1)} ms`
  return `${Math.round(value)} ms`
}

const formatSeconds = (value: number | null | undefined) => {
  if (value == null || !Number.isFinite(value)) return "—"
  if (value < 1) return `${value.toFixed(2)} s`
  if (value < 10) return `${value.toFixed(1)} s`
  return `${Math.round(value)} s`
}

const formatPercent = (value: number | null | undefined) => {
  if (value == null || !Number.isFinite(value)) return "—"
  return `${value.toFixed(1)}%`
}

const formatBytes = (value: number | null | undefined) => {
  if (value == null || !Number.isFinite(value)) return "—"
  const units = ["B", "KB", "MB", "GB", "TB"]
  let amount = Math.max(0, value)
  let unit = 0
  while (amount >= 1024 && unit < units.length - 1) {
    amount /= 1024
    unit += 1
  }
  const digits = amount >= 100 || unit === 0 ? 0 : amount >= 10 ? 1 : 2
  return `${amount.toFixed(digits)} ${units[unit]}`
}

const diskBarClass = (percent: number | null | undefined) => {
  if (percent == null) return "bg-muted-foreground/40"
  if (percent >= 90) return "bg-destructive"
  if (percent >= 75) return "bg-warning"
  return "bg-success"
}

export const DiagnosisPage = () => {
  const navigate = useNavigate()
  const location = useLocation()
  const { t } = useI18n()
  const [isSuperAdmin, setIsSuperAdmin] = useState(false)
  const [isAdmin, setIsAdmin] = useState(false)
  const [authChecked, setAuthChecked] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [diagnosis, setDiagnosis] = useState<SystemDiagnosis | null>(null)

  const loadDiagnosis = async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await systemDiagnosisApi.get()
      setDiagnosis(result)
    } catch (err) {
      setDiagnosis(null)
      setError(err instanceof Error ? err.message : t("common_error"))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    authApi
      .me()
      .then((me) => {
        setIsSuperAdmin(me.is_super_admin)
        setIsAdmin(me.is_admin)
        setAuthChecked(true)
      })
      .catch(() => {
        setAuthChecked(true)
        navigate({ to: "/settings/me" })
      })
  }, [navigate])

  useEffect(() => {
    if (!authChecked) return
    if (!isSuperAdmin) {
      navigate({ to: "/settings/me" })
      return
    }
    void loadDiagnosis()
  }, [authChecked, isSuperAdmin, navigate])

  const grouped = useMemo(() => {
    const keys = diagnosis?.keys ?? []
    const byCategory = new Map<string, EnvKeyDiagnosis[]>()
    for (const item of keys) {
      const list = byCategory.get(item.category) ?? []
      list.push(item)
      byCategory.set(item.category, list)
    }
    const orderedCategories = [
      ...CATEGORY_ORDER.filter((category) => byCategory.has(category)),
      ...[...byCategory.keys()].filter(
        (category) => !CATEGORY_ORDER.includes(category as (typeof CATEGORY_ORDER)[number])
      ),
    ]
    return orderedCategories.map((category) => ({
      category,
      items: byCategory.get(category) ?? [],
    }))
  }, [diagnosis])

  const statusLabel = (status: DiagnosisStatus) => {
    if (status === "ok") return t("diagnosis_status_ok")
    if (status === "invalid") return t("diagnosis_status_invalid")
    return t("diagnosis_status_missing")
  }

  const healthLabel = (status: DiagnosisStatus) => {
    if (status === "ok") return t("diagnosis_health_ok")
    if (status === "invalid") return t("diagnosis_health_invalid")
    return t("diagnosis_health_missing")
  }

  const categoryLabel = (category: string) => {
    switch (category) {
      case "core":
        return t("diagnosis_category_core")
      case "database":
        return t("diagnosis_category_database")
      case "providers":
        return t("diagnosis_category_providers")
      case "smtp":
        return t("diagnosis_category_smtp")
      case "files":
        return t("diagnosis_category_files")
      case "exec":
        return t("diagnosis_category_exec")
      case "web":
        return t("diagnosis_category_web")
      case "agents":
        return t("diagnosis_category_agents")
      case "runtime":
        return t("diagnosis_category_runtime")
      default:
        return category
    }
  }

  if (!authChecked || !isSuperAdmin) {
    return null
  }

  const navItems = [
    { label: t("me_settings"), href: "/settings/me", active: false },
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
      active: true,
    },
    {
      label: t("usage_title"),
      href: "/usage",
      visible: isAdmin,
      active: location.pathname.startsWith("/usage"),
    },
  ]

  const summary = diagnosis?.summary ?? { ok: 0, invalid: 0, missing: 0 }

  return (
    <SettingsShell
      title={t("diagnosis_title")}
      items={navItems}
      actions={
        <div className="flex items-center gap-2">
          <Button variant="outline" onClick={() => void loadDiagnosis()} disabled={loading}>
            {loading ? t("diagnosis_checking") : t("diagnosis_refresh")}
          </Button>
          <Button variant="outline" onClick={() => navigate({ to: "/chat/{-$chatId}" })}>
            {t("common_back_to_chat")}
          </Button>
        </div>
      }
    >
      <div className="space-y-6">
        {error ? (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}

        <p className="text-sm text-muted-foreground">{t("diagnosis_description")}</p>

        <div className="flex flex-wrap gap-3">
          <Badge variant="success">
            {t("diagnosis_status_ok")}: {summary.ok}
          </Badge>
          <Badge variant="destructive">
            {t("diagnosis_status_invalid")}: {summary.invalid}
          </Badge>
          <Badge variant="warning">
            {t("diagnosis_status_missing")}: {summary.missing}
          </Badge>
        </div>

        {loading && !diagnosis ? (
          <p className="text-sm text-muted-foreground">{t("diagnosis_checking")}</p>
        ) : null}

        {(diagnosis?.disks?.length ?? 0) > 0 ? (
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-lg font-medium">{t("diagnosis_disk_title")}</CardTitle>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("diagnosis_disk_path")}</TableHead>
                    <TableHead>{t("diagnosis_disk_used")}</TableHead>
                    <TableHead>{t("diagnosis_disk_free")}</TableHead>
                    <TableHead>{t("diagnosis_disk_total")}</TableHead>
                    <TableHead className="w-[180px]">{t("diagnosis_disk_usage")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(diagnosis?.disks ?? []).map((disk: DiskUsageInfo) => {
                    const percent = disk.used_percent ?? null
                    return (
                      <TableRow key={`${disk.label}:${disk.path}`}>
                        <TableCell>
                          <div className="font-medium">{disk.label}</div>
                          <div className="font-mono text-xs text-muted-foreground">{disk.path}</div>
                          {disk.error ? (
                            <div className="mt-1 text-xs text-destructive">{disk.error}</div>
                          ) : null}
                        </TableCell>
                        <TableCell>{formatBytes(disk.used_bytes)}</TableCell>
                        <TableCell>{formatBytes(disk.free_bytes)}</TableCell>
                        <TableCell>{formatBytes(disk.total_bytes)}</TableCell>
                        <TableCell>
                          {disk.error ? (
                            "—"
                          ) : (
                            <div className="space-y-1">
                              <div className="text-sm tabular-nums">
                                {percent == null ? "—" : `${percent.toFixed(1)}%`}
                              </div>
                              <div className="h-2 overflow-hidden rounded-full bg-muted">
                                <div
                                  className={`h-full ${diskBarClass(percent)}`}
                                  style={{
                                    width: `${Math.min(100, Math.max(0, percent ?? 0))}%`,
                                  }}
                                />
                              </div>
                            </div>
                          )}
                        </TableCell>
                      </TableRow>
                    )
                  })}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        ) : null}

        {(diagnosis?.dependencies?.length ?? 0) > 0 ? (
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-lg font-medium">{t("diagnosis_deps_title")}</CardTitle>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("diagnosis_deps_name")}</TableHead>
                    <TableHead>{t("diagnosis_status")}</TableHead>
                    <TableHead>{t("diagnosis_latency")}</TableHead>
                    <TableHead>{t("diagnosis_detail")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(diagnosis?.dependencies ?? []).map((item: DependencyCheck) => (
                    <TableRow key={item.name}>
                      <TableCell className="font-medium">{item.name}</TableCell>
                      <TableCell>
                        <Badge variant={statusVariant(item.status)}>{healthLabel(item.status)}</Badge>
                      </TableCell>
                      <TableCell className="tabular-nums">{formatLatency(item.latency_ms)}</TableCell>
                      <TableCell className="max-w-xl text-sm text-muted-foreground">
                        {item.detail || "—"}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        ) : null}

        {(diagnosis?.resources?.length ?? 0) > 0 ? (
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-lg font-medium">{t("diagnosis_resources_title")}</CardTitle>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("diagnosis_resources_name")}</TableHead>
                    <TableHead>{t("diagnosis_resources_value")}</TableHead>
                    <TableHead>{t("diagnosis_status")}</TableHead>
                    <TableHead>{t("diagnosis_detail")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(diagnosis?.resources ?? []).map((item: ResourceMetric) => (
                    <TableRow key={item.name}>
                      <TableCell className="font-medium">{item.name}</TableCell>
                      <TableCell className="tabular-nums">{item.value}</TableCell>
                      <TableCell>
                        {item.status ? (
                          <Badge variant={resourceStatusVariant(item.status)}>
                            {item.status === "warning"
                              ? t("diagnosis_status_warning")
                              : healthLabel(item.status)}
                          </Badge>
                        ) : (
                          "—"
                        )}
                      </TableCell>
                      <TableCell className="max-w-xl text-sm text-muted-foreground">
                        {item.detail || "—"}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        ) : null}

        {diagnosis?.workers ? (
          <Card>
            <CardHeader className="pb-3">
              <div className="flex flex-wrap items-center gap-3">
                <CardTitle className="text-lg font-medium">{t("diagnosis_workers_title")}</CardTitle>
                {diagnosis.workers.status ? (
                  <Badge variant={resourceStatusVariant(diagnosis.workers.status)}>
                    {diagnosis.workers.status === "warning"
                      ? t("diagnosis_status_warning")
                      : healthLabel(diagnosis.workers.status)}
                  </Badge>
                ) : null}
              </div>
              {diagnosis.workers.detail ? (
                <p className="text-sm text-muted-foreground">{diagnosis.workers.detail}</p>
              ) : null}
            </CardHeader>
            <CardContent className="space-y-6">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("diagnosis_resources_name")}</TableHead>
                    <TableHead>{t("diagnosis_resources_value")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  <TableRow>
                    <TableCell className="font-medium">{t("diagnosis_workers_count")}</TableCell>
                    <TableCell className="tabular-nums">{diagnosis.workers.worker_count}</TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell className="font-medium">{t("diagnosis_workers_concurrency")}</TableCell>
                    <TableCell className="tabular-nums">
                      {diagnosis.workers.total_concurrency ?? "—"}
                    </TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell className="font-medium">{t("diagnosis_workers_load")}</TableCell>
                    <TableCell className="tabular-nums">
                      {formatPercent(diagnosis.workers.load_percent)}
                    </TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell className="font-medium">{t("diagnosis_workers_active")}</TableCell>
                    <TableCell className="tabular-nums">{diagnosis.workers.active_tasks}</TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell className="font-medium">{t("diagnosis_workers_reserved")}</TableCell>
                    <TableCell className="tabular-nums">{diagnosis.workers.reserved_tasks}</TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell className="font-medium">{t("diagnosis_workers_queue_depth")}</TableCell>
                    <TableCell className="tabular-nums">{diagnosis.workers.queue_depth}</TableCell>
                  </TableRow>
                </TableBody>
              </Table>

              {(diagnosis.workers.workers?.length ?? 0) > 0 ? (
                <div className="space-y-2">
                  <h3 className="text-sm font-medium">{t("diagnosis_workers_per_worker")}</h3>
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>{t("diagnosis_workers_name")}</TableHead>
                        <TableHead>{t("diagnosis_workers_active")}</TableHead>
                        <TableHead>{t("diagnosis_workers_reserved")}</TableHead>
                        <TableHead>{t("diagnosis_workers_concurrency")}</TableHead>
                        <TableHead>{t("diagnosis_workers_load")}</TableHead>
                        <TableHead>{t("diagnosis_status")}</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {diagnosis.workers.workers.map((worker) => (
                        <TableRow key={worker.name}>
                          <TableCell className="font-medium">{worker.name}</TableCell>
                          <TableCell className="tabular-nums">{worker.active}</TableCell>
                          <TableCell className="tabular-nums">{worker.reserved}</TableCell>
                          <TableCell className="tabular-nums">{worker.concurrency ?? "—"}</TableCell>
                          <TableCell className="tabular-nums">
                            {formatPercent(worker.load_percent)}
                          </TableCell>
                          <TableCell>
                            {worker.status ? (
                              <Badge variant={resourceStatusVariant(worker.status)}>
                                {worker.status === "warning"
                                  ? t("diagnosis_status_warning")
                                  : healthLabel(worker.status)}
                              </Badge>
                            ) : (
                              "—"
                            )}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              ) : null}

              <div className="space-y-2">
                <h3 className="text-sm font-medium">{t("diagnosis_waits_title")}</h3>
                {diagnosis.workers.waits.detail ? (
                  <p className="text-sm text-muted-foreground">{diagnosis.workers.waits.detail}</p>
                ) : null}
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>{t("diagnosis_resources_name")}</TableHead>
                      <TableHead>{t("diagnosis_resources_value")}</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    <TableRow>
                      <TableCell className="font-medium">{t("diagnosis_waits_queued")}</TableCell>
                      <TableCell className="tabular-nums">
                        {diagnosis.workers.waits.queued_now}
                      </TableCell>
                    </TableRow>
                    <TableRow>
                      <TableCell className="font-medium">{t("diagnosis_waits_oldest")}</TableCell>
                      <TableCell className="tabular-nums">
                        {formatSeconds(diagnosis.workers.waits.oldest_queue_wait_seconds)}
                      </TableCell>
                    </TableRow>
                    <TableRow>
                      <TableCell className="font-medium">{t("diagnosis_waits_avg")}</TableCell>
                      <TableCell className="tabular-nums">
                        {formatSeconds(diagnosis.workers.waits.avg_wait_seconds_1h)}
                      </TableCell>
                    </TableRow>
                    <TableRow>
                      <TableCell className="font-medium">{t("diagnosis_waits_p95")}</TableCell>
                      <TableCell className="tabular-nums">
                        {formatSeconds(diagnosis.workers.waits.p95_wait_seconds_1h)}
                      </TableCell>
                    </TableRow>
                    <TableRow>
                      <TableCell className="font-medium">{t("diagnosis_waits_max")}</TableCell>
                      <TableCell className="tabular-nums">
                        {formatSeconds(diagnosis.workers.waits.max_wait_seconds_1h)}
                      </TableCell>
                    </TableRow>
                    <TableRow>
                      <TableCell className="font-medium">{t("diagnosis_waits_samples")}</TableCell>
                      <TableCell className="tabular-nums">
                        {diagnosis.workers.waits.sample_size_1h}
                      </TableCell>
                    </TableRow>
                  </TableBody>
                </Table>
              </div>
            </CardContent>
          </Card>
        ) : null}

        {(diagnosis?.providers?.length ?? 0) > 0 ? (
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-lg font-medium">{t("diagnosis_providers_title")}</CardTitle>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("diagnosis_providers_name")}</TableHead>
                    <TableHead>{t("diagnosis_status")}</TableHead>
                    <TableHead>{t("diagnosis_latency")}</TableHead>
                    <TableHead>{t("diagnosis_detail")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(diagnosis?.providers ?? []).map((item: ProviderSnapshot) => (
                    <TableRow key={item.provider}>
                      <TableCell className="font-medium">{item.provider}</TableCell>
                      <TableCell>
                        <Badge variant={statusVariant(item.status)}>{healthLabel(item.status)}</Badge>
                      </TableCell>
                      <TableCell className="tabular-nums">{formatLatency(item.latency_ms)}</TableCell>
                      <TableCell className="max-w-xl text-sm text-muted-foreground">
                        {item.detail || "—"}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        ) : null}

        {(diagnosis?.mcp_servers?.length ?? 0) > 0 ? (
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-lg font-medium">{t("diagnosis_mcp_title")}</CardTitle>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("diagnosis_mcp_server")}</TableHead>
                    <TableHead>{t("diagnosis_mcp_transport")}</TableHead>
                    <TableHead>{t("diagnosis_status")}</TableHead>
                    <TableHead>{t("diagnosis_latency")}</TableHead>
                    <TableHead>{t("diagnosis_mcp_tools")}</TableHead>
                    <TableHead>{t("diagnosis_detail")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(diagnosis?.mcp_servers ?? []).map((item: McpServerCheck) => (
                    <TableRow key={item.id}>
                      <TableCell className="font-medium">
                        <div>{item.name}</div>
                        <div className="text-xs text-muted-foreground">{item.id}</div>
                      </TableCell>
                      <TableCell className="text-sm text-muted-foreground">
                        {item.transport || "—"}
                      </TableCell>
                      <TableCell>
                        <Badge variant={statusVariant(item.status)}>{healthLabel(item.status)}</Badge>
                      </TableCell>
                      <TableCell className="tabular-nums">{formatLatency(item.latency_ms)}</TableCell>
                      <TableCell className="tabular-nums text-sm">
                        {item.tools == null && item.resources == null && item.prompts == null
                          ? "—"
                          : [
                              item.tools != null ? `${item.tools} tools` : null,
                              item.resources != null ? `${item.resources} res` : null,
                              item.prompts != null ? `${item.prompts} prompts` : null,
                            ]
                              .filter(Boolean)
                              .join(", ")}
                      </TableCell>
                      <TableCell className="max-w-xl text-sm text-muted-foreground">
                        {item.detail || "—"}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        ) : null}

        {(diagnosis?.data_volume?.length ?? 0) > 0 ? (
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-lg font-medium">{t("diagnosis_data_title")}</CardTitle>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("diagnosis_data_name")}</TableHead>
                    <TableHead>{t("diagnosis_data_value")}</TableHead>
                    <TableHead>{t("diagnosis_detail")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(diagnosis?.data_volume ?? []).map((item) => (
                    <TableRow key={item.name}>
                      <TableCell className="font-medium">{item.name}</TableCell>
                      <TableCell className="tabular-nums">{item.value}</TableCell>
                      <TableCell className="max-w-xl text-sm text-muted-foreground">
                        {item.detail || "—"}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        ) : null}

        {grouped.map((group) => (
          <Card key={group.category}>
            <CardHeader className="pb-3">
              <CardTitle className="text-lg font-medium">{categoryLabel(group.category)}</CardTitle>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("diagnosis_env_key")}</TableHead>
                    <TableHead>{t("diagnosis_status")}</TableHead>
                    <TableHead>{t("diagnosis_value")}</TableHead>
                    <TableHead>{t("diagnosis_detail")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {group.items.map((item) => (
                    <TableRow key={item.key}>
                      <TableCell className="font-mono text-sm">
                        {item.key}
                        {item.required ? (
                          <span className="ml-2 text-xs text-muted-foreground">
                            ({t("diagnosis_required")})
                          </span>
                        ) : null}
                      </TableCell>
                      <TableCell>
                        <Badge variant={statusVariant(item.status)}>{statusLabel(item.status)}</Badge>
                      </TableCell>
                      <TableCell className="max-w-md font-mono text-sm break-all">
                        {item.value == null || item.value === "" ? (
                          <span className="text-muted-foreground">—</span>
                        ) : (
                          item.value
                        )}
                      </TableCell>
                      <TableCell className="max-w-xl text-sm text-muted-foreground">
                        {item.detail || "—"}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        ))}
      </div>
    </SettingsShell>
  )
}
