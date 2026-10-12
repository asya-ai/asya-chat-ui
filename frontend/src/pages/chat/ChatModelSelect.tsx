import { useEffect, useMemo, useRef, type ReactNode } from "react"
import { Check, ChevronDown, Image as ImageIcon } from "lucide-react"

import { ProviderIcon } from "@/components/ProviderIcon"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  groupModelsByDisplayName,
  modelCostSymbols,
  modelCostTierClassName,
  normalizeModelCostTier,
  splitModelDisplayName,
  supportsImageOutput,
} from "@/lib/modelCapabilities"
import { getProviderIconCandidates } from "@/lib/providerIcons"
import type { ChatModel, ModelCostTier } from "@/lib/types"
import { cn } from "@/lib/utils"

type ChatModelSelectProps = {
  models: ChatModel[]
  selectedModelId?: string | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onSelect: (modelId: string) => void
  selectLabel: string
  emptyLabel: string
  disabled?: boolean
  footer?: ReactNode
}

const ModelCostBadge = ({
  tier,
  className,
}: {
  tier: ModelCostTier
  className?: string
}) => (
  <span
    aria-hidden="true"
    className={cn(
      "shrink-0 text-xs font-semibold tracking-tight",
      modelCostTierClassName(tier),
      className
    )}
  >
    {modelCostSymbols(tier)}
  </span>
)

export const ChatModelSelect = ({
  models,
  selectedModelId,
  open,
  onOpenChange,
  onSelect,
  selectLabel,
  emptyLabel,
  disabled = false,
  footer,
}: ChatModelSelectProps) => {
  const listRef = useRef<HTMLDivElement>(null)
  const sections = useMemo(() => groupModelsByDisplayName(models), [models])
  const selected = useMemo(
    () => models.find((model) => model.id === selectedModelId) ?? null,
    [models, selectedModelId]
  )

  useEffect(() => {
    if (!open || !selectedModelId || !listRef.current) return
    const selectedEl = listRef.current.querySelector(
      `[data-model-id="${selectedModelId}"]`
    )
    selectedEl?.scrollIntoView({ block: "nearest" })
  }, [open, selectedModelId])

  return (
    <DropdownMenu modal={false} open={open} onOpenChange={onOpenChange}>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          className="h-9 max-w-full min-w-0 gap-1.5 px-2 font-normal"
          aria-label={selectLabel}
          disabled={disabled}
        >
          {selected ? (
            <>
              {getProviderIconCandidates(selected.provider, selected.model_name)
                .length > 0 ? (
                <ProviderIcon
                  provider={selected.provider}
                  modelName={selected.model_name}
                  className="size-5 shrink-0"
                />
              ) : supportsImageOutput(selected) ? (
                <ImageIcon className="size-3.5 shrink-0 text-muted-foreground" />
              ) : null}
              <span className="min-w-0 truncate">
                {splitModelDisplayName(selected.display_name).label}
              </span>
            </>
          ) : (
            <span className="min-w-0 truncate text-muted-foreground">{emptyLabel}</span>
          )}
          <ChevronDown aria-hidden="true" className="size-4 shrink-0 opacity-50" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        className="z-100 flex w-72 min-h-0 max-h-(--radix-dropdown-menu-content-available-height) flex-col overflow-hidden p-0"
      >
        <div
          ref={listRef}
          className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-1"
        >
          {sections.map((section) => (
            <DropdownMenuGroup
              key={
                section.group ?? section.models.map((model) => model.id).join("-")
              }
            >
              {section.group ? (
                <DropdownMenuLabel className="text-muted-foreground text-xs font-medium">
                  {section.group}
                </DropdownMenuLabel>
              ) : null}
              {section.models.map((model) => {
                const hasProviderIcon =
                  getProviderIconCandidates(model.provider, model.model_name)
                    .length > 0
                const isSelected = model.id === selectedModelId
                const { label } = splitModelDisplayName(model.display_name)
                const tier = normalizeModelCostTier(model.cost_tier)
                return (
                  <DropdownMenuItem
                    key={model.id}
                    data-model-id={model.id}
                    disabled={model.is_available === false}
                    className="cursor-pointer gap-2"
                    onClick={() => onSelect(model.id)}
                  >
                    <span className="inline-flex min-w-0 flex-1 items-center gap-2">
                      {hasProviderIcon ? (
                        <ProviderIcon
                          provider={model.provider}
                          modelName={model.model_name}
                          className="size-5 shrink-0"
                        />
                      ) : supportsImageOutput(model) ? (
                        <ImageIcon className="size-3.5 shrink-0 text-muted-foreground" />
                      ) : null}
                      <span className="truncate">{label}</span>
                    </span>
                    {tier ? <ModelCostBadge tier={tier} /> : null}
                    {isSelected ? (
                      <Check aria-hidden="true" className="size-4 shrink-0" />
                    ) : (
                      <span className="size-4 shrink-0" aria-hidden="true" />
                    )}
                  </DropdownMenuItem>
                )
              })}
            </DropdownMenuGroup>
          ))}
        </div>
        {footer ? (
          <div
            className="shrink-0 space-y-3 border-t px-3 py-2"
            onPointerDown={(event) => {
              const target = event.target
              if (
                target instanceof Element &&
                target.closest("input, button, label, [role='switch']")
              ) {
                return
              }
              event.preventDefault()
            }}
          >
            {footer}
          </div>
        ) : null}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
