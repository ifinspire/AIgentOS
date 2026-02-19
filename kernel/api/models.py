from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


Role = Literal["system", "user", "assistant"]


class HealthResponse(BaseModel):
    status: Literal["ok"]
    tenant_id: str
    model: str
    ollama_base_url: str
    is_warm: bool


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=10000)
    conversation_id: str | None = None


class MessageResponse(BaseModel):
    id: str
    role: Role
    content: str
    timestamp: datetime


class ConversationSummary(BaseModel):
    id: str
    title: str
    last_message: str
    updated_at: datetime
    message_count: int


class ConversationDetail(BaseModel):
    id: str
    title: str
    updated_at: datetime
    messages: list[MessageResponse]


class PromptBreakdown(BaseModel):
    system_chars: int
    user_chars: int
    assistant_chars: int
    system_tokens_est: int | None = None
    user_tokens_est: int | None = None
    assistant_tokens_est: int | None = None


class ContextCompactionMetrics(BaseModel):
    applied: bool
    trigger_tokens: int
    estimated_prompt_tokens_before: int
    estimated_prompt_tokens_after: int
    dropped_history_messages: int


class PerformanceMetrics(BaseModel):
    total_latency_ms: int
    llm_latency_ms: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    prompt_breakdown: PromptBreakdown
    context_compaction: ContextCompactionMetrics | None = None


class PerformanceExchange(BaseModel):
    id: str
    conversation_id: str
    created_at: datetime
    user_preview: str
    assistant_preview: str
    metrics: PerformanceMetrics


class PromptComponentResponse(BaseModel):
    id: str
    name: str
    file_path: str
    content: str
    order: int
    enabled: bool
    is_system: bool
    is_custom: bool


class SystemPromptResponse(BaseModel):
    agent_id: str
    prompt: str
    component_count: int
    profile_name: str
    is_custom: bool


class DebugLogResponse(BaseModel):
    id: str
    log_type: str
    content: dict
    duration_ms: int | None = None
    token_count: int | None = None
    created_at: datetime


class WarmupResponse(BaseModel):
    ok: bool
    status: str
    latency_ms: int
    model: str
    warmed_at: datetime


class PromptProfileResponse(BaseModel):
    id: str
    name: str
    is_active: bool
    is_default: bool


class PromptProfileCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)


class PromptComponentUpdateRequest(BaseModel):
    content: str | None = None
    enabled: bool | None = None


class PromptResetResponse(BaseModel):
    ok: bool
    profile_id: str
    profile_name: str


class ContextSettingsResponse(BaseModel):
    max_context_tokens: int
    max_response_tokens: int
    compact_trigger_pct: float
    compact_instructions: str
    updated_at: datetime


class ContextSettingsUpdateRequest(BaseModel):
    max_context_tokens: int | None = Field(default=None, ge=256, le=262144)
    max_response_tokens: int | None = Field(default=None, ge=16, le=262144)
    compact_trigger_pct: float | None = Field(default=None, ge=0.1, le=1.0)
    compact_instructions: str | None = None


class TokenWindowStats(BaseModel):
    total_tokens: int
    prompt_tokens: int
    completion_tokens: int
    exchange_count: int
    avg_tokens_per_exchange: float


class PerformanceSummaryResponse(BaseModel):
    exchange_count: int
    latency_min_ms: int
    latency_max_ms: int
    latency_avg_ms: float
    tokens_day: TokenWindowStats
    tokens_week: TokenWindowStats
    tokens_month: TokenWindowStats
    tokens_all_time: TokenWindowStats


class ChatResponse(BaseModel):
    conversation_id: str
    user_message: MessageResponse
    assistant_message: MessageResponse
    performance: PerformanceMetrics


class CreateConversationRequest(BaseModel):
    title: str | None = None


class DeleteAllDataRequest(BaseModel):
    confirm: bool = False


class DeleteAllDataResponse(BaseModel):
    ok: bool
    deleted_at: datetime


class BaselineCaseResult(BaseModel):
    id: str
    label: str
    calls: int
    input_tokens_est: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    total_latency_ms: int
    avg_latency_ms: float
    min_latency_ms: int | None = None
    max_latency_ms: int | None = None
    per_turn_latency_ms: list[int] | None = None
    per_turn_prompt_tokens: list[int] | None = None
    per_turn_completion_tokens: list[int] | None = None


class BaselineCategoryResult(BaseModel):
    id: str
    label: str
    cases: list[BaselineCaseResult]


class BaselineRunResponse(BaseModel):
    model: str
    started_at: datetime
    completed_at: datetime
    duration_ms: int
    total_calls: int
    categories: list[BaselineCategoryResult]


class BaselineJobStartResponse(BaseModel):
    job_id: str
    status: str


class BaselineStartRequest(BaseModel):
    enforce_max_response_tokens: bool = True


class BaselineJobStatusResponse(BaseModel):
    job_id: str
    status: str
    model: str
    total_calls: int
    completed_calls: int
    current_step: str | None = None
    started_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    duration_ms: int | None = None
    events: list[str]
    error: str | None = None
    result: BaselineRunResponse | None = None
