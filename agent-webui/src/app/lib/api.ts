export const API_BASE_URL = import.meta.env.VITE_API_URL || "http://localhost:5501";

export type MessageRole = "system" | "user" | "assistant";

export interface ApiMessage {
  id: string;
  role: MessageRole;
  content: string;
  timestamp: string;
}

export interface ApiInteractionEvent {
  id: string;
  conversation_id: string;
  role: MessageRole;
  event_type: string;
  content: string;
  status: "pending" | "processing" | "completed" | "failed";
  timestamp: string;
  processed_at: string | null;
  error: string | null;
  causation_event_id: string | null;
}

export interface ApiConversationSummary {
  id: string;
  title: string;
  last_message: string;
  updated_at: string;
  message_count: number;
}

export interface ApiConversationDetail {
  id: string;
  title: string;
  updated_at: string;
  messages: ApiMessage[];
}

export interface ApiConversationEventsResponse {
  id: string;
  title: string;
  updated_at: string;
  events: ApiInteractionEvent[];
  background_updates?: ApiBackgroundUpdate[];
}

export interface ApiChatAcceptedResponse {
  conversation_id: string;
  event_id: string;
  accepted_at: string;
}

export interface ApiBackgroundUpdate {
  id: string;
  label: string;
  status: "pending" | "processing" | "completed" | "failed";
  message: string;
  detail: string | null;
  payload: Record<string, unknown> | null;
  timestamp: string;
}

export interface ApiPromptBreakdown {
  system_chars: number;
  user_chars: number;
  assistant_chars: number;
  system_tokens_est: number | null;
  user_tokens_est: number | null;
  assistant_tokens_est: number | null;
}

export interface ApiPerformanceMetrics {
  total_latency_ms: number;
  llm_latency_ms: number;
  ttft_ms: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  response_source: string | null;
  response_policy: string | null;
  llm_involved: boolean;
  tool_observations: Record<string, unknown>[];
  workflow_trace: Record<string, unknown>[];
  retrieved_chunk_count: number;
  retrieved_chunks: ApiRetrievedChunk[];
  prompt_breakdown: ApiPromptBreakdown;
  context_compaction?: {
    applied: boolean;
    trigger_tokens: number;
    estimated_prompt_tokens_before: number;
    estimated_prompt_tokens_after: number;
    dropped_history_messages: number;
  } | null;
}

export interface ApiRetrievedChunk {
  content: string;
  score: number;
  source_id: string;
  source_type: string;
  source_preview: string;
}

export interface ApiPerformanceExchange {
  id: string;
  conversation_id: string;
  created_at: string;
  user_preview: string;
  assistant_preview: string;
  metrics: ApiPerformanceMetrics;
}

export interface ApiPromptComponent {
  id: string;
  name: string;
  file_path: string;
  content: string;
  order: number;
  enabled: boolean;
  is_system: boolean;
  is_custom: boolean;
}

export interface ApiSystemPrompt {
  agent_id: string;
  prompt: string;
  component_count: number;
  profile_name: string;
  is_custom: boolean;
}

export interface ApiDebugLog {
  id: string;
  log_type: string;
  content: Record<string, unknown>;
  duration_ms: number | null;
  token_count: number | null;
  created_at: string;
}

export interface ApiPromptProfile {
  id: string;
  name: string;
  is_active: boolean;
  is_default: boolean;
}

export interface ApiTokenWindowStats {
  total_tokens: number;
  prompt_tokens: number;
  completion_tokens: number;
  exchange_count: number;
  avg_tokens_per_exchange: number;
}

export interface ApiPerformanceSummary {
  exchange_count: number;
  latency_min_ms: number;
  latency_max_ms: number;
  latency_avg_ms: number;
  tokens_day: ApiTokenWindowStats;
  tokens_week: ApiTokenWindowStats;
  tokens_month: ApiTokenWindowStats;
  tokens_all_time: ApiTokenWindowStats;
}

export interface ApiContextSettings {
  max_context_tokens: number;
  max_response_tokens: number;
  compact_trigger_pct: number;
  compact_instructions: string;
  memory_enabled: boolean;
  updated_at: string;
}

export interface ApiMemoryChunk {
  id: string;
  source_type: string;
  source_id: string;
  content: string;
  created_at: string;
  embedding_dimensions: number;
  content_tokens_est: number;
}

export interface ApiMemoryChunkListResponse {
  memory_enabled: boolean;
  chunks: ApiMemoryChunk[];
}

export interface ApiDocumentImport {
  id: string;
  conversation_id: string | null;
  filename: string;
  media_type: string;
  reused_existing?: boolean;
  status: "pending" | "processing" | "completed" | "failed";
  created_at: string;
  processed_at: string | null;
  error: string | null;
}

export interface ApiMcpServer {
  id: string;
  name: string;
  transport: "stdio" | "streamable_http";
  command: string | null;
  args: string[];
  url: string | null;
  env: Record<string, string>;
  enabled: boolean;
  status: string;
  last_error: string | null;
  discovered_tools: { name: string; description?: string; inputSchema?: Record<string, unknown> }[];
  created_at: string;
  updated_at: string;
}

export interface ApiWarmupResponse {
  ok: boolean;
  status: string;
  latency_ms: number;
  model: string;
  warmed_at: string;
}

export interface ApiBaselineCaseResult {
  id: string;
  label: string;
  calls: number;
  input_tokens_est: number;
  ttft_ms?: number | null;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  total_latency_ms: number;
  avg_latency_ms: number;
  min_latency_ms?: number | null;
  max_latency_ms?: number | null;
  completion_time_ms?: number | null;
  per_turn_latency_ms?: number[] | null;
  per_turn_ttft_ms?: number[] | null;
  per_turn_prompt_tokens?: number[] | null;
  per_turn_completion_tokens?: number[] | null;
}

export interface ApiBaselineCategoryResult {
  id: string;
  label: string;
  cases: ApiBaselineCaseResult[];
}

export interface ApiBaselineRunResponse {
  model: string;
  mode: "direct_model" | "end_to_end_aigentos";
  started_at: string;
  completed_at: string;
  duration_ms: number;
  total_calls: number;
  categories: ApiBaselineCategoryResult[];
}

export interface ApiBaselineJobStartResponse {
  job_id: string;
  status: string;
}

export interface ApiBaselineStartRequest {
  enforce_max_response_tokens?: boolean;
  mode?: "direct_model" | "end_to_end_aigentos";
}

export interface ApiBaselineJobStatusResponse {
  job_id: string;
  status: string;
  model: string;
  total_calls: number;
  completed_calls: number;
  current_step: string | null;
  started_at: string;
  updated_at: string;
  completed_at: string | null;
  duration_ms: number | null;
  events: string[];
  error: string | null;
  result: ApiBaselineRunResponse | null;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
  });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const data = await response.json();
      detail = data?.detail || detail;
    } catch {
      // Keep status text fallback.
    }
    throw new Error(detail);
  }

  if (response.status === 204) {
    return null as T;
  }

  const text = await response.text();
  if (!text) {
    return null as T;
  }

  return JSON.parse(text) as T;
}

export const api = {
  async healthCheck() {
    return request<{ status: "ok"; version: string; tenant_id: string; model: string; ollama_base_url: string; embedding_base_url: string; is_warm: boolean }>("/health");
  },

  async listConversations() {
    return request<ApiConversationSummary[]>("/api/conversations");
  },

  async getConversation(conversationId: string) {
    return request<ApiConversationDetail>(`/api/conversations/${conversationId}`);
  },

  async getConversationEvents(conversationId: string) {
    return request<ApiConversationEventsResponse>(`/api/conversations/${conversationId}/events`);
  },

  conversationStreamUrl(conversationId: string) {
    return `${API_BASE_URL}/api/conversations/${conversationId}/stream`;
  },

  async createConversation(title?: string) {
    return request<ApiConversationDetail>("/api/conversations", {
      method: "POST",
      body: JSON.stringify({ title }),
    });
  },

  async sendMessage(message: string, conversationId?: string) {
    return request<ApiChatAcceptedResponse>("/api/chat", {
      method: "POST",
      body: JSON.stringify({
        message,
        conversation_id: conversationId,
      }),
    });
  },

  async importDocument(file: File, conversationId?: string) {
    const form = new FormData();
    form.append("file", file);
    if (conversationId) {
      form.append("conversation_id", conversationId);
    }
    const response = await fetch(`${API_BASE_URL}/api/imports`, {
      method: "POST",
      body: form,
    });
    if (!response.ok) {
      let detail = response.statusText;
      try {
        const data = await response.json();
        detail = data?.detail || detail;
      } catch {
        // Keep status text fallback.
      }
      throw new Error(detail);
    }
    return response.json() as Promise<ApiDocumentImport>;
  },

  async getDocumentImport(documentId: string) {
    return request<ApiDocumentImport>(`/api/imports/${documentId}`);
  },

  async getDocumentImports(limit = 200) {
    return request<ApiDocumentImport[]>(`/api/imports?limit=${limit}`);
  },

  async deleteDocumentImport(documentId: string) {
    await request<null>(`/api/imports/${documentId}`, {
      method: "DELETE",
    });
  },

  async listMcpServers() {
    return request<ApiMcpServer[]>("/api/mcp/servers");
  },

  async createMcpServer(payload: {
    name: string;
    transport: "stdio" | "streamable_http";
    command?: string | null;
    args?: string[];
    url?: string | null;
    env?: Record<string, string>;
    enabled?: boolean;
  }) {
    return request<ApiMcpServer>("/api/mcp/servers", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  async updateMcpServer(serverId: string, payload: {
    name?: string;
    command?: string | null;
    args?: string[];
    url?: string | null;
    env?: Record<string, string>;
    enabled?: boolean;
  }) {
    return request<ApiMcpServer>(`/api/mcp/servers/${serverId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },

  async refreshMcpServer(serverId: string) {
    return request<ApiMcpServer>(`/api/mcp/servers/${serverId}/refresh`, {
      method: "POST",
      body: JSON.stringify({}),
    });
  },

  async deleteMcpServer(serverId: string) {
    await request<null>(`/api/mcp/servers/${serverId}`, {
      method: "DELETE",
    });
  },

  async deleteConversation(conversationId: string) {
    await request<null>(`/api/conversations/${conversationId}`, {
      method: "DELETE",
    });
  },

  async getRecentPerformance(limit = 5) {
    return request<ApiPerformanceExchange[]>(`/api/performance/recent?limit=${limit}`);
  },

  async getSystemPrompt() {
    return request<ApiSystemPrompt>("/api/prompts/system");
  },

  async getPromptComponents() {
    return request<ApiPromptComponent[]>("/api/prompts/components");
  },

  async getOrchestratorPrompts() {
    return request<ApiPromptComponent[]>("/api/prompts/orchestrator");
  },

  async updatePromptComponent(componentId: string, payload: { content?: string; enabled?: boolean }) {
    return request<ApiPromptComponent>(`/api/prompts/components/${componentId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },

  async getPromptProfiles() {
    return request<ApiPromptProfile[]>("/api/prompts/profiles");
  },

  async createPromptProfile(name: string) {
    return request<ApiPromptProfile>("/api/prompts/profiles", {
      method: "POST",
      body: JSON.stringify({ name }),
    });
  },

  async activatePromptProfile(profileId: string) {
    return request<ApiPromptProfile>(`/api/prompts/profiles/${profileId}/activate`, {
      method: "POST",
      body: JSON.stringify({}),
    });
  },

  async resetPrompts() {
    return request<{ ok: boolean; profile_id: string; profile_name: string }>("/api/prompts/reset", {
      method: "POST",
      body: JSON.stringify({}),
    });
  },

  async getDebugLogs(limit = 50) {
    return request<ApiDebugLog[]>(`/api/debug/logs?limit=${limit}`);
  },

  async getPerformanceSummary() {
    return request<ApiPerformanceSummary>("/api/performance/summary");
  },

  async getContextSettings() {
    return request<ApiContextSettings>("/api/prompts/context-settings");
  },

  async updateContextSettings(payload: {
    max_response_tokens?: number;
    compact_trigger_pct?: number;
    compact_instructions?: string;
    memory_enabled?: boolean;
  }) {
    return request<ApiContextSettings>("/api/prompts/context-settings", {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },

  async getMemoryChunks(limit = 200) {
    return request<ApiMemoryChunkListResponse>(`/api/memory/chunks?limit=${limit}`);
  },

  async deleteMemoryChunk(chunkId: string) {
    await request<null>(`/api/memory/chunks/${chunkId}`, {
      method: "DELETE",
    });
  },

  async warmupLLM() {
    return request<ApiWarmupResponse>("/api/llm/warmup", {
      method: "POST",
      body: JSON.stringify({}),
    });
  },

  async deleteAllData() {
    return request<{ ok: boolean; deleted_at: string }>("/api/admin/delete-all-data", {
      method: "POST",
      body: JSON.stringify({ confirm: true }),
    });
  },

  async exportAllData() {
    return request<{
      version: string;
      model: string;
      ollama_base_url: string;
      data: Record<string, unknown>;
    }>("/api/admin/export");
  },

  async runBaseline(payload?: ApiBaselineStartRequest) {
    return request<ApiBaselineRunResponse>("/api/baseline/run", {
      method: "POST",
      body: JSON.stringify(payload ?? { enforce_max_response_tokens: true }),
    });
  },

  async startBaseline(payload?: ApiBaselineStartRequest) {
    return request<ApiBaselineJobStartResponse>("/api/baseline/start", {
      method: "POST",
      body: JSON.stringify(payload ?? { enforce_max_response_tokens: true }),
    });
  },

  async getBaselineStatus(jobId: string) {
    return request<ApiBaselineJobStatusResponse>(`/api/baseline/status/${jobId}`);
  },
};
