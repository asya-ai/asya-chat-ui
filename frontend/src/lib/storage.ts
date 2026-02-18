const TOKEN_KEY = "chatui_token"
const ORG_KEY = "chatui_org"
const MODEL_KEY = "chatui_model"
const LOCALE_KEY = "chatui_locale"

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
