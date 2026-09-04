const TOKEN_KEY = "chatui_token"
const ORG_KEY = "chatui_org"
const MODEL_KEY = "chatui_model"
const LOCALE_KEY = "chatui_locale"
const LOGIN_ORG_KEY = "chatui_login_org"
const WEB_SEARCH_ENABLED_KEY = "chatui_web_search_enabled"
const CODE_EXECUTION_ENABLED_KEY = "chatui_code_execution_enabled"
const REASONING_EFFORT_BY_MODEL_KEY = "chatui_reasoning_effort_by_model"
const ACTION_INFO_LEVEL_KEY = "chatui_toolcall_logs_visible"
const SIDEBAR_SECTIONS_KEY = "chatui_sidebar_sections"
const COWORK_PANEL_WIDTH_KEY = "chatui_cowork_panel_width_pct"
const COMPOSER_TEXTAREA_HEIGHT_KEY = "chatui_composer_textarea_height"

const DEFAULT_COWORK_PANEL_WIDTH_PCT = 50
const DEFAULT_COMPOSER_TEXTAREA_HEIGHT = 88
const MIN_COMPOSER_TEXTAREA_HEIGHT = 52
const COMPOSER_TEXTAREA_AUTO_GROW_MAX_HEIGHT_VH = 0.5
const COMPOSER_TEXTAREA_MAX_HEIGHT_VH = 0.75
const MIN_COWORK_PANEL_WIDTH_PCT = 24
const MAX_COWORK_PANEL_WIDTH_PCT = 76

const parseCoworkPanelWidthPct = (raw: string | null): number => {
  if (raw == null) return DEFAULT_COWORK_PANEL_WIDTH_PCT
  const value = Number(raw)
  if (!Number.isFinite(value)) return DEFAULT_COWORK_PANEL_WIDTH_PCT
  return Math.min(
    MAX_COWORK_PANEL_WIDTH_PCT,
    Math.max(MIN_COWORK_PANEL_WIDTH_PCT, value)
  )
}

export type ActionInfoLevel = "none" | "detailed"

export type SidebarSectionsState = {
  pinned: boolean
  projects: boolean
  prompts: boolean
  sessions: boolean
}

const DEFAULT_SIDEBAR_SECTIONS: SidebarSectionsState = {
  pinned: true,
  projects: false,
  prompts: false,
  sessions: true,
}

const parseSidebarSections = (raw: string | null): SidebarSectionsState => {
  if (!raw) return { ...DEFAULT_SIDEBAR_SECTIONS }
  try {
    const parsed = JSON.parse(raw) as Partial<SidebarSectionsState> & {
      spaces?: boolean
    }
    return {
      pinned: parsed.pinned == null ? true : Boolean(parsed.pinned),
      projects:
        parsed.projects != null
          ? Boolean(parsed.projects)
          : Boolean(parsed.spaces),
      prompts: Boolean(parsed.prompts),
      sessions: parsed.sessions == null ? true : Boolean(parsed.sessions),
    }
  } catch {
    return { ...DEFAULT_SIDEBAR_SECTIONS }
  }
}

const parseActionInfoLevel = (raw: string | null): ActionInfoLevel => {
  if (raw === "none" || raw === "detailed") return raw
  // Legacy values: short / boolean on → detailed, off/unset → hidden.
  if (raw === "short" || raw === "1") return "detailed"
  return "none"
}

export const tokenStore = {
  get: () => localStorage.getItem(TOKEN_KEY),
  set: (token: string) => localStorage.setItem(TOKEN_KEY, token),
  clear: () => localStorage.removeItem(TOKEN_KEY),
}

export const orgStore = {
  get: () => localStorage.getItem(ORG_KEY),
  set: (orgId: string) => localStorage.setItem(ORG_KEY, orgId),
  clear: () => localStorage.removeItem(ORG_KEY),
}

export const modelStore = {
  get: () => localStorage.getItem(MODEL_KEY),
  set: (modelId: string) => localStorage.setItem(MODEL_KEY, modelId),
  clear: () => localStorage.removeItem(MODEL_KEY),
}

export const localeStore = {
  get: () => localStorage.getItem(LOCALE_KEY),
  set: (locale: string) => localStorage.setItem(LOCALE_KEY, locale),
  clear: () => localStorage.removeItem(LOCALE_KEY),
}

export const loginOrgStore = {
  get: () => localStorage.getItem(LOGIN_ORG_KEY),
  set: (org: string) => localStorage.setItem(LOGIN_ORG_KEY, org),
  clear: () => localStorage.removeItem(LOGIN_ORG_KEY),
}

export const webSearchEnabledStore = {
  get: () => localStorage.getItem(WEB_SEARCH_ENABLED_KEY),
  set: (enabled: boolean) =>
    localStorage.setItem(WEB_SEARCH_ENABLED_KEY, enabled ? "1" : "0"),
  clear: () => localStorage.removeItem(WEB_SEARCH_ENABLED_KEY),
}

export const codeExecutionEnabledStore = {
  get: () => localStorage.getItem(CODE_EXECUTION_ENABLED_KEY),
  set: (enabled: boolean) =>
    localStorage.setItem(CODE_EXECUTION_ENABLED_KEY, enabled ? "1" : "0"),
  clear: () => localStorage.removeItem(CODE_EXECUTION_ENABLED_KEY),
}

const parseReasoningEffortByModel = (raw: string | null): Record<string, string> => {
  if (!raw) return {}
  try {
    const parsed = JSON.parse(raw) as unknown
    if (!parsed || typeof parsed !== "object") return {}
    return Object.fromEntries(
      Object.entries(parsed as Record<string, unknown>).filter(
        (entry): entry is [string, string] => typeof entry[1] === "string"
      )
    )
  } catch {
    return {}
  }
}

export const reasoningEffortStore = {
  get: (modelId: string): string | null => {
    const map = parseReasoningEffortByModel(
      localStorage.getItem(REASONING_EFFORT_BY_MODEL_KEY)
    )
    return map[modelId] ?? null
  },
  set: (modelId: string, effort: string) => {
    const map = parseReasoningEffortByModel(
      localStorage.getItem(REASONING_EFFORT_BY_MODEL_KEY)
    )
    map[modelId] = effort
    localStorage.setItem(REASONING_EFFORT_BY_MODEL_KEY, JSON.stringify(map))
  },
  clear: () => localStorage.removeItem(REASONING_EFFORT_BY_MODEL_KEY),
}

export const actionInfoLevelStore = {
  get: (): ActionInfoLevel =>
    parseActionInfoLevel(localStorage.getItem(ACTION_INFO_LEVEL_KEY)),
  set: (level: ActionInfoLevel) =>
    localStorage.setItem(ACTION_INFO_LEVEL_KEY, level),
  clear: () => localStorage.removeItem(ACTION_INFO_LEVEL_KEY),
  parse: parseActionInfoLevel,
}

export const sidebarSectionsStore = {
  get: (): SidebarSectionsState =>
    parseSidebarSections(localStorage.getItem(SIDEBAR_SECTIONS_KEY)),
  set: (state: SidebarSectionsState) =>
    localStorage.setItem(SIDEBAR_SECTIONS_KEY, JSON.stringify(state)),
  clear: () => localStorage.removeItem(SIDEBAR_SECTIONS_KEY),
}

export const coworkPanelWidthStore = {
  default: DEFAULT_COWORK_PANEL_WIDTH_PCT,
  min: MIN_COWORK_PANEL_WIDTH_PCT,
  max: MAX_COWORK_PANEL_WIDTH_PCT,
  get: (): number => parseCoworkPanelWidthPct(localStorage.getItem(COWORK_PANEL_WIDTH_KEY)),
  set: (pct: number) =>
    localStorage.setItem(
      COWORK_PANEL_WIDTH_KEY,
      String(parseCoworkPanelWidthPct(String(pct)))
    ),
  clear: () => localStorage.removeItem(COWORK_PANEL_WIDTH_KEY),
}

const parseComposerTextareaHeight = (raw: string | null): number => {
  if (raw == null) return DEFAULT_COMPOSER_TEXTAREA_HEIGHT
  const value = Number(raw)
  if (!Number.isFinite(value)) return DEFAULT_COMPOSER_TEXTAREA_HEIGHT
  return Math.max(MIN_COMPOSER_TEXTAREA_HEIGHT, Math.round(value))
}

export const composerTextareaHeightStore = {
  default: DEFAULT_COMPOSER_TEXTAREA_HEIGHT,
  min: MIN_COMPOSER_TEXTAREA_HEIGHT,
  autoGrowMaxHeightVh: COMPOSER_TEXTAREA_AUTO_GROW_MAX_HEIGHT_VH,
  maxHeightVh: COMPOSER_TEXTAREA_MAX_HEIGHT_VH,
  get: (): number =>
    parseComposerTextareaHeight(localStorage.getItem(COMPOSER_TEXTAREA_HEIGHT_KEY)),
  set: (px: number) =>
    localStorage.setItem(
      COMPOSER_TEXTAREA_HEIGHT_KEY,
      String(parseComposerTextareaHeight(String(px)))
    ),
  clear: () => localStorage.removeItem(COMPOSER_TEXTAREA_HEIGHT_KEY),
}

const USAGE_LIMIT_WARN_DISMISS_KEY = "chatui_usage_limit_warn_dismissed"

const parseUsageLimitDismissed = (raw: string | null): Record<string, true> => {
  if (!raw) return {}
  try {
    const parsed = JSON.parse(raw) as unknown
    if (!parsed || typeof parsed !== "object") return {}
    return Object.fromEntries(
      Object.keys(parsed as Record<string, unknown>).map((key) => [key, true as const])
    )
  } catch {
    return {}
  }
}

export const usageLimitWarningDismissStore = {
  keyFor: (scope: "user" | "org", orgId: string, month: string) =>
    `${scope}:${orgId}:${month}`,
  isDismissed: (scope: "user" | "org", orgId: string, month: string) => {
    const map = parseUsageLimitDismissed(localStorage.getItem(USAGE_LIMIT_WARN_DISMISS_KEY))
    return Boolean(map[usageLimitWarningDismissStore.keyFor(scope, orgId, month)])
  },
  dismiss: (scope: "user" | "org", orgId: string, month: string) => {
    const map = parseUsageLimitDismissed(localStorage.getItem(USAGE_LIMIT_WARN_DISMISS_KEY))
    map[usageLimitWarningDismissStore.keyFor(scope, orgId, month)] = true
    localStorage.setItem(USAGE_LIMIT_WARN_DISMISS_KEY, JSON.stringify(map))
  },
}
