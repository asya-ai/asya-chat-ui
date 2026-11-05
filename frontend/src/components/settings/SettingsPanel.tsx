import type { LucideIcon } from "lucide-react"
import { ChevronRight } from "lucide-react"
import type { ComponentPropsWithoutRef, ReactNode } from "react"

import { cn } from "@/lib/utils"

const settingsControlClass = "w-full sm:w-44 sm:shrink-0"

type SettingsPageProps = {
  children: ReactNode
  className?: string
  wide?: boolean
  /** Personal / simple settings (profile, whitelabel). */
  narrow?: boolean
}

export const SettingsPage = ({
  children,
  className,
  wide = false,
  narrow = false,
}: SettingsPageProps) => (
  <div
    className={cn(
      "mx-auto flex h-full w-full flex-col gap-6 overflow-y-auto px-4 py-5 sm:px-6 sm:py-8",
      wide ? "max-w-6xl" : narrow ? "max-w-lg" : "max-w-2xl",
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
  <section className={cn("space-y-2", className)}>
    {title ? (
      <div className="px-0.5">
        <h2 className="text-muted-foreground text-[11px] font-semibold uppercase tracking-wider">
          {title}
        </h2>
        {description ? (
          <p className="text-muted-foreground mt-1 text-xs leading-5">{description}</p>
        ) : null}
      </div>
    ) : null}
    <div className="overflow-hidden rounded-[var(--radius-card)] border border-border/80 bg-card shadow-sm divide-y divide-border/80">
      {children}
    </div>
  </section>
)

export const SettingsSectionMessage = ({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) => (
  <p className={cn("text-muted-foreground px-4 py-3.5 text-sm leading-5 sm:px-5", className)}>
    {children}
  </p>
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
  <div className={cn("px-4 py-3 sm:px-5", className)}>
    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between sm:gap-6">
      <div className="min-w-0 flex-1">
        {htmlFor ? (
          <label htmlFor={htmlFor} className="block text-sm leading-snug">
            {label}
          </label>
        ) : (
          <p className="text-sm leading-snug">{label}</p>
        )}
        {description ? (
          <p className="text-muted-foreground mt-0.5 text-xs leading-5">{description}</p>
        ) : null}
        {footer ? <div className="mt-2">{footer}</div> : null}
      </div>
      <div className="flex shrink-0 items-center sm:justify-end">{children}</div>
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
      "flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/40 sm:px-5",
      className
    )}
    {...props}
  >
    <div className="min-w-0 flex-1">
      <p className={cn("text-sm leading-snug", destructive && "text-destructive")}>
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
