import { describe, expect, it } from "vitest"

import {
  formatCostPerImage,
  formatCostPerMillion,
  groupModelsByCostTier,
  groupModelsByDisplayName,
  modelCostSymbols,
  normalizeModelCostTier,
  splitModelDisplayName,
} from "./modelCapabilities"

describe("splitModelDisplayName", () => {
  it("splits a single slash into group and label", () => {
    expect(splitModelDisplayName("OpenAI/gpt-4o")).toEqual({
      group: "OpenAI",
      label: "gpt-4o",
    })
  })

  it("treats names without a slash as a plain label", () => {
    expect(splitModelDisplayName("Claude Sonnet")).toEqual({
      group: null,
      label: "Claude Sonnet",
    })
  })

  it("does not group when more than one slash is present", () => {
    expect(splitModelDisplayName("a/b/c")).toEqual({
      group: null,
      label: "a/b/c",
    })
  })
})

describe("groupModelsByDisplayName", () => {
  it("collects shared groups while keeping ungrouped models in order", () => {
    const models = [
      { id: "1", display_name: "OpenAI/gpt-4o" },
      { id: "2", display_name: "Solo" },
      { id: "3", display_name: "OpenAI/gpt-5" },
      { id: "4", display_name: "Anthropic/claude" },
    ]
    expect(groupModelsByDisplayName(models)).toEqual([
      {
        group: "OpenAI",
        models: [
          { id: "1", display_name: "OpenAI/gpt-4o" },
          { id: "3", display_name: "OpenAI/gpt-5" },
        ],
      },
      { group: null, models: [{ id: "2", display_name: "Solo" }] },
      {
        group: "Anthropic",
        models: [{ id: "4", display_name: "Anthropic/claude" }],
      },
    ])
  })
})

describe("normalizeModelCostTier", () => {
  it("accepts only 1–4", () => {
    expect(normalizeModelCostTier(1)).toBe(1)
    expect(normalizeModelCostTier(4)).toBe(4)
    expect(normalizeModelCostTier(0)).toBeNull()
    expect(normalizeModelCostTier(5)).toBeNull()
    expect(normalizeModelCostTier(null)).toBeNull()
  })
})

describe("modelCostSymbols", () => {
  it("repeats euro signs by tier", () => {
    expect(modelCostSymbols(1)).toBe("€")
    expect(modelCostSymbols(3)).toBe("€€€")
  })
})

describe("formatCostPerMillion", () => {
  it("formats known rates and blanks unknowns", () => {
    expect(formatCostPerMillion(null)).toBe("—")
    expect(formatCostPerMillion(0.0015)).toBe("$0.0015/M")
    expect(formatCostPerMillion(0.15)).toBe("$0.150/M")
    expect(formatCostPerMillion(2.5)).toBe("$2.50/M")
  })
})

describe("formatCostPerImage", () => {
  it("formats per-image USD", () => {
    expect(formatCostPerImage(null)).toBe("—")
    expect(formatCostPerImage(0.0315)).toBe("$0.032")
    expect(formatCostPerImage(0.04)).toBe("$0.040")
  })
})

describe("groupModelsByCostTier", () => {
  it("buckets by tier and keeps unknown last", () => {
    const models = [
      { id: "a", cost_tier: 3 },
      { id: "b", cost_tier: 1 },
      { id: "c", cost_tier: null },
      { id: "d", cost_tier: 1 },
      { id: "e", cost_tier: 4 },
    ]
    expect(groupModelsByCostTier(models)).toEqual([
      {
        tier: 1,
        models: [
          { id: "b", cost_tier: 1 },
          { id: "d", cost_tier: 1 },
        ],
      },
      { tier: 3, models: [{ id: "a", cost_tier: 3 }] },
      { tier: 4, models: [{ id: "e", cost_tier: 4 }] },
      { tier: null, models: [{ id: "c", cost_tier: null }] },
    ])
  })
})
