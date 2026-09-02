import { useEffect, useState } from "react"
import { TriangleAlert, X } from "lucide-react"

import { useUsageLimits } from "@/hooks/use-chat-query"
import { useI18n } from "@/lib/i18n-context"
import { usageLimitWarningDismissStore } from "@/lib/storage"
import type { UsageLimitInfo } from "@/lib/types"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"

const formatCost = (value: number, locale: string | undefined) =>
  new Intl.NumberFormat(locale ?? "en", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: value > 0 && value < 0.01 ? 4 : 2,
    maximumFractionDigits: value > 0 && value < 0.01 ? 4 : 2,
  }).format(value)

type UsageLimitBannerProps = {
  scope: "user" | "org"
  orgId: string
  month: string
  message: string
  onDismiss: () => void
}

const UsageLimitBanner = ({
  scope,
  orgId,
  month,
  message,
  onDismiss,
}: UsageLimitBannerProps) => {
  const { t } = useI18n()

  return (
    <Alert variant="warning" className="rounded-none border-x-0 border-t-0">
      <TriangleAlert aria-hidden="true" />
      <AlertTitle>
        {scope === "user" ? t("usage_limit_warning_user_title") : t("usage_limit_warning_org_title")}
      </AlertTitle>
      <AlertDescription>{message}</AlertDescription>
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        className="absolute top-2 right-2"
        aria-label={t("common_close")}
        onClick={() => {
          usageLimitWarningDismissStore.dismiss(scope, orgId, month)
          onDismiss()
        }}
      >
        <X aria-hidden="true" />
      </Button>
    </Alert>
  )
}

export const UsageLimitBanners = ({ orgId }: { orgId: string | null }) => {
  const { t, locale } = useI18n()
  const { data } = useUsageLimits(orgId)
  const [dismissed, setDismissed] = useState({ user: false, org: false })

  useEffect(() => {
    if (!orgId || !data) return
    setDismissed({
      user: usageLimitWarningDismissStore.isDismissed("user", orgId, data.month),
      org: usageLimitWarningDismissStore.isDismissed("org", orgId, data.month),
    })
  }, [orgId, data?.month])

  if (!orgId || !data) return null

  const formatLimitMessage = (info: UsageLimitInfo) => {
    const used = formatCost(info.used_usd, locale)
    const limit =
      info.limit_usd == null ? "—" : formatCost(info.limit_usd, locale)
    const percent =
      info.percent_used == null ? "—" : String(Math.round(info.percent_used))
    return { used, limit, percent }
  }

  const banners: UsageLimitBannerProps[] = []

  if (data.user.near_limit && data.user.limit_usd != null && !dismissed.user) {
    const { used, limit, percent } = formatLimitMessage(data.user)
    banners.push({
      scope: "user",
      orgId,
      month: data.month,
      message: t("usage_limit_warning_user", { used, limit, percent }),
      onDismiss: () => setDismissed((prev) => ({ ...prev, user: true })),
    })
  }

  if (data.org?.near_limit && data.org.limit_usd != null && !dismissed.org) {
    const { used, limit, percent } = formatLimitMessage(data.org)
    banners.push({
      scope: "org",
      orgId,
      month: data.month,
      message: t("usage_limit_warning_org", { used, limit, percent }),
      onDismiss: () => setDismissed((prev) => ({ ...prev, org: true })),
    })
  }

  if (banners.length === 0) return null

  return (
    <div className="shrink-0">
      {banners.map((banner) => (
        <UsageLimitBanner key={banner.scope} {...banner} />
      ))}
    </div>
  )
}
