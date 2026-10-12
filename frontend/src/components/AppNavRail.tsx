import { Link } from "@tanstack/react-router"

import { BrandLogo } from "@/components/BrandLogo"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

type AppNavRailProps = {
  profileLabel: string | null
  avatarUrl?: string | null
  settingsHref?: string
  settingsLabel: string
  onLogoClick?: () => void
  onMouseEnter?: () => void
  onMouseLeave?: () => void
  className?: string
}

/** Persistent 72px icon rail (logo + avatar). */
export const AppNavRail = ({
  profileLabel,
  avatarUrl,
  settingsHref = "/settings/me",
  settingsLabel,
  onLogoClick,
  onMouseEnter,
  onMouseLeave,
  className,
}: AppNavRailProps) => (
  <aside
    className={cn(
      "hidden z-30 md:flex flex-col items-center bg-background p-2 w-18 shrink-0",
      className
    )}
    onMouseEnter={onMouseEnter}
    onMouseLeave={onMouseLeave}
  >
    <button
      type="button"
      className="flex justify-center items-center pt-4 rounded-lg size-11"
      onClick={onLogoClick}
    >
      <BrandLogo className="w-[39.54px] h-11" />
    </button>
    <div className="flex-1 min-h-0" />
    <Button
      variant="ghost"
      className="justify-center p-1.5 size-14 shrink-0"
      asChild
    >
      <Link to={settingsHref} aria-label={profileLabel ?? settingsLabel}>
        {avatarUrl ? (
          <img
            src={avatarUrl}
            alt=""
            className="rounded-lg size-11 object-cover shrink-0"
          />
        ) : (
          <span className="flex justify-center items-center bg-secondary rounded-lg size-11 font-semibold text-sm shrink-0">
            {profileLabel ? profileLabel.slice(0, 1).toUpperCase() : null}
          </span>
        )}
      </Link>
    </Button>
  </aside>
)
