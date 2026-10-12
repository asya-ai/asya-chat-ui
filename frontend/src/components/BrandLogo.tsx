import { useBranding } from "@/lib/branding"
import { cn } from "@/lib/utils"

type BrandLogoProps = {
  className?: string
  alt?: string
}

/** Theme-aware Eldigen mark (mask follows --icon-solid), or custom whitelabel logo. */
export const BrandLogo = ({ className, alt = "Eldigen" }: BrandLogoProps) => {
  const { logoUrl } = useBranding()

  if (logoUrl) {
    return (
      <img
        src={logoUrl}
        alt={alt}
        className={cn("object-contain", className)}
        draggable={false}
      />
    )
  }

  return (
    <span
      role="img"
      aria-label={alt}
      className={cn("figma-icon", className)}
      style={{
        maskImage: "url('/logo_chat.svg')",
        WebkitMaskImage: "url('/logo_chat.svg')",
      }}
    />
  )
}
