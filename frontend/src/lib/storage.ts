const TOKEN_KEY = "chatui_token"
const ORG_KEY = "chatui_org"
const MODEL_KEY = "chatui_model"
const LOCALE_KEY = "chatui_locale"
const LOGIN_ORG_KEY = "chatui_login_org"
const WEB_SEARCH_ENABLED_KEY = "chatui_web_search_enabled"
const CODE_EXECUTION_ENABLED_KEY = "chatui_code_execution_enabled"
const ACTION_INFO_LEVEL_KEY = "chatui_toolcall_logs_visible"
const SIDEBAR_SECTIONS_KEY = "chatui_sidebar_sections"
const COWORK_PANEL_WIDTH_KEY = "chatui_cowork_panel_width_pct"

const DEFAULT_COWORK_PANEL_WIDTH_PCT = 50
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

export type ActionInfoLevel = "none" | "short" | "detailed"

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
  if (raw === "none" || raw === "short" || raw === "detailed") return raw
  // Legacy boolean toggle: on → detailed, off/unset → short.
  if (raw === "1") return "detailed"
  return "short"
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
