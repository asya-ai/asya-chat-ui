const TOKEN_KEY = "chatui_token"
const ORG_KEY = "chatui_org"
const MODEL_KEY = "chatui_model"
const LOCALE_KEY = "chatui_locale"
const LOGIN_ORG_KEY = "chatui_login_org"
const WEB_SEARCH_ENABLED_KEY = "chatui_web_search_enabled"
const CODE_EXECUTION_ENABLED_KEY = "chatui_code_execution_enabled"
const TOOLCALL_LOGS_VISIBLE_KEY = "chatui_toolcall_logs_visible"

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

export const toolCallLogsVisibleStore = {
  get: () => localStorage.getItem(TOOLCALL_LOGS_VISIBLE_KEY),
  set: (enabled: boolean) =>
    localStorage.setItem(TOOLCALL_LOGS_VISIBLE_KEY, enabled ? "1" : "0"),
  clear: () => localStorage.removeItem(TOOLCALL_LOGS_VISIBLE_KEY),
}
