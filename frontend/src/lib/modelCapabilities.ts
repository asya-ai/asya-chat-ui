import type { ChatModel, ModelCostTier } from "./types"

export function supportsImageInput(model: ChatModel | null | undefined): boolean {
  return model?.supports_image_input === true
}

export function supportsImageOutput(model: ChatModel | null | undefined): boolean {
  return model?.supports_image_output === true
}

/** Split `Group/name` display names. At most one `/`; both sides required. */
export function splitModelDisplayName(displayName: string): {
  group: string | null
  label: string
} {
  const trimmed = displayName.trim()
  const slash = trimmed.indexOf("/")
  if (slash <= 0) return { group: null, label: trimmed }
  if (trimmed.indexOf("/", slash + 1) !== -1) return { group: null, label: trimmed }
  const group = trimmed.slice(0, slash).trim()
  const label = trimmed.slice(slash + 1).trim()
  if (!group || !label) return { group: null, label: trimmed }
  return { group, label }
}

/** Group models by display-name prefix; preserve list order within and across groups. */
export function groupModelsByDisplayName<T extends { id: string; display_name: string }>(
  models: T[]
): { group: string | null; models: T[] }[] {
  const sections: { group: string | null; models: T[] }[] = []
  const groupIndex = new Map<string, number>()

  for (const model of models) {
    const { group } = splitModelDisplayName(model.display_name)
    if (group === null) {
      sections.push({ group: null, models: [model] })
      continue
    }
    const existing = groupIndex.get(group)
    if (existing === undefined) {
      groupIndex.set(group, sections.length)
      sections.push({ group, models: [model] })
    } else {
      sections[existing].models.push(model)
    }
  }

  return sections
}

export function normalizeModelCostTier(
  value: number | null | undefined
): ModelCostTier | null {
  if (value === 1 || value === 2 || value === 3 || value === 4) return value
  return null
}

export function modelCostSymbols(tier: ModelCostTier): string {
  return "€".repeat(tier)
}

/** Format USD per million tokens for admin display. */
export function formatCostPerMillion(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—"
  if (value < 0.01) return `$${value.toFixed(4)}/M`
  if (value < 1) return `$${value.toFixed(3)}/M`
  return `$${value.toFixed(2)}/M`
}

/** Format USD per generated image for admin display. */
export function formatCostPerImage(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—"
  if (value < 0.01) return `$${value.toFixed(4)}`
  if (value < 1) return `$${value.toFixed(3)}`
  return `$${value.toFixed(2)}`
}

export function modelCostTierClassName(tier: ModelCostTier): string {
  switch (tier) {
    case 1:
      return "text-emerald-600 dark:text-emerald-400"
    case 2:
      return "text-amber-500 dark:text-amber-400"
    case 3:
      return "text-orange-500 dark:text-orange-400"
    case 4:
      return "text-red-600 dark:text-red-400"
  }
}

/** Group by cost tier (€→€€€€); unknown pricing last. Preserves list order within tiers. */
export function groupModelsByCostTier<
  T extends { id: string; cost_tier?: number | null },
>(models: T[]): { tier: ModelCostTier | null; models: T[] }[] {
  const buckets: { tier: ModelCostTier | null; models: T[] }[] = [
    { tier: 1, models: [] },
    { tier: 2, models: [] },
    { tier: 3, models: [] },
    { tier: 4, models: [] },
    { tier: null, models: [] },
  ]

  for (const model of models) {
    const tier = normalizeModelCostTier(model.cost_tier)
    const index = tier === null ? 4 : tier - 1
    buckets[index].models.push(model)
  }

  return buckets.filter((section) => section.models.length > 0)
}
