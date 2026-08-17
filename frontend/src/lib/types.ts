export type Org = {
  id: string
  name: string
  slug?: string | null
  is_active: boolean
  is_frozen: boolean
  file_retention_days: number | null
  chat_retention_days: number | null
  cost_ceiling_usd?: number | null
}

export type OrgUpdate = {
  name?: string
  is_active?: boolean
  is_frozen?: boolean
  file_retention_days?: number | null
  chat_retention_days?: number | null
  cost_ceiling_usd?: number | null
}

export type OrgWebSettings = {
  web_tools_enabled: boolean
  web_search_enabled: boolean
  web_scrape_enabled: boolean
  web_grounding_openai: boolean
  web_grounding_gemini: boolean
  exec_network_enabled?: boolean
  exec_policy: "off" | "prompt" | "always"
}

export type OrgAuthSettings = {
  slug: string
  login_domains: string[]
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
  login_domains?: string[] | null
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

export type AttachmentLimits = {
  max_files: number
  max_file_bytes: number
  max_total_bytes: number
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
  uses_responses_api?: boolean | null
  reasoning_effort?: string | null
  openrouter_endpoint?: string | null
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

export type OpenRouterEndpoint = {
  tag: string
  name: string
  provider_name?: string | null
  quantization?: string | null
}

export type OpenRouterEndpointsResponse = {
  model_name: string
  endpoints: OpenRouterEndpoint[]
  error?: string | null
}

export type Chat = {
  id: string
  title?: string | null
  model_id?: string | null
  agent_id?: string | null
  is_shared?: boolean
  is_incognito?: boolean
  is_pinned?: boolean
  created_at: string
  last_activity_at: string
}

export type MessageUsage = {
  input_tokens: number
  output_tokens: number
  cached_tokens: number
  thinking_tokens: number
  total_tokens: number
  cost_usd?: number | null
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
  stream_parts?: AssistantStreamPart[] | null
  tool_event?: ToolEvent | null
  activity_event?: {
    type?: "chat_view" | string
    count?: number | null
    opens?: {
      viewer?: string | null
      opened_at?: string | null
    }[] | null
  } | null
  task_id?: string | null
  generation_status?: GenerationStatus | null
  usage?: MessageUsage | null
}

export type SourceItem = {
  url?: string | null
  title?: string | null
  host?: string | null
  source_id?: string | null
  snippet?: string | null
}

/** Same site/file once — matches backend `_dedupe_sources` identity rules. */
export const dedupeSources = (sources: SourceItem[]): SourceItem[] => {
  const seen = new Set<string>()
  const unique: SourceItem[] = []
  for (const source of sources) {
    const sourceId = source.source_id?.trim()
    const url = source.url?.trim()
    const title = source.title?.trim()
    const key = sourceId
      ? `id:${sourceId}`
      : url
        ? `url:${url}`
        : title
          ? `title:${title.toLowerCase()}`
          : null
    if (key) {
      if (seen.has(key)) continue
      seen.add(key)
    }
    unique.push(source)
  }
  return unique
}

export type CodeExecutionToolEvent = {
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

export type ContextSummaryToolEvent = {
  type: "context_summary"
  id?: string
  summary: string
  output?: {
    original_message_count?: number | null
    used_message_count?: number | null
  } | null
}

export type UrlAttachmentsToolEvent = {
  type: "url_attachments"
  id?: string
  urls?: string[] | null
  output?: {
    results?: {
      url?: string | null
      file_name?: string | null
      content_type?: string | null
      size_bytes?: number | null
      error?: string | null
    }[] | null
    error?: string | null
  } | null
}

export type ToolCallToolEvent = {
  type: "tool_call"
  id?: string
  tool_name: string
  state?: "start" | "end" | string
  input_preview?: string | null
  action_summary?: string | null
  output?: {
    status?: "ok" | "error" | string
    result_preview?: string | null
    error?: string | null
    raw_output?: unknown
    attachments?: ChatMessageAttachmentLike[] | null
  } | null
}

export type CoworkFormat = "markdown" | "code" | "text" | "json" | "csv" | "presentation"

export type CoworkDocument = {
  document_id: string
  chat_id: string
  title: string
  file_name: string
  format: CoworkFormat | string
  language?: string | null
  content?: string | null
  version: number
  is_active: boolean
  last_assistant_version: number
  user_edited: boolean
  updated_at?: string | null
  created_at?: string | null
}

export type CoworkingToolEvent = {
  type: "coworking"
  id?: string
  action?: "open" | "update" | "close" | "writing" | string
  document_id?: string
  title?: string | null
  file_name?: string | null
  format?: string | null
  language?: string | null
  version?: number | null
  last_assistant_version?: number | null
  user_edited?: boolean | null
  content?: string | null
  append_text?: string | null
  output?: {
    status?: "ok" | "error" | "writing" | string
    error?: string | null
    tool_name?: string | null
    synced?: boolean | null
  } | null
}

export type ToolEvent =
  | CodeExecutionToolEvent
  | ContextSummaryToolEvent
  | UrlAttachmentsToolEvent
  | ToolCallToolEvent
  | CoworkingToolEvent

export type GenerationStatus =
  | "queued"
  | "running"
  | "streaming"
  | "completed"
  | "failed"
  | "cancelled"

export type ChatGenerationTask = {
  id: string
  chat_id: string
  user_message_id: string
  assistant_message_id: string
  status: GenerationStatus
  error?: string | null
  created_at: string
  started_at?: string | null
  completed_at?: string | null
  model_id?: string | null
  model_name?: string | null
}

export type ChatGenerationEvent = {
  id: string
  event_type: string
  payload?: Record<string, unknown> | null
  sequence: number
  created_at: string
}

export type ChatMessageAttachment = {
  id: string
  file_name: string
  content_type: string
  data_base64?: string
  content_url?: string
}

export type ChatMessageAttachmentInput = {
  upload_id?: string
  file_name: string
  content_type: string
  data_base64?: string
  content_url?: string
}

export type ChatMessageAttachmentLike =
  | ChatMessageAttachment
  | ChatMessageAttachmentInput

export type AssistantStreamPart =
  | { type: "text"; text: string }
  | {
      type: "action"
      label: string
      attachments?: ChatMessageAttachmentLike[]
      tool_event?: ToolEvent
    }

export type UsageSlice = {
  key: string
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  input_tokens: number
  output_tokens: number
  cached_tokens: number
  thinking_tokens: number
  cost_usd?: number | null
  breakdown?: UsageSlice[]
}

export type EnvKeyDiagnosis = {
  key: string
  category: string
  status: "ok" | "invalid" | "missing"
  required: boolean
  detail?: string | null
  value?: string | null
}

export type DiskUsageInfo = {
  label: string
  path: string
  total_bytes?: number | null
  used_bytes?: number | null
  free_bytes?: number | null
  used_percent?: number | null
  error?: string | null
}

export type DependencyCheck = {
  name: string
  status: "ok" | "invalid" | "missing"
  latency_ms?: number | null
  detail?: string | null
}

export type ResourceMetric = {
  name: string
  value: string
  detail?: string | null
  status?: "ok" | "invalid" | "warning" | null
}

export type ProviderSnapshot = {
  provider: string
  status: "ok" | "invalid" | "missing"
  latency_ms?: number | null
  detail?: string | null
}

export type McpServerCheck = {
  id: string
  name: string
  transport?: string | null
  status: "ok" | "invalid" | "missing"
  latency_ms?: number | null
  tools?: number | null
  resources?: number | null
  prompts?: number | null
  detail?: string | null
}

export type DataVolumeMetric = {
  name: string
  value: string
  detail?: string | null
}

export type WorkerLoadInfo = {
  name: string
  active: number
  reserved: number
  concurrency?: number | null
  load_percent?: number | null
  status?: "ok" | "invalid" | "warning" | null
}

export type TaskWaitStats = {
  queued_now: number
  oldest_queue_wait_seconds?: number | null
  avg_wait_seconds_1h?: number | null
  p95_wait_seconds_1h?: number | null
  max_wait_seconds_1h?: number | null
  sample_size_1h: number
  detail?: string | null
}

export type WorkersSnapshot = {
  worker_count: number
  active_tasks: number
  reserved_tasks: number
  queue_depth: number
  total_concurrency?: number | null
  load_percent?: number | null
  workers: WorkerLoadInfo[]
  waits: TaskWaitStats
  status?: "ok" | "invalid" | "warning" | null
  detail?: string | null
}

export type SystemDiagnosis = {
  keys: EnvKeyDiagnosis[]
  disks: DiskUsageInfo[]
  dependencies: DependencyCheck[]
  resources: ResourceMetric[]
  providers: ProviderSnapshot[]
  mcp_servers: McpServerCheck[]
  data_volume: DataVolumeMetric[]
  workers: WorkersSnapshot
  summary: {
    ok: number
    invalid: number
    missing: number
  }
}

export type OrgMember = {
  user_id: string
  email: string
  role: string
  is_super_admin: boolean
  teams?: { id: string; name: string }[]
  cost_ceiling_usd?: number | null
}

export type Team = {
  id: string
  name: string
  is_default: boolean
  oidc_group?: string | null
  member_count: number
  model_count: number
}

export type TeamMember = {
  user_id: string
  email: string
  username?: string | null
  display_name?: string | null
  source: string
}

export type TeamModel = {
  model_id: string
  display_name: string
  provider: string
  model_name: string
  is_enabled: boolean
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
  role: string
  token: string
  expires_at: string
  accepted_at?: string | null
  created_at: string
}

export type UserMemory = {
  id: string
  content: string
  created_at: string
}

export type AgentRole = "owner" | "editor" | "viewer"
export type AgentVisibility = "private" | "shared"
export type AgentSourceStatus = "queued" | "indexing" | "ready" | "failed"
export type AgentSourceKind = "text" | "file" | "url"

export type Agent = {
  id: string
  name: string
  description?: string | null
  preferred_model_id?: string | null
  master_prompt: string
  visibility: AgentVisibility
  is_owner: boolean
  role: AgentRole
  created_at: string
  updated_at: string
}

export type AgentSource = {
  id: string
  kind: AgentSourceKind
  title: string
  summary?: string | null
  url?: string | null
  file_name?: string | null
  content_type?: string | null
  status: AgentSourceStatus
  error_message?: string | null
  created_at: string
  updated_at: string
}

export type AgentShare = {
  user_id: string
  email: string
  role: AgentRole
  created_at: string
  updated_at: string
}

export type AgentShareSuggestion = {
  user_id: string
  email: string
  display_name?: string | null
}

export type PromptVisibility = "private" | "team" | "users" | "space" | "org"

export type PromptSharedUser = {
  user_id: string
  email: string
  display_name?: string | null
}

export type Prompt = {
  id: string
  name: string
  description?: string | null
  body: string
  visibility: PromptVisibility
  team_ids: string[]
  user_ids: string[]
  users: PromptSharedUser[]
  agent_id?: string | null
  is_owner: boolean
  created_at: string
  updated_at: string
}

export type MyTeam = {
  id: string
  name: string
}
