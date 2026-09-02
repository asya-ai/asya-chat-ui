import type { LucideIcon } from "lucide-react"
import { ChevronRight } from "lucide-react"
import type { ComponentPropsWithoutRef, ReactNode } from "react"

import { cn } from "@/lib/utils"

const settingsControlClass = "w-full sm:w-40"

type SettingsPageProps = {
  children: ReactNode
  className?: string
  wide?: boolean
}

export const SettingsPage = ({ children, className, wide = false }: SettingsPageProps) => (
  <div
    className={cn(
      "mx-auto flex h-full w-full flex-col gap-5 overflow-y-auto p-4 sm:p-6",
      wide ? "max-w-6xl" : "max-w-3xl",
      className
    )}
  >
    {children}
  </div>
)

type SettingsPageHeaderProps = {
  title: string
  description?: string
  icon?: LucideIcon
  actions?: ReactNode
}

export const SettingsPageHeader = ({
  title,
  description,
  icon: Icon,
  actions,
}: SettingsPageHeaderProps) => (
  <header className="flex flex-wrap items-start justify-between gap-3 pb-1">
    <div className="flex min-w-0 items-center gap-3">
      {Icon ? <Icon className="size-5 shrink-0 text-muted-foreground" aria-hidden="true" /> : null}
      <div className="min-w-0">
        <h1 className="font-heading text-3xl font-normal leading-9">{title}</h1>
        {description ? (
          <p className="text-muted-foreground mt-0.5 text-sm leading-6">{description}</p>
        ) : null}
      </div>
    </div>
    {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
  </header>
)

type SettingsSectionProps = {
  title?: string
  description?: string
  children: ReactNode
  className?: string
}

export const SettingsSection = ({
  title,
  description,
  children,
  className,
}: SettingsSectionProps) => (
  <section className={cn("space-y-1", className)}>
    {title ? (
      <div className="px-1">
        <h2 className="text-muted-foreground text-xs font-semibold uppercase tracking-wide">
          {title}
        </h2>
        {description ? (
          <p className="text-muted-foreground mt-0.5 text-xs leading-5">{description}</p>
        ) : null}
      </div>
    ) : null}
    <div className="overflow-hidden rounded-[var(--radius-card)] border border-border bg-card divide-y divide-border">
      {children}
    </div>
  </section>
)

type SettingsRowProps = {
  label: string
  description?: string
  children: ReactNode
  className?: string
  htmlFor?: string
  footer?: ReactNode
}

export const SettingsRow = ({
  label,
  description,
  children,
  className,
  htmlFor,
  footer,
}: SettingsRowProps) => (
  <div className={cn("px-4 py-2.5 sm:px-5", className)}>
    <div className="grid grid-cols-1 items-center gap-3 sm:grid-cols-[minmax(0,1fr)_10rem] sm:gap-6">
      <div className="min-w-0">
        {htmlFor ? (
          <label htmlFor={htmlFor} className="block text-sm leading-5">
            {label}
          </label>
        ) : (
          <p className="text-sm leading-5">{label}</p>
        )}
        {description ? (
          <p className="text-muted-foreground mt-0.5 text-xs leading-5">{description}</p>
        ) : null}
        {footer ? <div className="mt-2">{footer}</div> : null}
      </div>
      <div className="flex items-center justify-start sm:justify-end">{children}</div>
    </div>
  </div>
)

export const SettingsControl = ({
  className,
  children,
}: {
  className?: string
  children: ReactNode
}) => <div className={cn(settingsControlClass, className)}>{children}</div>

type SettingsActionRowProps = ComponentPropsWithoutRef<"button"> & {
  label: string
  description?: string
  destructive?: boolean
}

export const SettingsActionRow = ({
  label,
  description,
  destructive = false,
  className,
  ...props
}: SettingsActionRowProps) => (
  <button
    type="button"
    className={cn(
      "flex w-full items-center gap-3 px-4 py-2.5 text-left transition-colors hover:bg-muted/50 sm:px-5",
      className
    )}
    {...props}
  >
    <div className="min-w-0 flex-1">
      <p
        className={cn(
          "text-sm font-medium leading-5",
          destructive && "text-destructive"
        )}
      >
        {label}
      </p>
      {description ? (
        <p className="text-muted-foreground mt-0.5 text-xs leading-5">{description}</p>
      ) : null}
    </div>
    {!destructive ? (
      <ChevronRight className="text-muted-foreground size-4 shrink-0" aria-hidden="true" />
    ) : null}
  </button>
)

type SettingsListItemProps = {
  title: string
  subtitle?: string
  meta?: ReactNode
  actions?: ReactNode
  className?: string
}

export const SettingsListItem = ({
  title,
  subtitle,
  meta,
  actions,
  className,
}: SettingsListItemProps) => (
  <div
    className={cn(
      "flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-5 sm:py-3.5",
      className
    )}
  >
    <div className="min-w-0 space-y-0.5">
      <p className="truncate text-sm font-medium leading-5">{title}</p>
      {subtitle ? (
        <p className="text-muted-foreground truncate text-xs leading-5">{subtitle}</p>
      ) : null}
      {meta ? <div className="pt-0.5">{meta}</div> : null}
    </div>
    {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
  </div>
)

export const SettingsEmptyState = ({
  title,
  description,
  action,
}: {
  title: string
  description?: string
  action?: ReactNode
}) => (
  <div className="flex flex-col items-center justify-center gap-3 px-6 py-12 text-center">
    <div className="space-y-1">
      <p className="font-medium">{title}</p>
      {description ? (
        <p className="text-muted-foreground text-sm">{description}</p>
      ) : null}
    </div>
    {action}
  </div>
)
