export interface ChatAttachment {
  id?: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
  uri?: string;
  previewUrl?: string;
  storage_path?: string;
}

export interface BranchInfo {
  current: number; // 1-based index
  total: number;
  sibling_ids?: number[];
}

export interface CodeBlockData {
  language: string;
  code: string;
}

export interface Message {
  id: number;
  conversation_id: string;
  parent_id: number | null;
  role: 'user' | 'assistant' | 'system';
  content: string;
  thoughts?: string | null;
  model: string;
  token_count?: number | null;
  is_error?: boolean;
  created_at: number;
  updated_at?: number;
  attachments?: ChatAttachment[];
  sibling_index?: number;
  sibling_count?: number;
  sibling_ids?: number[];
  children_ids?: number[];
  branchInfo?: BranchInfo;
  codeBlocks?: CodeBlockData[];
  metadata?: any;
}

export interface ConversationSummary {
  id: string;
  title: string;
  model: string;
  remote_conversation_id?: string | null;
  gem_id?: string | null;
  active_leaf_message_id?: number | null;
  is_pinned: boolean;
  is_archived: boolean;
  created_at: number;
  updated_at: number;
  message_count: number;
}

export interface ConversationDetail extends ConversationSummary {
  active_thread: Message[];
  all_messages?: Message[];
}

export interface ModelOption {
  id: string;
  display_name: string;
  description: string;
  supports_thinking: boolean;
  is_default: boolean;
}

export interface GemOption {
  id: string;
  title: string;
  name?: string;
  description?: string;
  prompt?: string;
  system_prompt?: string;
  icon?: string;
  is_builtin?: boolean;
  created_at?: number;
}

export interface SessionHealthStatus {
  status: 'healthy' | 'refreshing' | 'expired' | 'unauthenticated' | string;
  is_authenticated: boolean;
  has_psid: boolean;
  has_psidts: boolean;
  has_access_token: boolean;
  build_label?: string;
  last_refresh_timestamp?: number;
  next_refresh_seconds?: number;
  account_tier?: string;
  error_message?: string;
}

export type StreamEvent =
  | { type: 'start'; conversation_id: string; user_message_id?: number }
  | { type: 'thought'; delta: string }
  | { type: 'text'; delta: string }
  | { type: 'failover'; from: string; to: string; error_code?: number; reason?: string; timestamp?: number }
  | { type: 'citation' | 'grounding'; metadata: any }
  | { type: 'media'; metadata: any }
  | { type: 'saved'; assistant_message_id?: number }
  | { type: 'done'; metadata?: any }
  | { type: 'error'; error: string };

export interface SearchResultItem {
  conversation_id: string;
  conversation_title: string;
  message_id: number;
  role: string;
  content_snippet: string;
  score: number;
  created_at: number;
}

export interface AccountItem {
  id: string;
  name: string;
  email: string;
  tier: 'free' | 'pro' | 'ultra' | string;
  priority: number;
  is_active: boolean;
  health_status: 'healthy' | 'refreshing' | 'cooldown' | 'expired' | 'unauthenticated' | string;
  has_psid: boolean;
  has_psidts: boolean;
  has_access_token: boolean;
  build_label?: string;
  last_refresh_timestamp?: number;
  next_refresh_seconds?: number;
  cooldown_until?: number | null;
  cooldown_models?: Record<string, number>;
}

export interface FailoverConfig {
  enabled: boolean;
  strategy: string;
  cooldown_duration_seconds: number;
  fallback_chain: string[];
  notify_client_on_failover: boolean;
}

export interface ModelTelemetry {
  model_id: string;
  requests: number;
  tokens_est: number;
  quota_status: string;
  cooldown_remaining_seconds: number;
}

export interface AccountLimitsData {
  account_id: string;
  name: string;
  tier: string;
  policy_string: string;
  health_status: string;
  cooldown: {
    is_active: boolean;
    reset_timestamp: number | null;
    remaining_seconds: number;
    affected_models: string[];
  };
  rolling_1h: {
    requests: number;
    tokens_est: number;
    task_calls: number;
  };
  rolling_24h: {
    requests: number;
    prompt_tokens_est: number;
    completion_tokens_est: number;
    thinking_tokens_est: number;
    total_tokens_est: number;
    task_calls: number;
    quota_errors: number;
  };
  per_model_24h: Record<string, ModelTelemetry>;
  thresholds: {
    daily_request_threshold: number;
    daily_request_percentage: number;
    daily_token_threshold: number;
    daily_token_percentage: number;
    hourly_request_threshold?: number;
    warning_status: string;
  };
}

export interface LimitsResponse {
  timestamp: number;
  active_account_id: string;
  global_summary_24h: {
    total_requests: number;
    total_tokens_est: number;
    task_mode_invocations: number;
    failover_events_count: number;
  };
  accounts: AccountLimitsData[];
  failover_state: {
    enabled: boolean;
    strategy: string;
    fallback_chain: string[];
    recent_failovers: Array<{
      timestamp: number;
      from_account_name: string;
      to_account_name: string;
      model: string;
      error_code: number;
    }>;
  };
}

export interface NotebookSource {
  id: string;
  title: string;
  source_type: number; // 1=text, 2=url, 3=pdf/doc, 4=youtube
  url?: string | null;
  summary: string;
  token_count: number;
  created_at: number;
}

export interface NotebookNote {
  id: string;
  title: string;
  content: string;
  created_at: number;
  updated_at: number;
}

export interface NotebookInfo {
  id: string;
  title: string;
  created_at: number;
  updated_at: number;
  source_count: number;
  emoji?: string;
}

export interface NotebookDetail extends NotebookInfo {
  sources: NotebookSource[];
  notes: NotebookNote[];
}

export interface NotebookAttachContextResponse {
  formatted_context: string;
  estimated_tokens: number;
  notebook_id: string;
  notebook_title: string;
  source_count: number;
  note_count: number;
}


