export type Org = {
  id: string
  name: string
  slug?: string | null
  is_active: boolean
  is_frozen: boolean
}

export type OrgWebSettings = {
  web_tools_enabled: boolean
  web_search_enabled: boolean
  web_scrape_enabled: boolean
  web_grounding_openai: boolean
  web_grounding_gemini: boolean
  exec_network_enabled: boolean
  exec_policy: "off" | "prompt" | "always"
}

export type OrgAuthSettings = {
  slug: string
  oidc_enabled: boolean
  oidc_issuer?: string | null
  oidc_client_id?: string | null
  oidc_client_secret_set: boolean
  oidc_scopes: string
  oidc_email_claim: string
  oidc_username_claim?: string | null
  oidc_groups_claim?: string | null
  oidc_auto_create_users: boolean
}

export type OrgAuthSettingsUpdate = {
  slug?: string | null
  oidc_enabled?: boolean
  oidc_issuer?: string | null
  oidc_client_id?: string | null
  oidc_client_secret?: string | null
  oidc_scopes?: string | null
  oidc_email_claim?: string | null
  oidc_username_claim?: string | null
  oidc_groups_claim?: string | null
  oidc_auto_create_users?: boolean
}

export type ApiKey = {
  id: string
  name: string
  prefix: string
  created_at: string
  last_used_at?: string | null
  revoked_at?: string | null
}

export type ChatModel = {
  id: string
  provider: string
  model_name: string
  display_name: string
  is_active: boolean
  display_order?: number
  context_length?: number | null
  supports_image_input?: boolean | null
  supports_image_output?: boolean | null
  reasoning_effort?: string | null
  is_available?: boolean
}

export type ModelSuggestionItem = {
  model_name: string
  display_name: string
  context_length?: number | null
  supports_image_input?: boolean | null
  supports_image_output?: boolean | null
  reasoning_effort?: string | null
}

export type ModelSuggestionProvider = {
  provider: string
  models: ModelSuggestionItem[]
  error?: string | null
}

export type Chat = {
  id: string
  title?: string | null
  model_id?: string | null
  created_at: string
  last_activity_at: string
}

export type ChatMessage = {
  id: string
  role: string
  content: string
  created_at: string
  model_id?: string | null
  model_name?: string | null
  attachments?: ChatMessageAttachmentLike[] | null
  sources?: SourceItem[] | null
  thinking_steps?: string[] | null
  tool_event?: ToolEvent | null
}

export type SourceItem = {
  url: string
  title?: string | null
  host?: string | null
}

export type ToolEvent = {
  type: "code_execution"
  id?: string
  code: string
  output: {
    stdout?: string | null
    stderr?: string | null
    exit_code?: number | null
    timed_out?: boolean | null
    error?: string | null
    requires_approval?: boolean | null
    outputs?: string[] | null
    output_files?: {
      file_name: string
      content_type: string
      data_base64: string
    }[] | null
  }
}

export type ChatMessageAttachment = {
  id: string
  file_name: string
  content_type: string
  data_base64: string
}

export type ChatMessageAttachmentInput = {
  file_name: string
  content_type: string
  data_base64: string
}

export type ChatMessageAttachmentLike =
  | ChatMessageAttachment
  | ChatMessageAttachmentInput

export type UsageSlice = {
  key: string
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  input_tokens: number
  output_tokens: number
  cached_tokens: number
  thinking_tokens: number
}

export type OrgMember = {
  user_id: string
  email: string
  role: string
  is_super_admin: boolean
}

export type ProviderConfig = {
  provider: string
  is_enabled: boolean
  api_key_override_set: boolean
  base_url_override?: string | null
  endpoint_override?: string | null
  api_key_override?: string
  config_json?: string | null
  has_global_config: boolean
}

export type ProviderConfigUpdate = {
  provider: string
  is_enabled?: boolean
  api_key_override?: string | null
  base_url_override?: string | null
  endpoint_override?: string | null
  config_json?: string | null
}

export type Invite = {
  id: string
  org_id: string
  email: string
  token: string
  expires_at: string
  accepted_at?: string | null
  created_at: string
}
