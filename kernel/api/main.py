from __future__ import annotations

from pathlib import Path
import time
from datetime import datetime, timezone
import asyncio
import math
import re
import uuid

from fastapi import FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import httpx

from .llm import ChatMessageIn, OllamaClient
from .models import (
    BaselineJobStartResponse,
    BaselineStartRequest,
    BaselineJobStatusResponse,
    BaselineCaseResult,
    BaselineCategoryResult,
    BaselineRunResponse,
    ChatRequest,
    ChatResponse,
    ConversationDetail,
    ConversationSummary,
    CreateConversationRequest,
    ContextSettingsResponse,
    ContextSettingsUpdateRequest,
    DeleteAllDataRequest,
    DeleteAllDataResponse,
    DebugLogResponse,
    HealthResponse,
    PerformanceMetrics,
    PerformanceExchange,
    PerformanceSummaryResponse,
    PromptComponentUpdateRequest,
    PromptProfileCreateRequest,
    PromptProfileResponse,
    PromptResetResponse,
    PromptComponentResponse,
    PromptBreakdown,
    SystemPromptResponse,
    TokenWindowStats,
    WarmupResponse,
    MessageResponse,
)
from .prompts import compose_system_prompt, load_prompt_bundle, load_prompt_components
from .settings import get_settings
from .storage import ChatStore


settings = get_settings()
repo_root = Path(__file__).resolve().parents[2]
prompt_bundle = load_prompt_bundle(repo_root=repo_root, default_agent_id=settings.default_agent_id)
store = ChatStore(settings.chat_db_path)
llm_client = OllamaClient(settings.ollama_base_url, settings.ollama_model)
_warmup_lock = asyncio.Lock()
_warmup_completed_at: datetime | None = None
_baseline_jobs: dict[str, dict] = {}


def _tenant_id() -> str:
    return settings.aigent_tenant_id


def _default_compact_trigger() -> float:
    agent_file = repo_root / "agent-prompts" / "basic" / "agent.yaml"
    if not agent_file.exists():
        return 0.9
    try:
        import yaml
        config = yaml.safe_load(agent_file.read_text(encoding="utf-8")) or {}
        strategy = config.get("context_strategy") or {}
        compact_pct = float(strategy.get("pruning_threshold", 0.9))
        return compact_pct
    except Exception:
        return 0.9


def _get_context_settings():
    compact_pct = _default_compact_trigger()
    current = store.ensure_context_settings(
        _tenant_id(),
        settings.ollama_context_window,
        settings.ollama_max_response_tokens,
        compact_pct,
    )
    if (
        current.max_context_tokens != settings.ollama_context_window
        or current.max_response_tokens != settings.ollama_max_response_tokens
    ):
        current = store.update_context_settings(
            tenant_id=_tenant_id(),
            max_context_tokens=settings.ollama_context_window,
            max_response_tokens=settings.ollama_max_response_tokens,
        )
    return current


def _effective_prompt_components():
    defaults = load_prompt_components(repo_root=repo_root)
    profile = store.get_active_prompt_profile(_tenant_id())
    overrides = store.get_prompt_overrides(profile.id)
    merged = []
    for item in defaults:
        override = overrides.get(item.id)
        if override is None:
            merged.append(item)
            continue
        merged.append(
            item.__class__(
                id=item.id,
                name=item.name,
                content=override["content"] if override.get("content") is not None else item.content,
                order=item.order,
                enabled=override["enabled"] if override.get("enabled") is not None else item.enabled,
                is_system=item.is_system,
            )
        )
    return profile, merged


def _window_stats(window: tuple[int, int, int, int]) -> TokenWindowStats:
    total_tokens, prompt_tokens, completion_tokens, exchange_count = window
    avg = 0.0 if exchange_count == 0 else float(total_tokens) / float(exchange_count)
    return TokenWindowStats(
        total_tokens=total_tokens,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        exchange_count=exchange_count,
        avg_tokens_per_exchange=avg,
    )

app = FastAPI(
    title="AIgentOS Kernel API",
    version="0.1.0",
    description="Minimal OSS chat API wired to Ollama",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    )


def _estimate_tokens_for_messages(messages: list[ChatMessageIn]) -> int:
    char_count = sum(len(m.content or "") for m in messages)
    return max(1, math.ceil(char_count / 4))


def _estimate_tokens_for_text(text: str) -> int:
    return max(1, math.ceil(len(text or "") / 4))


def _extract_visible_assistant_text(text: str) -> str:
    no_think = re.sub(r"<think>[\s\S]*?</think>", "", text or "", flags=re.IGNORECASE)
    no_tags = re.sub(r"</?think>", "", no_think, flags=re.IGNORECASE)
    return no_tags.strip()


def _should_refresh_conversation_summary(exchange_count: int) -> bool:
    return exchange_count == 1 or (exchange_count > 0 and exchange_count % 10 == 0)


async def _refresh_conversation_summary(conversation_id: str) -> None:
    messages = store.get_messages(conversation_id)
    exchange_count = sum(1 for m in messages if m.role == "assistant")
    if not _should_refresh_conversation_summary(exchange_count):
        return

    dialogue: list[str] = []
    for m in messages:
        if m.role == "user":
            dialogue.append(f"User: {m.content.strip()}")
        elif m.role == "assistant":
            visible = _extract_visible_assistant_text(m.content)
            dialogue.append(f"Assistant: {visible}")

    transcript = "\n".join(line for line in dialogue if line).strip()
    if not transcript:
        return
    if len(transcript) > 8000:
        transcript = transcript[-8000:]

    summary_messages = [
        ChatMessageIn(
            role="system",
            content=(
                "Generate a short conversation title for a sidebar list. "
                "Return one plain line, 4-10 words, no markdown, no quotes."
            ),
        ),
        ChatMessageIn(
            role="user",
            content=f"Conversation transcript:\n{transcript}",
        ),
    ]
    try:
        result = await llm_client.chat(summary_messages, max_tokens=64)
    except Exception:
        return

    title = _extract_visible_assistant_text(result.content).splitlines()[0].strip() if result.content else ""
    title = title.strip(" \"'`")
    if not title:
        return
    if len(title) > 96:
        title = title[:96].rstrip()
    store.update_conversation_title(conversation_id, title)


def _build_user_payload(target_tokens: int, seed: str) -> str:
    fragment = (
        f"{seed}. Keep this coherent, factual, and concise. "
        "Include clear constraints, concrete details, and specific wording. "
    )
    text = ""
    while _estimate_tokens_for_text(text) < target_tokens:
        text += fragment
    return text


def _build_system_payload(target_tokens: int, seed: str) -> str:
    fragment = (
        f"{seed}. Preserve instruction fidelity, avoid hallucination, and remain concise. "
        "Use explicit constraints and deterministic formatting cues. "
    )
    text = ""
    while _estimate_tokens_for_text(text) < target_tokens:
        text += fragment
    return text


async def _run_single_turn_case(
    effective_prompt: str,
    case_id: str,
    label: str,
    task_instruction: str,
    input_tokens: int,
    max_response_tokens: int | None = None,
    on_progress=None,
) -> BaselineCaseResult:
    if on_progress is not None:
        on_progress(label, 0)
    user_payload = _build_user_payload(input_tokens, f"{label} input")
    messages = [
        ChatMessageIn(role="system", content=effective_prompt),
        ChatMessageIn(role="system", content=task_instruction),
        ChatMessageIn(role="user", content=user_payload),
    ]
    started = time.perf_counter()
    completion = await llm_client.chat(messages, max_tokens=max_response_tokens)
    latency_ms = int((time.perf_counter() - started) * 1000)
    prompt_tokens = completion.prompt_tokens if completion.prompt_tokens is not None else _estimate_tokens_for_messages(messages)
    completion_tokens = completion.completion_tokens if completion.completion_tokens is not None else _estimate_tokens_for_text(completion.content)
    total_tokens = completion.total_tokens if completion.total_tokens is not None else prompt_tokens + completion_tokens
    if on_progress is not None:
        on_progress(label, 1)
    return BaselineCaseResult(
        id=case_id,
        label=label,
        calls=1,
        input_tokens_est=_estimate_tokens_for_text(user_payload),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        total_latency_ms=latency_ms,
        avg_latency_ms=float(latency_ms),
        min_latency_ms=latency_ms,
        max_latency_ms=latency_ms,
    )


async def _run_system_prompt_pressure_case(
    effective_prompt: str,
    case_id: str,
    label: str,
    system_tokens: int,
    user_tokens: int,
    max_response_tokens: int | None = None,
    on_progress=None,
) -> BaselineCaseResult:
    if on_progress is not None:
        on_progress(label, 0)
    system_pressure = _build_system_payload(system_tokens, f"{label} system context")
    user_payload = _build_user_payload(user_tokens, f"{label} user input")
    messages = [
        ChatMessageIn(role="system", content=effective_prompt),
        ChatMessageIn(role="system", content=system_pressure),
        ChatMessageIn(role="user", content=user_payload),
    ]
    started = time.perf_counter()
    completion = await llm_client.chat(messages, max_tokens=max_response_tokens)
    latency_ms = int((time.perf_counter() - started) * 1000)
    prompt_tokens = completion.prompt_tokens if completion.prompt_tokens is not None else _estimate_tokens_for_messages(messages)
    completion_tokens = completion.completion_tokens if completion.completion_tokens is not None else _estimate_tokens_for_text(completion.content)
    total_tokens = completion.total_tokens if completion.total_tokens is not None else prompt_tokens + completion_tokens
    if on_progress is not None:
        on_progress(label, 1)
    return BaselineCaseResult(
        id=case_id,
        label=label,
        calls=1,
        input_tokens_est=_estimate_tokens_for_text(user_payload),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        total_latency_ms=latency_ms,
        avg_latency_ms=float(latency_ms),
        min_latency_ms=latency_ms,
        max_latency_ms=latency_ms,
    )


async def _run_multi_turn_case(
    effective_prompt: str,
    case_id: str,
    label: str,
    task_instruction: str,
    turn_targets: list[int],
    max_response_tokens: int | None = None,
    on_progress=None,
) -> BaselineCaseResult:
    messages: list[ChatMessageIn] = [
        ChatMessageIn(role="system", content=effective_prompt),
        ChatMessageIn(role="system", content=task_instruction),
    ]
    prompt_total = 0
    completion_total = 0
    token_total = 0
    latency_total = 0
    per_turn_latency_ms: list[int] = []
    per_turn_prompt_tokens: list[int] = []
    per_turn_completion_tokens: list[int] = []
    input_total = 0

    for idx, target in enumerate(turn_targets):
        if on_progress is not None:
            on_progress(f"{label} (turn {idx + 1}/{len(turn_targets)})", 0)
        user_payload = _build_user_payload(target, f"{label} turn {idx + 1}")
        input_total += _estimate_tokens_for_text(user_payload)
        messages.append(ChatMessageIn(role="user", content=user_payload))
        started = time.perf_counter()
        completion = await llm_client.chat(messages, max_tokens=max_response_tokens)
        latency_ms = int((time.perf_counter() - started) * 1000)
        latency_total += latency_ms
        per_turn_latency_ms.append(latency_ms)
        prompt_tokens = completion.prompt_tokens if completion.prompt_tokens is not None else _estimate_tokens_for_messages(messages)
        completion_tokens = completion.completion_tokens if completion.completion_tokens is not None else _estimate_tokens_for_text(completion.content)
        total_tokens = completion.total_tokens if completion.total_tokens is not None else prompt_tokens + completion_tokens
        per_turn_prompt_tokens.append(prompt_tokens)
        per_turn_completion_tokens.append(completion_tokens)
        prompt_total += prompt_tokens
        completion_total += completion_tokens
        token_total += total_tokens
        messages.append(ChatMessageIn(role="assistant", content=completion.content))
        if on_progress is not None:
            on_progress(f"{label} (turn {idx + 1}/{len(turn_targets)})", 1)

    calls = len(turn_targets)
    return BaselineCaseResult(
        id=case_id,
        label=label,
        calls=calls,
        input_tokens_est=input_total,
        prompt_tokens=prompt_total,
        completion_tokens=completion_total,
        total_tokens=token_total,
        total_latency_ms=latency_total,
        avg_latency_ms=(float(latency_total) / float(calls)) if calls > 0 else 0.0,
        min_latency_ms=min(per_turn_latency_ms) if per_turn_latency_ms else None,
        max_latency_ms=max(per_turn_latency_ms) if per_turn_latency_ms else None,
        per_turn_latency_ms=per_turn_latency_ms,
        per_turn_prompt_tokens=per_turn_prompt_tokens,
        per_turn_completion_tokens=per_turn_completion_tokens,
    )


def _make_baseline_status(job_id: str) -> BaselineJobStatusResponse:
    job = _baseline_jobs[job_id]
    return BaselineJobStatusResponse(
        job_id=job_id,
        status=job["status"],
        model=job["model"],
        total_calls=job["total_calls"],
        completed_calls=job["completed_calls"],
        current_step=job.get("current_step"),
        started_at=job["started_at"],
        updated_at=job["updated_at"],
        completed_at=job.get("completed_at"),
        duration_ms=job.get("duration_ms"),
        events=job.get("events", []),
        error=job.get("error"),
        result=job.get("result"),
    )


def _append_baseline_event(job_id: str, message: str) -> None:
    job = _baseline_jobs[job_id]
    events = job.setdefault("events", [])
    events.append(message)
    if len(events) > 100:
        del events[:-100]
    job["updated_at"] = datetime.now(timezone.utc)


def _baseline_progress(job_id: str, step: str, completed_inc: int) -> None:
    job = _baseline_jobs[job_id]
    job["current_step"] = step
    if completed_inc > 0:
        job["completed_calls"] = min(job["total_calls"], int(job["completed_calls"]) + completed_inc)
        _append_baseline_event(job_id, f"Completed: {step}")
    else:
        _append_baseline_event(job_id, f"Running: {step}")
    job["updated_at"] = datetime.now(timezone.utc)


def _baseline_total_calls() -> int:
    # simple QA 3 + summarization 4 + multi-turn 20 + extraction 1 + system prompt pressure 6
    return 34


async def _execute_baseline(job_id: str, enforce_max_response_tokens: bool) -> BaselineRunResponse:
    started_at = datetime.now(timezone.utc)
    started = time.perf_counter()
    _, effective_components = _effective_prompt_components()
    effective_prompt = compose_system_prompt(effective_components)
    context_settings = _get_context_settings()
    baseline_max_tokens = context_settings.max_response_tokens if enforce_max_response_tokens else None

    simple_qa_cases = [
        await _run_single_turn_case(
            effective_prompt,
            case_id="qa_100",
            label="Simple Q/A (100 user tokens)",
            task_instruction="Answer directly in 6-10 concise sentences.",
            input_tokens=100,
            max_response_tokens=baseline_max_tokens,
            on_progress=lambda step, inc: _baseline_progress(job_id, step, inc),
        ),
        await _run_single_turn_case(
            effective_prompt,
            case_id="qa_250",
            label="Simple Q/A (250 user tokens)",
            task_instruction="Answer directly in 6-10 concise sentences.",
            input_tokens=250,
            max_response_tokens=baseline_max_tokens,
            on_progress=lambda step, inc: _baseline_progress(job_id, step, inc),
        ),
        await _run_single_turn_case(
            effective_prompt,
            case_id="qa_500",
            label="Simple Q/A (500 user tokens)",
            task_instruction="Answer directly in 6-10 concise sentences.",
            input_tokens=500,
            max_response_tokens=baseline_max_tokens,
            on_progress=lambda step, inc: _baseline_progress(job_id, step, inc),
        ),
    ]

    summarization_cases = [
        await _run_single_turn_case(
            effective_prompt,
            case_id="sum_200",
            label="Summarization (200 user tokens)",
            task_instruction="Summarize the content in 5 bullet points.",
            input_tokens=200,
            max_response_tokens=baseline_max_tokens,
            on_progress=lambda step, inc: _baseline_progress(job_id, step, inc),
        ),
        await _run_single_turn_case(
            effective_prompt,
            case_id="sum_500",
            label="Summarization (500 user tokens)",
            task_instruction="Summarize the content in 5 bullet points.",
            input_tokens=500,
            max_response_tokens=baseline_max_tokens,
            on_progress=lambda step, inc: _baseline_progress(job_id, step, inc),
        ),
        await _run_single_turn_case(
            effective_prompt,
            case_id="sum_1000",
            label="Summarization (1000 user tokens)",
            task_instruction="Summarize the content in 8 bullet points.",
            input_tokens=1000,
            max_response_tokens=baseline_max_tokens,
            on_progress=lambda step, inc: _baseline_progress(job_id, step, inc),
        ),
        await _run_single_turn_case(
            effective_prompt,
            case_id="sum_2000",
            label="Summarization (2000 user tokens)",
            task_instruction="Summarize the content in 10 bullet points.",
            input_tokens=2000,
            max_response_tokens=baseline_max_tokens,
            on_progress=lambda step, inc: _baseline_progress(job_id, step, inc),
        ),
    ]

    multi_turn_targets = [50 + ((i * 17) % 151) for i in range(20)]
    multi_turn_cases = [
        await _run_multi_turn_case(
            effective_prompt,
            case_id="mt_20x_50_200",
            label="20-turn conversation (50-200 user tokens/turn)",
            task_instruction=(
                "Maintain consistency across turns and preserve key decisions while answering each turn concisely."
            ),
            turn_targets=multi_turn_targets,
            max_response_tokens=baseline_max_tokens,
            on_progress=lambda step, inc: _baseline_progress(job_id, step, inc),
        )
    ]

    extraction_cases = [
        await _run_single_turn_case(
            effective_prompt,
            case_id="extract_400",
            label="Structured Extraction (400 user tokens)",
            task_instruction=(
                "Extract entities into JSON with keys: people, organizations, dates, locations, and actions."
            ),
            input_tokens=400,
            max_response_tokens=baseline_max_tokens,
            on_progress=lambda step, inc: _baseline_progress(job_id, step, inc),
        )
    ]

    system_prompt_targets = [200, 500, 1000, 2000, 5000, 10000]
    system_prompt_cases: list[BaselineCaseResult] = []
    for idx, target in enumerate(system_prompt_targets):
        user_tokens = 100 + ((idx * 37) % 201)
        system_prompt_cases.append(
            await _run_system_prompt_pressure_case(
                effective_prompt,
                case_id=f"sys_{target}",
                label=f"System Prompt Pressure ({target} system tokens)",
                system_tokens=target,
                user_tokens=user_tokens,
                max_response_tokens=baseline_max_tokens,
                on_progress=lambda step, inc: _baseline_progress(job_id, step, inc),
            )
        )

    categories = [
        BaselineCategoryResult(id="simple_qa", label="Simple Q/A", cases=simple_qa_cases),
        BaselineCategoryResult(id="summarization", label="Summarization Tasks", cases=summarization_cases),
        BaselineCategoryResult(id="multi_turn", label="20-Turn Conversation", cases=multi_turn_cases),
        BaselineCategoryResult(id="structured_extraction", label="Structured Extraction (Extra)", cases=extraction_cases),
        BaselineCategoryResult(id="system_prompt_pressure", label="System Prompt Pressure", cases=system_prompt_cases),
    ]

    completed_at = datetime.now(timezone.utc)
    duration_ms = int((time.perf_counter() - started) * 1000)
    total_calls = sum(case.calls for category in categories for case in category.cases)
    return BaselineRunResponse(
        model=settings.ollama_model,
        started_at=started_at,
        completed_at=completed_at,
        duration_ms=duration_ms,
        total_calls=total_calls,
        categories=categories,
    )


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        tenant_id=settings.aigent_tenant_id,
        model=settings.ollama_model,
        ollama_base_url=settings.ollama_base_url,
        is_warm=_warmup_completed_at is not None,
    )


@app.post("/api/llm/warmup", response_model=WarmupResponse)
async def llm_warmup() -> WarmupResponse:
    global _warmup_completed_at
    started = time.perf_counter()
    async with _warmup_lock:
        status_text = "warmed"
        if _warmup_completed_at is not None:
            status_text = "already_warmed"
        else:
            try:
                await llm_client.chat(
                    [
                        ChatMessageIn(
                            role="user",
                            content="hello",
                        )
                    ],
                    max_tokens=min(64, settings.ollama_max_response_tokens),
                )
                _warmup_completed_at = datetime.now(timezone.utc)
            except httpx.HTTPStatusError as exc:
                raise HTTPException(
                    status_code=502,
                    detail=f"Ollama warmup failed with status {exc.response.status_code}",
                ) from exc
            except httpx.HTTPError as exc:
                raise HTTPException(status_code=502, detail="Failed to connect to Ollama for warmup") from exc
            except RuntimeError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc

    latency_ms = int((time.perf_counter() - started) * 1000)
    return WarmupResponse(
        ok=True,
        status=status_text,
        latency_ms=latency_ms,
        model=settings.ollama_model,
        warmed_at=_warmup_completed_at or datetime.now(timezone.utc),
    )


@app.post("/api/admin/delete-all-data", response_model=DeleteAllDataResponse)
async def delete_all_data(payload: DeleteAllDataRequest) -> DeleteAllDataResponse:
    global _warmup_completed_at
    if not payload.confirm:
        raise HTTPException(status_code=400, detail="Confirmation required")
    store.delete_all_data()
    _warmup_completed_at = None
    return DeleteAllDataResponse(ok=True, deleted_at=datetime.now(timezone.utc))


@app.get("/api/admin/export")
async def export_all_data() -> dict:
    snapshot = store.export_all_data(_tenant_id())
    return {
        "version": "aigentos-export-v1",
        "model": settings.ollama_model,
        "ollama_base_url": settings.ollama_base_url,
        "data": snapshot,
    }


@app.get("/api/prompts/context-settings", response_model=ContextSettingsResponse)
async def get_context_settings() -> ContextSettingsResponse:
    current = _get_context_settings()
    return ContextSettingsResponse(
        max_context_tokens=current.max_context_tokens,
        max_response_tokens=current.max_response_tokens,
        compact_trigger_pct=current.compact_trigger_pct,
        compact_instructions=current.compact_instructions,
        updated_at=current.updated_at,
    )


@app.patch("/api/prompts/context-settings", response_model=ContextSettingsResponse)
async def update_context_settings(payload: ContextSettingsUpdateRequest) -> ContextSettingsResponse:
    current = store.update_context_settings(
        tenant_id=_tenant_id(),
        max_context_tokens=settings.ollama_context_window,
        max_response_tokens=payload.max_response_tokens,
        compact_trigger_pct=payload.compact_trigger_pct,
        compact_instructions=payload.compact_instructions,
    )
    return ContextSettingsResponse(
        max_context_tokens=current.max_context_tokens,
        max_response_tokens=current.max_response_tokens,
        compact_trigger_pct=current.compact_trigger_pct,
        compact_instructions=current.compact_instructions,
        updated_at=current.updated_at,
    )


@app.get("/api/prompts/system", response_model=SystemPromptResponse)
async def get_system_prompt() -> SystemPromptResponse:
    profile, components = _effective_prompt_components()
    bundle = load_prompt_bundle(repo_root=repo_root, default_agent_id=settings.default_agent_id)
    prompt = compose_system_prompt(components)
    overrides = store.get_prompt_overrides(profile.id)
    return SystemPromptResponse(
        agent_id=bundle.agent_id,
        prompt=prompt,
        component_count=len(components),
        profile_name=profile.name,
        is_custom=len(overrides) > 0,
    )


@app.get("/api/prompts/components", response_model=list[PromptComponentResponse])
async def get_prompt_components() -> list[PromptComponentResponse]:
    profile, components = _effective_prompt_components()
    overrides = store.get_prompt_overrides(profile.id)
    return [
            PromptComponentResponse(
                id=item.id,
                name=item.name,
                file_path=item.file_path,
                content=item.content,
                order=item.order,
                enabled=item.enabled,
                is_system=item.is_system,
            is_custom=item.id in overrides,
        )
        for item in components
    ]


@app.get("/api/prompts/profiles", response_model=list[PromptProfileResponse])
async def get_prompt_profiles() -> list[PromptProfileResponse]:
    profiles = store.list_prompt_profiles(_tenant_id())
    return [
        PromptProfileResponse(
            id=item.id,
            name=item.name,
            is_active=item.is_active,
            is_default=item.is_default,
        )
        for item in profiles
    ]


@app.post("/api/prompts/profiles", response_model=PromptProfileResponse)
async def create_prompt_profile(payload: PromptProfileCreateRequest) -> PromptProfileResponse:
    created = store.create_prompt_profile(_tenant_id(), payload.name)
    return PromptProfileResponse(
        id=created.id,
        name=created.name,
        is_active=created.is_active,
        is_default=created.is_default,
    )


@app.post("/api/prompts/profiles/{profile_id}/activate", response_model=PromptProfileResponse)
async def activate_prompt_profile(profile_id: str) -> PromptProfileResponse:
    ok = store.activate_prompt_profile(_tenant_id(), profile_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Prompt profile not found")
    active = store.get_active_prompt_profile(_tenant_id())
    return PromptProfileResponse(
        id=active.id,
        name=active.name,
        is_active=active.is_active,
        is_default=active.is_default,
    )


@app.patch("/api/prompts/components/{component_id}", response_model=PromptComponentResponse)
async def update_prompt_component(component_id: str, payload: PromptComponentUpdateRequest) -> PromptComponentResponse:
    default_ids = {c.id for c in load_prompt_components(repo_root=repo_root)}
    if component_id not in default_ids:
        raise HTTPException(status_code=404, detail="Prompt component not found")
    profile = store.get_active_prompt_profile(_tenant_id())
    store.upsert_prompt_override(
        profile_id=profile.id,
        component_id=component_id,
        content=payload.content,
        enabled=payload.enabled,
    )
    _, components = _effective_prompt_components()
    component = next((item for item in components if item.id == component_id), None)
    if component is None:
        raise HTTPException(status_code=404, detail="Prompt component not found")
    return PromptComponentResponse(
        id=component.id,
        name=component.name,
        file_path=component.file_path,
        content=component.content,
        order=component.order,
        enabled=component.enabled,
        is_system=component.is_system,
        is_custom=True,
    )


@app.post("/api/prompts/reset", response_model=PromptResetResponse)
async def reset_prompts() -> PromptResetResponse:
    profile = store.get_active_prompt_profile(_tenant_id())
    store.reset_prompt_profile(profile.id)
    return PromptResetResponse(ok=True, profile_id=profile.id, profile_name=profile.name)


@app.post("/api/conversations", response_model=ConversationDetail)
async def create_conversation(payload: CreateConversationRequest) -> ConversationDetail:
    conversation_id, updated_at = store.create_conversation(title=payload.title)
    return ConversationDetail(
        id=conversation_id,
        title=payload.title or "New Conversation",
        updated_at=updated_at,
        messages=[],
    )


@app.get("/api/conversations", response_model=list[ConversationSummary])
async def list_conversations() -> list[ConversationSummary]:
    conversations = store.list_conversations()
    return [
        ConversationSummary(
            id=item.id,
            title=item.title,
            last_message=item.last_message,
            updated_at=item.updated_at,
            message_count=item.message_count,
        )
        for item in conversations
    ]


@app.get("/api/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(conversation_id: str) -> ConversationDetail:
    detail = store.get_conversation_detail(conversation_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    title, updated_at, messages = detail
    return ConversationDetail(
        id=conversation_id,
        title=title,
        updated_at=updated_at,
        messages=[
            MessageResponse(
                id=m.id,
                role=m.role,  # type: ignore[arg-type]
                content=m.content,
                timestamp=m.created_at,
            )
            for m in messages
        ],
    )


@app.delete("/api/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(conversation_id: str) -> Response:
    deleted = store.delete_conversation(conversation_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/api/performance/recent", response_model=list[PerformanceExchange])
async def recent_performance(limit: int = 5) -> list[PerformanceExchange]:
    safe_limit = max(1, min(limit, 50))
    rows = store.list_recent_performance_exchanges(safe_limit)
    return [
        PerformanceExchange(
            id=row.id,
            conversation_id=row.conversation_id,
            created_at=row.created_at,
            user_preview=row.user_preview,
            assistant_preview=row.assistant_preview,
            metrics=PerformanceMetrics(
                total_latency_ms=row.total_latency_ms,
                llm_latency_ms=row.llm_latency_ms,
                prompt_tokens=row.prompt_tokens,
                completion_tokens=row.completion_tokens,
                total_tokens=row.total_tokens,
                prompt_breakdown=PromptBreakdown(
                    system_chars=row.system_chars,
                    user_chars=row.user_chars,
                    assistant_chars=row.assistant_chars,
                    system_tokens_est=row.system_tokens_est,
                    user_tokens_est=row.user_tokens_est,
                    assistant_tokens_est=row.assistant_tokens_est,
                ),
            ),
        )
        for row in rows
    ]


@app.get("/api/performance/summary", response_model=PerformanceSummaryResponse)
async def performance_summary() -> PerformanceSummaryResponse:
    summary = store.summarize_performance()
    return PerformanceSummaryResponse(
        exchange_count=summary["exchange_count"],
        latency_min_ms=summary["latency_min_ms"],
        latency_max_ms=summary["latency_max_ms"],
        latency_avg_ms=summary["latency_avg_ms"],
        tokens_day=_window_stats(summary["tokens_day"]),
        tokens_week=_window_stats(summary["tokens_week"]),
        tokens_month=_window_stats(summary["tokens_month"]),
        tokens_all_time=_window_stats(summary["tokens_all_time"]),
    )


async def _run_baseline_background(job_id: str) -> None:
    try:
        job = _baseline_jobs[job_id]
        result = await _execute_baseline(job_id, enforce_max_response_tokens=bool(job.get("enforce_max_response_tokens", True)))
        job = _baseline_jobs[job_id]
        job["status"] = "completed"
        job["result"] = result
        job["completed_at"] = datetime.now(timezone.utc)
        job["duration_ms"] = result.duration_ms
        job["current_step"] = "Completed"
        job["updated_at"] = datetime.now(timezone.utc)
        _append_baseline_event(job_id, "Baseline run completed")
    except Exception as exc:
        job = _baseline_jobs[job_id]
        job["status"] = "failed"
        msg = str(exc).strip()
        job["error"] = f"{exc.__class__.__name__}: {msg}" if msg else exc.__class__.__name__
        job["completed_at"] = datetime.now(timezone.utc)
        job["updated_at"] = datetime.now(timezone.utc)
        _append_baseline_event(job_id, f"Baseline failed: {job['error']}")


@app.post("/api/baseline/start", response_model=BaselineJobStartResponse)
async def start_baseline(payload: BaselineStartRequest | None = None) -> BaselineJobStartResponse:
    payload = payload or BaselineStartRequest()
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    _baseline_jobs[job_id] = {
        "status": "running",
        "model": settings.ollama_model,
        "total_calls": _baseline_total_calls(),
        "completed_calls": 0,
        "current_step": "Initializing",
        "started_at": now,
        "updated_at": now,
        "completed_at": None,
        "duration_ms": None,
        "events": ["Baseline run started"],
        "error": None,
        "result": None,
        "enforce_max_response_tokens": payload.enforce_max_response_tokens,
    }
    if payload.enforce_max_response_tokens:
        _append_baseline_event(job_id, "Mode: enforcing max response tokens")
    else:
        _append_baseline_event(job_id, "Mode: no max response token cap")
    asyncio.create_task(_run_baseline_background(job_id))
    return BaselineJobStartResponse(job_id=job_id, status="running")


@app.get("/api/baseline/status/{job_id}", response_model=BaselineJobStatusResponse)
async def baseline_status(job_id: str) -> BaselineJobStatusResponse:
    if job_id not in _baseline_jobs:
        raise HTTPException(status_code=404, detail="Baseline job not found")
    return _make_baseline_status(job_id)


@app.post("/api/baseline/run", response_model=BaselineRunResponse)
async def run_baseline(payload: BaselineStartRequest | None = None) -> BaselineRunResponse:
    payload = payload or BaselineStartRequest()
    job_id = f"direct-{uuid.uuid4()}"
    now = datetime.now(timezone.utc)
    _baseline_jobs[job_id] = {
        "status": "running",
        "model": settings.ollama_model,
        "total_calls": _baseline_total_calls(),
        "completed_calls": 0,
        "current_step": "Initializing",
        "started_at": now,
        "updated_at": now,
        "completed_at": None,
        "duration_ms": None,
        "events": ["Baseline run started (direct)"],
        "error": None,
        "result": None,
        "enforce_max_response_tokens": payload.enforce_max_response_tokens,
    }
    if payload.enforce_max_response_tokens:
        _append_baseline_event(job_id, "Mode: enforcing max response tokens")
    else:
        _append_baseline_event(job_id, "Mode: no max response token cap")
    result = await _execute_baseline(job_id, enforce_max_response_tokens=payload.enforce_max_response_tokens)
    _baseline_jobs[job_id]["status"] = "completed"
    _baseline_jobs[job_id]["result"] = result
    _baseline_jobs[job_id]["completed_at"] = datetime.now(timezone.utc)
    _baseline_jobs[job_id]["duration_ms"] = result.duration_ms
    _baseline_jobs[job_id]["updated_at"] = datetime.now(timezone.utc)
    return result


@app.get("/api/debug/logs", response_model=list[DebugLogResponse])
async def debug_logs(limit: int = 50) -> list[DebugLogResponse]:
    safe_limit = max(1, min(limit, 200))
    rows = store.list_recent_performance_exchanges(safe_limit)
    result: list[DebugLogResponse] = []
    for row in rows:
        result.append(
            DebugLogResponse(
                id=row.id,
                log_type="llm_exchange",
                duration_ms=row.total_latency_ms,
                token_count=row.total_tokens,
                created_at=row.created_at,
                content={
                    "conversation_id": row.conversation_id,
                    "user_preview": row.user_preview,
                    "assistant_preview": row.assistant_preview,
                    "metrics": {
                        "total_latency_ms": row.total_latency_ms,
                        "llm_latency_ms": row.llm_latency_ms,
                        "prompt_tokens": row.prompt_tokens,
                        "completion_tokens": row.completion_tokens,
                        "total_tokens": row.total_tokens,
                        "prompt_breakdown": {
                            "system_chars": row.system_chars,
                            "user_chars": row.user_chars,
                            "assistant_chars": row.assistant_chars,
                            "system_tokens_est": row.system_tokens_est,
                            "user_tokens_est": row.user_tokens_est,
                            "assistant_tokens_est": row.assistant_tokens_est,
                        },
                    },
                },
            )
        )
    return result


def _allocate_estimated_tokens(total: int | None, system_chars: int, user_chars: int, assistant_chars: int) -> tuple[int | None, int | None, int | None]:
    if total is None:
        return None, None, None
    total_chars = system_chars + user_chars + assistant_chars
    if total_chars <= 0:
        return 0, 0, 0
    system_est = round(total * system_chars / total_chars)
    user_est = round(total * user_chars / total_chars)
    assistant_est = total - system_est - user_est
    return system_est, user_est, assistant_est


@app.post("/api/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest) -> ChatResponse:
    if payload.conversation_id:
        if not store.ensure_conversation(payload.conversation_id):
            raise HTTPException(status_code=404, detail="Conversation not found")
        conversation_id = payload.conversation_id
    else:
        conversation_id, _ = store.create_conversation()

    user_message = store.add_message(conversation_id, "user", payload.message)
    store.maybe_set_title_from_message(conversation_id, payload.message)

    _, effective_components = _effective_prompt_components()
    effective_prompt = compose_system_prompt(effective_components)
    context_settings = _get_context_settings()

    history = store.get_messages(conversation_id)
    history_messages = [
        ChatMessageIn(role=m.role, content=m.content)
        for m in history
        if m.role in {"user", "assistant"}
    ]
    llm_messages = [ChatMessageIn(role="system", content=effective_prompt), *history_messages]

    est_prompt_tokens_before = _estimate_tokens_for_messages(llm_messages)
    compact_threshold = int(context_settings.max_context_tokens * context_settings.compact_trigger_pct)
    compaction_applied = False
    dropped_history_messages = 0
    if context_settings.compact_instructions.strip() and est_prompt_tokens_before >= compact_threshold:
        llm_messages.insert(
            1,
            ChatMessageIn(
                role="system",
                content=context_settings.compact_instructions.strip(),
            ),
        )
        compaction_applied = True

    while len(llm_messages) > 2 and _estimate_tokens_for_messages(llm_messages) > context_settings.max_context_tokens:
        # Drop the oldest non-system message first.
        llm_messages.pop(2)
        dropped_history_messages += 1

    est_prompt_tokens_after = _estimate_tokens_for_messages(llm_messages)

    system_chars = sum(len(m.content) for m in llm_messages if m.role == "system")
    user_chars = sum(len(m.content) for m in llm_messages if m.role == "user")
    assistant_chars = sum(len(m.content) for m in llm_messages if m.role == "assistant")

    try:
        started = time.perf_counter()
        completion = await llm_client.chat(
            llm_messages,
            max_tokens=context_settings.max_response_tokens,
        )
        total_latency_ms = int((time.perf_counter() - started) * 1000)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Ollama request failed with status {exc.response.status_code}",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Failed to connect to Ollama") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    assistant_message = store.add_message(conversation_id, "assistant", completion.content)

    system_tokens_est, user_tokens_est, assistant_tokens_est = _allocate_estimated_tokens(
        completion.prompt_tokens,
        system_chars,
        user_chars,
        assistant_chars,
    )

    store.add_performance_exchange(
        conversation_id=conversation_id,
        user_preview=payload.message.strip()[:160],
        assistant_preview=completion.content.strip()[:160],
        total_latency_ms=total_latency_ms,
        llm_latency_ms=completion.latency_ms,
        prompt_tokens=completion.prompt_tokens,
        completion_tokens=completion.completion_tokens,
        total_tokens=completion.total_tokens,
        system_chars=system_chars,
        user_chars=user_chars,
        assistant_chars=assistant_chars,
        system_tokens_est=system_tokens_est,
        user_tokens_est=user_tokens_est,
        assistant_tokens_est=assistant_tokens_est,
    )

    await _refresh_conversation_summary(conversation_id)

    return ChatResponse(
        conversation_id=conversation_id,
        user_message=MessageResponse(
            id=user_message.id,
            role="user",
            content=user_message.content,
            timestamp=user_message.created_at,
        ),
        assistant_message=MessageResponse(
            id=assistant_message.id,
            role="assistant",
            content=assistant_message.content,
            timestamp=assistant_message.created_at,
        ),
        performance=PerformanceMetrics(
            total_latency_ms=total_latency_ms,
            llm_latency_ms=completion.latency_ms,
            prompt_tokens=completion.prompt_tokens,
            completion_tokens=completion.completion_tokens,
            total_tokens=completion.total_tokens,
            prompt_breakdown=PromptBreakdown(
                system_chars=system_chars,
                user_chars=user_chars,
                assistant_chars=assistant_chars,
                system_tokens_est=system_tokens_est,
                user_tokens_est=user_tokens_est,
                assistant_tokens_est=assistant_tokens_est,
            ),
            context_compaction={
                "applied": compaction_applied or dropped_history_messages > 0,
                "trigger_tokens": compact_threshold,
                "estimated_prompt_tokens_before": est_prompt_tokens_before,
                "estimated_prompt_tokens_after": est_prompt_tokens_after,
                "dropped_history_messages": dropped_history_messages,
            },
        ),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(_, exc: Exception):
    return JSONResponse(status_code=500, content={"detail": str(exc)})
