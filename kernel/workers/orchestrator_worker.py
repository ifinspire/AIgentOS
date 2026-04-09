from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
import re
import sqlite3
import time

from kernel.api.llm import ChatMessageIn, OllamaClient, OllamaEmbeddingClient
from kernel.api.mcp import McpClientError, call_tool as call_mcp_tool, discover_tools as discover_mcp_tools, ensure_default_markitdown_server, discover_enabled_servers
from kernel.api.settings import get_settings
from kernel.api.storage import (
    ChatStore,
    StoredInteractionEvent,
    StoredMcpServer,
    StoredOrchestrationEvent,
)
from kernel.shared.metrics import allocate_estimated_tokens, estimate_tokens_for_messages
from kernel.shared.text import chunk_text, extract_visible_text, preview_text


settings = get_settings()
store = ChatStore(settings.chat_db_path)
llm_client = OllamaClient(settings.ollama_base_url, settings.ollama_model)
embedding_client = OllamaEmbeddingClient(settings.embedding_base_url, settings.embedding_model)

_ROUTING_PROMPT_PATH = Path(__file__).resolve().parents[2] / "agent-prompts" / "orchestrator" / "routing.md"
_MATH_SUBAGENT_PROMPT_PATH = Path(__file__).resolve().parents[2] / "agent-prompts" / "orchestrator" / "math_subagent.md"

_SAFE_NUMBER_NODE_TYPES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.USub,
    ast.UAdd,
    ast.Load,
)

_MAX_CALC_OPERAND = 1_000_000


def _ensure_default_markitdown_mcp_server() -> None:
    ensure_default_markitdown_server(store, settings)


async def _discover_enabled_mcp_servers_on_startup() -> None:
    _ensure_default_markitdown_mcp_server()
    await discover_enabled_servers(store)


def _safe_number_node(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return isinstance(node.value, (int, float)) and abs(node.value) <= _MAX_CALC_OPERAND
    return isinstance(node, _SAFE_NUMBER_NODE_TYPES)


def _calculate_expression(expression: str) -> str | None:
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        return None
    if any(not _safe_number_node(node) for node in ast.walk(tree)):
        return None
    try:
        value = eval(compile(tree, "<calc>", "eval"), {"__builtins__": {}}, {})
    except Exception:
        return None
    return str(value)


def _load_routing_prompt() -> str:
    if _ROUTING_PROMPT_PATH.exists():
        return _ROUTING_PROMPT_PATH.read_text(encoding="utf-8").strip()
    return ""


def _available_mcp_servers() -> list[StoredMcpServer]:
    return [
        server
        for server in store.list_mcp_servers()
        if server.enabled and server.status in {"configured", "connected"} and server.discovered_tools
    ]


def _mcp_server_ref(server: StoredMcpServer) -> str:
    """Generate a stable, human-readable slug from the server name.

    Used as the middle segment in MCP tool identifiers (mcp::<ref>::<tool>).
    Slugified name is deterministic and readable in routing prompts. Truncated
    to 24 chars to keep tool identifiers compact.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", server.name.lower()).strip("-")
    return slug[:24] or server.id[:8]


def _routing_prompt_with_mcp_tools() -> str:
    base = _load_routing_prompt()
    servers = _available_mcp_servers()
    if not servers:
        return base
    lines = [base, "", "Additional MCP tools discovered at runtime:", ""]
    for server in servers:
        for tool in server.discovered_tools:
            tool_name = tool["name"] if isinstance(tool, dict) else str(tool)
            ref = f"mcp::{_mcp_server_ref(server)}::{tool_name}"
            description = tool.get("description", "") if isinstance(tool, dict) else ""
            input_schema = tool.get("inputSchema") if isinstance(tool, dict) else None
            line = f"- {ref}"
            if description:
                line += f" — {description}"
            else:
                line += f" — MCP tool `{tool_name}` from server `{server.name}`."
            lines.append(line)
            if isinstance(input_schema, dict):
                props = input_schema.get("properties", {})
                required = set(input_schema.get("required", []))
                if props:
                    param_parts = []
                    for pname, pschema in props.items():
                        ptype = pschema.get("type", "string") if isinstance(pschema, dict) else "string"
                        pdesc = pschema.get("description", "") if isinstance(pschema, dict) else ""
                        req_marker = " (required)" if pname in required else ""
                        entry = f"`{pname}`: {ptype}{req_marker}"
                        if pdesc:
                            entry += f" — {pdesc}"
                        param_parts.append(entry)
                    lines.append(f"  Params: {'; '.join(param_parts)}")
    lines.extend(
        [
            "",
            "For MCP tools, output the exact tool identifier in TOOL and put the tool arguments object in PARAMS.",
            "Only route to an MCP tool when the user clearly needs that tool and the tool result materially helps answer the request.",
        ]
    )
    return "\n".join(lines).strip()


def _load_math_subagent_prompt() -> str:
    if _MATH_SUBAGENT_PROMPT_PATH.exists():
        return _MATH_SUBAGENT_PROMPT_PATH.read_text(encoding="utf-8").strip()
    return ""


def _recent_math_context(conversation_id: str, current_event_id: str) -> str:
    events = store.get_conversation_events(conversation_id)
    lines: list[str] = []
    for event in events:
        if event.id == current_event_id:
            continue
        if event.status != "completed":
            continue
        if event.role not in {"user", "assistant", "tool"}:
            continue
        content = event.content.strip().replace("\n", " ")
        if not content:
            continue
        if len(content) > 240:
            content = f"{content[:240].rstrip()}..."
        lines.append(f"{event.role.upper()}: {content}")
    return "\n".join(lines[-8:])


def _recent_routing_context(conversation_id: str, current_event_id: str) -> str:
    """Provide recent turn context to the router so follow-ups are interpreted in context."""
    events = store.get_conversation_events(conversation_id)
    lines: list[str] = []
    for event in events:
        if event.id == current_event_id:
            continue
        if event.status != "completed":
            continue
        if event.role not in {"user", "assistant", "tool"}:
            continue
        content = event.content.strip().replace("\n", " ")
        if not content:
            continue
        if len(content) > 240:
            content = f"{content[:240].rstrip()}..."
        lines.append(f"{event.role.upper()}: {content}")
    return "\n".join(lines[-10:])


async def _run_math_subagent(user_message: str, conversation_id: str, current_event_id: str) -> tuple[dict, str]:
    """Run the math subagent LLM to resolve arithmetic intent.

    Returns (resolved_payload, raw_text) where raw_text is the verbatim
    LLM output for observability.
    """
    prompt = _load_math_subagent_prompt()
    if not prompt:
        return {"action": "none"}, ""
    recent_context = _recent_math_context(conversation_id, current_event_id)
    try:
        result = await llm_client.chat(
            [
                ChatMessageIn(role="system", content=prompt),
                ChatMessageIn(
                    role="user",
                    content=(
                        f"Latest user message:\n{user_message}\n\n"
                        f"Recent conversation context:\n{recent_context or '(none)'}"
                    ),
                ),
            ],
            max_tokens=min(128, settings.ollama_max_response_tokens),
        )
        text = extract_visible_text(result.content).strip()
        payload = json.loads(text) if text else {"action": "none"}
        if isinstance(payload, dict):
            return payload, text
    except Exception:
        pass
    return {"action": "none"}, ""


async def _route_tools_with_llm(
    user_message: str,
    conversation_id: str | None = None,
    current_event_id: str | None = None,
) -> tuple[str | None, dict, str]:
    """Call the routing LLM to decide if and which tool to dispatch.

    Returns (tool_name, params, raw_text) where tool_name is None if no tool
    applies. raw_text is the verbatim LLM output for observability.
    Falls back to (None, {}, "") on any error so the dialogue path is never blocked.
    """
    routing_prompt = _routing_prompt_with_mcp_tools()
    if not routing_prompt:
        return None, {}, ""
    recent_context = ""
    if conversation_id and current_event_id:
        recent_context = _recent_routing_context(conversation_id, current_event_id)
    try:
        result = await llm_client.chat(
            [
                ChatMessageIn(role="system", content=routing_prompt),
                ChatMessageIn(
                    role="user",
                    content=(
                        f"Latest user message:\n{user_message}\n\n"
                        f"Recent conversation context:\n{recent_context or '(none)'}"
                    ),
                ),
            ],
            max_tokens=min(192, settings.ollama_max_response_tokens),
        )
        text = extract_visible_text(result.content).strip()
    except Exception:
        return None, {}, ""
    if not text or text.upper().startswith("NONE"):
        return None, {}, text or ""
    tool_name: str | None = None
    params: dict = {}
    for line in text.splitlines():
        uline = line.upper()
        if uline.startswith("TOOL:"):
            tool_name = line[5:].strip().lower()
        elif uline.startswith("PARAMS:"):
            raw = line[7:].strip()
            try:
                params = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                pass
    return tool_name, params, text


def _mcp_tool_names(server: StoredMcpServer) -> set[str]:
    """Extract the set of tool names from a server's discovered_tools list."""
    names: set[str] = set()
    for tool in server.discovered_tools:
        if isinstance(tool, dict):
            name = str(tool.get("name", "")).strip()
        else:
            name = str(tool).strip()
        if name:
            names.add(name)
    return names


def _find_mcp_server_for_tool(tool_name: str) -> tuple[StoredMcpServer | None, str | None]:
    if not tool_name.startswith("mcp::"):
        return None, None
    _, _, remainder = tool_name.partition("mcp::")
    server_ref, sep, discovered_tool = remainder.partition("::")
    if not sep or not server_ref or not discovered_tool:
        return None, None
    for server in store.list_mcp_servers():
        if _mcp_server_ref(server) != server_ref:
            continue
        if not server.enabled:
            return None, None
        if discovered_tool not in _mcp_tool_names(server):
            return None, None
        return server, discovered_tool
    return None, None


async def _ensure_markitdown_server_ready() -> StoredMcpServer:
    _ensure_default_markitdown_mcp_server()
    server = store.find_mcp_server_by_name("MarkItDown MCP")
    if server is None or not server.enabled:
        raise RuntimeError("MarkItDown MCP server is not registered or enabled")
    tool_name = settings.markitdown_mcp_tool_name
    if tool_name in _mcp_tool_names(server) and server.status in {"configured", "connected"}:
        return server
    try:
        tools = await discover_mcp_tools(
            {
                "transport": server.transport,
                "command": server.command,
                "args": server.args,
                "url": server.url,
                "env": server.env,
            }
        )
        updated = store.set_mcp_server_discovery_result(server.id, discovered_tools=tools, status="connected", last_error=None)
        server = updated or server
    except McpClientError as exc:
        store.set_mcp_server_error(server.id, error=str(exc), status="error")
        raise RuntimeError(f"MarkItDown MCP discovery failed: {exc}") from exc
    if tool_name not in _mcp_tool_names(server):
        raise RuntimeError(f'MarkItDown MCP tool "{tool_name}" is not available')
    return server


def _markitdown_file_uri(stored_path: str) -> str:
    filename = Path(stored_path).name
    base = settings.markitdown_mcp_uploads_dir.rstrip("/")
    return f"file://{base}/{filename}"


def _extract_markdown_from_mcp_result(result: dict) -> str:
    """Extract Markdown text from a MarkItDown MCP tool call result.

    Tries multiple response shapes in order of preference:
    1. structuredContent with known text keys (markdown, text_content, text, content)
    2. content array with text items (standard MCP tool result format)
    3. raw string fallback
    """
    structured = result.get("structured")
    if isinstance(structured, dict):
        for key in ("markdown", "text_content", "text", "content"):
            value = structured.get(key)
            if isinstance(value, str) and value.strip():
                return value
        available_keys = list(structured.keys())
        raise RuntimeError(
            f"MarkItDown MCP structuredContent present but no text found. "
            f"Available keys: {available_keys}"
        )
    content = result.get("content")
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text)
        if parts:
            return "\n".join(parts).strip()
        raise RuntimeError(
            f"MarkItDown MCP content array present ({len(content)} items) but no text items found. "
            f"Item types: {[type(i).__name__ for i in content[:5]]}"
        )
    raw = result.get("raw")
    if isinstance(raw, str) and raw.strip():
        return raw
    available_keys = list(result.keys())
    raise RuntimeError(
        f"MarkItDown MCP returned no recognizable Markdown content. "
        f"Response keys: {available_keys}"
    )


async def _store_chunks_for_source(source_type: str, source_id: str, content: str) -> bool:
    chunks = chunk_text(content)
    if not chunks:
        return False
    embedded: list[tuple[str, list[float]]] = []
    for chunk in chunks:
        try:
            embedding = await embedding_client.embed(chunk)
        except Exception:
            continue
        embedded.append((chunk, embedding))
    if not embedded:
        return False
    store.upsert_rag_chunks(source_type, source_id, embedded)
    return True


async def _summarize_memory_if_needed() -> None:
    compactable_sources = ["interaction_event", "turn_memory", "memory_summary"]
    total_chunks = store.count_rag_chunks(source_types=compactable_sources)
    if total_chunks <= settings.memory_chunk_limit:
        return
    oldest = store.list_oldest_rag_chunks(
        limit=max(settings.memory_compaction_batch_size, total_chunks - settings.memory_chunk_limit),
        source_types=compactable_sources,
    )
    if len(oldest) < 2:
        return
    joined = "\n".join(f"- {chunk.content}" for chunk in oldest if chunk.content.strip()).strip()
    if not joined:
        return
    if len(joined) > 6000:
        joined = joined[:6000]
    try:
        result = await llm_client.chat(
            [
                ChatMessageIn(
                    role="system",
                    content=(
                        "Summarize older memory into compact reusable notes. Preserve stable facts, decisions, "
                        "preferences, unresolved tasks, and corrections. Use concise bullet points."
                    ),
                ),
                ChatMessageIn(role="user", content=joined),
            ],
            max_tokens=min(256, settings.ollama_max_response_tokens),
        )
        summary_text = extract_visible_text(result.content).strip()
    except Exception:
        summary_text = "\n".join(f"- {preview_text(chunk.content, max_chars=180)}" for chunk in oldest[:12]).strip()
    if not summary_text:
        return
    stored = await _store_chunks_for_source("memory_summary", f"memory-summary-{int(time.time())}", summary_text)
    if stored:
        store.delete_rag_chunks([chunk.id for chunk in oldest])


async def _memory_candidates(user_message: str, assistant_message: str) -> list[dict]:
    try:
        result = await llm_client.chat(
            [
                ChatMessageIn(
                    role="system",
                    content=(
                        "Decide if this turn should be written to long-lived memory. "
                        "Return either NONE or up to three lines in the format kind::content. "
                        "Allowed kinds: summary, preference, decision, fact, open_loop. "
                        "Only include durable information likely to matter later."
                    ),
                ),
                ChatMessageIn(
                    role="user",
                    content=f"User:\n{user_message}\n\nAssistant:\n{assistant_message}",
                ),
            ],
            max_tokens=min(192, settings.ollama_max_response_tokens),
        )
        text = extract_visible_text(result.content).strip()
    except Exception:
        text = ""
    if not text or text.upper().startswith("NONE"):
        return []
    candidates: list[dict] = []
    for line in text.splitlines():
        if "::" not in line:
            continue
        kind, content = line.split("::", 1)
        cleaned_kind = kind.strip().lower()
        cleaned_content = content.strip()
        if not cleaned_content:
            continue
        candidates.append({"kind": cleaned_kind or "summary", "content": cleaned_content})
    return candidates[:3]


async def _generate_import_started_message(filename: str) -> str:
    try:
        result = await llm_client.chat(
            [
                ChatMessageIn(role="system", content="You are a helpful assistant. Write a single short natural sentence only — no lists, no extra context."),
                ChatMessageIn(role="user", content=f'A file named "{filename}" was just uploaded. Acknowledge receipt and let the user know you are processing it. One sentence, under 20 words.'),
            ],
            max_tokens=48,
        )
        text = extract_visible_text(result.content).strip()
        return text if text else f'"{filename}" received — processing it now.'
    except Exception:
        return f'"{filename}" received — processing it now.'


async def _generate_import_done_message(filename: str) -> str:
    try:
        result = await llm_client.chat(
            [
                ChatMessageIn(role="system", content="You are a helpful assistant. Write a single short natural sentence only — no lists, no extra context."),
                ChatMessageIn(role="user", content=f'A file named "{filename}" has finished processing and is indexed. Let the user know it is ready and they can ask questions about it. One sentence, under 20 words.'),
            ],
            max_tokens=48,
        )
        text = extract_visible_text(result.content).strip()
        return text if text else f'"{filename}" is ready — you can ask me questions about it.'
    except Exception:
        return f'"{filename}" is ready — you can ask me questions about it.'


async def _generate_import_failed_message(filename: str, error: str) -> str:
    try:
        result = await llm_client.chat(
            [
                ChatMessageIn(role="system", content="You are a helpful assistant. Write a single short natural sentence only — no lists, no extra context."),
                ChatMessageIn(role="user", content=f'Processing the file "{filename}" failed with error: {error}. Briefly let the user know you could not process it and suggest they try re-uploading or a different format. One sentence, under 25 words.'),
            ],
            max_tokens=48,
        )
        text = extract_visible_text(result.content).strip()
        return text if text else f'I wasn\'t able to process "{filename}" — try re-uploading or a different format.'
    except Exception:
        return f'I wasn\'t able to process "{filename}" — try re-uploading or a different format.'


def _direct_tool_response(user_message: str, tool_observations: list[dict]) -> str | None:
    if len(tool_observations) != 1:
        return None
    observation = tool_observations[0]
    tool_name = str(observation.get("tool", "")).strip().lower()
    if tool_name == "count_occurrences":
        needle = str(observation.get("needle", "")).strip()
        haystack = str(observation.get("haystack", "")).strip()
        result = str(observation.get("result", "")).strip()
        if needle and haystack and result:
            suffix = "" if result == "1" else "s"
            return f'There {"is" if result == "1" else "are"} {result} occurrence{suffix} of the letter "{needle}" in "{haystack}".'
        return None
    if tool_name in {"math_subagent", "calculate"}:
        result = str(observation.get("result", "")).strip()
        if not result:
            return None
        reference = str(observation.get("reference", "")).strip()
        expression = str(observation.get("expression", "")).strip()
        unit = str(observation.get("unit", "")).strip()
        label = reference or expression or "Computed result"
        return f"{label}: {result}{f' {unit}' if unit else ''}."
    return None


def _tool_workflow_trace(
    *,
    tool_observations: list[dict],
    response_source: str,
    llm_involved: bool,
) -> list[dict]:
    routed_label = tool_observations[0].get("label", tool_observations[0].get("tool", "tool")) if tool_observations else "tool"
    return [
        {
            "step": "dialogue_ingest",
            "layer": "interaction",
            "where": "api",
            "llm_involved": False,
            "detail": "User message accepted and queued for async processing.",
        },
        {
            "step": "orchestrator_tool_routing",
            "layer": "orchestrator",
            "where": "orchestrator-worker",
            "llm_involved": True,
            "detail": f"Tool dispatched: {routed_label}. Dialogue worker remained idle for this turn.",
        },
        {
            "step": "tool_execution",
            "layer": "tool",
            "where": "orchestrator-worker",
            "llm_involved": False,
            "detail": f"{routed_label} executed and returned a result.",
        },
        {
            "step": "response_generation",
            "layer": "orchestrator",
            "where": "orchestrator-worker",
            "llm_involved": llm_involved,
            "detail": "Response generated by the orchestrator from tool output."
            if llm_involved
            else "Response emitted directly from tool output without calling the dialogue model.",
        },
        {
            "step": "post_turn_finalize",
            "layer": "orchestrator",
            "where": "orchestrator-worker",
            "llm_involved": False,
            "detail": "Queued post-turn memory selection and compaction work.",
        },
    ]


async def _synthesize_tool_response(user_message: str, tool_observations: list[dict]) -> tuple[str, int, int | None, int | None, int | None]:
    messages = [
        ChatMessageIn(
            role="system",
            content=(
                "You are the AIgentOS orchestrator. Use the provided tool results to answer the user directly. "
                "Be accurate, concise, and do not invent tool output."
            ),
        ),
        ChatMessageIn(
            role="system",
            content="Tool results:\n" + json.dumps(tool_observations, ensure_ascii=True, indent=2),
        ),
        ChatMessageIn(role="user", content=user_message),
    ]
    result = await llm_client.chat(
        messages,
        max_tokens=min(512, settings.ollama_max_response_tokens),
    )
    return (
        result.content,
        result.latency_ms,
        result.ttft_ms,
        result.prompt_tokens,
        result.completion_tokens,
        result.total_tokens,
    )


async def _complete_tool_routed_turn(
    *,
    user_event: StoredInteractionEvent,
    tool_observations: list[dict],
    response_source: str,
    response_policy: str,
    llm_involved: bool,
) -> None:
    direct_response = _direct_tool_response(user_event.content, tool_observations)
    if llm_involved or direct_response is None:
        completion_content, llm_latency_ms, ttft_ms, prompt_tokens, completion_tokens, total_tokens = await _synthesize_tool_response(
            user_event.content,
            tool_observations,
        )
    else:
        completion_content = direct_response
        llm_latency_ms = 0
        ttft_ms = 0
        prompt_tokens = None
        completion_tokens = estimate_tokens_for_messages([ChatMessageIn(role="assistant", content=completion_content)])
        total_tokens = completion_tokens

    assistant_event = store.create_interaction_event(
        conversation_id=user_event.conversation_id,
        role="assistant",
        content=completion_content,
        status="completed",
        causation_event_id=user_event.id,
    )
    store.mark_event_completed(user_event.id)
    store.create_orchestration_event(
        event_type="finalize_turn",
        label="Writing memory",
        detail="Queued post-turn orchestration",
        status="pending",
        conversation_id=user_event.conversation_id,
        parent_event_id=user_event.id,
        payload={"assistant_event_id": assistant_event.id},
    )

    prompt_breakdown_messages = [ChatMessageIn(role="assistant", content=completion_content)]
    if llm_involved:
        prompt_breakdown_messages = [
            ChatMessageIn(
                role="system",
                content=(
                    "You are the AIgentOS orchestrator. Use the provided tool results to answer the user directly. "
                    "Be accurate, concise, and do not invent tool output."
                ),
            ),
            ChatMessageIn(role="system", content="Tool results:\n" + json.dumps(tool_observations, ensure_ascii=True, indent=2)),
            ChatMessageIn(role="user", content=user_event.content),
        ]
    system_chars = sum(len(m.content) for m in prompt_breakdown_messages if m.role == "system")
    user_chars = sum(len(m.content) for m in prompt_breakdown_messages if m.role == "user")
    assistant_chars = len(completion_content)
    system_tokens_est, user_tokens_est, assistant_tokens_est = allocate_estimated_tokens(
        prompt_tokens,
        system_chars,
        user_chars,
        assistant_chars,
    )
    store.add_performance_exchange(
        conversation_id=user_event.conversation_id,
        user_event_id=user_event.id,
        assistant_event_id=assistant_event.id,
        user_preview=user_event.content.strip()[:160],
        assistant_preview=completion_content.strip()[:160],
        total_latency_ms=llm_latency_ms,
        llm_latency_ms=llm_latency_ms,
        ttft_ms=ttft_ms,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        response_source=response_source,
        response_policy=response_policy,
        llm_involved=llm_involved,
        tool_observations=tool_observations,
        workflow_trace=_tool_workflow_trace(
            tool_observations=tool_observations,
            response_source=response_source,
            llm_involved=llm_involved,
        ),
        retrieved_chunks=[],
        system_chars=system_chars,
        user_chars=user_chars,
        assistant_chars=assistant_chars,
        system_tokens_est=system_tokens_est,
        user_tokens_est=user_tokens_est,
        assistant_tokens_est=assistant_tokens_est,
    )


async def _process_prepare_turn(event: StoredOrchestrationEvent) -> None:
    """Route the user turn before any dialogue generation begins.

    Direct-dialogue turns are handed off to the dialogue worker via turn_contexts.
    Tool-routed turns are completed here so the dialogue worker never falls
    through to the main model for a turn that should have been handled by tools.
    """
    if not event.parent_event_id or not event.conversation_id:
        store.update_orchestration_event(event.id, status="failed", error="missing_parent_event")
        return
    user_event = store.get_interaction_event(event.parent_event_id)
    if user_event is None:
        store.update_orchestration_event(event.id, status="failed", error="parent_event_not_found")
        return

    store.update_orchestration_event(event.id, status="processing", detail="Routing turn to tools")

    tool_name, params, routing_raw = await _route_tools_with_llm(
        user_event.content,
        conversation_id=event.conversation_id,
        current_event_id=user_event.id,
    )

    tool_observations: list[dict] = []
    subagent_raw: str = ""

    if tool_name == "math_subagent":
        resolved, subagent_raw = await _run_math_subagent(user_event.content, event.conversation_id, user_event.id)
        if str(resolved.get("action", "")).strip().lower() == "calculate":
            expression = str(resolved.get("expression", "")).strip()
            unit = str(resolved.get("unit", "")).strip()
            reference = str(resolved.get("reference", "")).strip()
            result = _calculate_expression(expression) if expression else None
            if result is not None:
                tool_observations.append(
                    {
                        "tool": "math_subagent",
                        "label": "Math/Calculator",
                        "expression": expression,
                        "result": result,
                        "unit": unit,
                        "reference": reference,
                    }
                )
                store.create_orchestration_event(
                    event_type="tool_call",
                    label="Math/Calculator",
                    detail=f"{reference or expression} = {result}{f' {unit}' if unit else ''}",
                    status="completed",
                    conversation_id=event.conversation_id,
                    parent_event_id=user_event.id,
                    payload={
                        "expression": expression,
                        "result": result,
                        "unit": unit,
                        "reference": reference,
                        "subagent_raw": subagent_raw,
                    },
                )
                store.create_interaction_event(
                    event.conversation_id,
                    "tool",
                    json.dumps({"tool": "math_subagent", "expression": expression, "result": result, "unit": unit, "reference": reference}),
                    status="completed",
                    causation_event_id=user_event.id,
                )

    elif tool_name == "calculate":
        expression = str(params.get("expression", "")).strip()
        result = _calculate_expression(expression) if expression else None
        if result is not None:
            tool_observations.append(
                {
                    "tool": "calculate",
                    "label": "Calculator",
                    "expression": expression,
                    "result": result,
                }
            )
            store.create_orchestration_event(
                event_type="tool_call",
                label="Calculator",
                detail=f"{expression} = {result}",
                status="completed",
                conversation_id=event.conversation_id,
                parent_event_id=user_event.id,
                payload={"expression": expression, "result": result},
            )
            store.create_interaction_event(
                event.conversation_id,
                "tool",
                json.dumps({"tool": "calculate", "expression": expression, "result": result}),
                status="completed",
                causation_event_id=user_event.id,
            )

    elif tool_name == "count_occurrences":
        needle = str(params.get("needle", "")).strip()
        haystack = str(params.get("haystack", "")).strip()
        if needle and haystack and len(needle) == 1:
            count = haystack.count(needle)
            tool_observations.append(
                {
                    "tool": "count_occurrences",
                    "label": "Letter Count",
                    "needle": needle,
                    "haystack": haystack,
                    "result": str(count),
                }
            )
            store.create_orchestration_event(
                event_type="tool_call",
                label="Letter Count",
                detail=f'"{needle}" appears {count} time{"s" if count != 1 else ""}',
                status="completed",
                conversation_id=event.conversation_id,
                parent_event_id=user_event.id,
                payload={"needle": needle, "haystack": haystack, "result": count},
            )
            store.create_interaction_event(
                event.conversation_id,
                "tool",
                json.dumps({"tool": "count_occurrences", "needle": needle, "haystack": haystack, "result": count}),
                status="completed",
                causation_event_id=user_event.id,
            )

    elif tool_name and tool_name.startswith("mcp::"):
        server, discovered_tool = _find_mcp_server_for_tool(tool_name)
        if server is not None and discovered_tool:
            try:
                result = await call_mcp_tool(
                    {
                        "transport": server.transport,
                        "command": server.command,
                        "args": server.args,
                        "url": server.url,
                        "env": server.env,
                    },
                    discovered_tool,
                    params if isinstance(params, dict) else {},
                )
                detail = preview_text(json.dumps(result, ensure_ascii=True), max_chars=180)
                tool_observations.append(
                    {
                        "tool": tool_name,
                        "label": f"MCP: {server.name} / {discovered_tool}",
                        "tool_source_type": "mcp",
                        "server_id": server.id,
                        "server_name": server.name,
                        "mcp_tool": discovered_tool,
                        "arguments": params if isinstance(params, dict) else {},
                        "result": result,
                    }
                )
                store.create_orchestration_event(
                    event_type="mcp_call",
                    label=f"MCP: {server.name}",
                    detail=f"{discovered_tool} -> {detail}",
                    status="completed",
                    conversation_id=event.conversation_id,
                    parent_event_id=user_event.id,
                    payload={
                        "server_id": server.id,
                        "server_name": server.name,
                        "tool_name": discovered_tool,
                        "arguments": params if isinstance(params, dict) else {},
                        "result": result,
                    },
                )
                store.create_interaction_event(
                    event.conversation_id,
                    "tool",
                    json.dumps(
                        {
                            "tool": tool_name,
                            "server_name": server.name,
                            "mcp_tool": discovered_tool,
                            "arguments": params if isinstance(params, dict) else {},
                            "result": result,
                        }
                    ),
                    status="completed",
                    causation_event_id=user_event.id,
                )
            except McpClientError as exc:
                store.create_orchestration_event(
                    event_type="mcp_call",
                    label=f"MCP: {server.name}",
                    detail=str(exc),
                    status="failed",
                    conversation_id=event.conversation_id,
                    parent_event_id=user_event.id,
                    payload={
                        "server_id": server.id,
                        "server_name": server.name,
                        "tool_name": discovered_tool,
                        "arguments": params if isinstance(params, dict) else {},
                    },
                )

    route_decision = "tool_response" if tool_observations else "direct_dialogue"
    # retrieved_chunks is intentionally empty: RAG is owned by the dialogue worker.
    store.upsert_turn_context(
        user_event_id=user_event.id,
        conversation_id=event.conversation_id,
        route_decision=route_decision,
        retrieved_chunks=[],
        tool_observations=tool_observations,
        memory_candidates=[],
    )

    detail = (
        f"Tool dispatched: {tool_observations[0].get('label', tool_name or 'tool')}"
        if tool_observations
        else "No tool dispatched; route set to direct dialogue"
    )
    payload: dict = {"tool_observation_count": len(tool_observations)}
    payload["route_decision"] = route_decision
    if routing_raw:
        payload["routing_raw"] = routing_raw
    if subagent_raw:
        payload["subagent_raw"] = subagent_raw

    if tool_observations:
        store.mark_event_processing(user_event.id)
        llm_involved = any(str(item.get("tool_source_type", "")).strip().lower() == "mcp" for item in tool_observations)
        response_source = "orchestrator_tool"
        response_policy = "orchestrator_direct_tool_response"
        if llm_involved:
            response_source = "orchestrator_mcp_tool"
            response_policy = "orchestrator_tool_synthesis"
        await _complete_tool_routed_turn(
            user_event=user_event,
            tool_observations=tool_observations,
            response_source=response_source,
            response_policy=response_policy,
            llm_involved=llm_involved,
        )

    store.update_orchestration_event(
        event.id,
        status="completed",
        detail=detail,
        payload=payload,
    )


async def _process_finalize_turn(event: StoredOrchestrationEvent) -> None:
    if not event.parent_event_id or not event.conversation_id:
        store.update_orchestration_event(event.id, status="failed", error="missing_parent_event")
        return
    user_event = store.get_interaction_event(event.parent_event_id)
    assistant_event_id = str(event.payload.get("assistant_event_id", "")).strip() if isinstance(event.payload, dict) else ""
    assistant_event = store.get_interaction_event(assistant_event_id) if assistant_event_id else None
    if user_event is None or assistant_event is None:
        store.update_orchestration_event(event.id, status="failed", error="missing_turn_events")
        return
    context_settings = store.ensure_context_settings(
        settings.aigent_tenant_id,
        settings.ollama_context_window,
        settings.ollama_max_response_tokens,
        0.9,
    )
    if not context_settings.memory_enabled:
        store.update_orchestration_event(event.id, status="completed", detail="Memory disabled")
        return
    store.update_orchestration_event(event.id, status="processing", detail="Selecting durable memory")
    candidates = await _memory_candidates(user_event.content, extract_visible_text(assistant_event.content))
    turn_context = store.get_turn_context(user_event.id)
    tool_observations = turn_context.tool_observations if turn_context is not None else []
    retrieved = turn_context.retrieved_chunks if turn_context is not None else []
    if turn_context is not None:
        store.upsert_turn_context(
            user_event_id=user_event.id,
            conversation_id=event.conversation_id,
            route_decision=turn_context.route_decision,
            retrieved_chunks=retrieved,
            tool_observations=tool_observations,
            memory_candidates=candidates,
        )
    stored_count = 0
    for idx, candidate in enumerate(candidates):
        ok = await _store_chunks_for_source("turn_memory", f"{user_event.id}-{idx}", candidate["content"])
        if ok:
            stored_count += 1
            store.create_orchestration_event(
                event_type="memory_write",
                label="Writing memory",
                detail=f"{candidate['kind']}: {preview_text(candidate['content'])}",
                status="completed",
                conversation_id=event.conversation_id,
                parent_event_id=user_event.id,
                payload=candidate,
            )
    await _summarize_memory_if_needed()
    detail = "No durable memory written" if stored_count == 0 else f"Wrote {stored_count} memory item{'s' if stored_count != 1 else ''}"
    store.update_orchestration_event(
        event.id,
        status="completed",
        detail=detail,
        payload={"stored_count": stored_count},
    )


async def _process_document_import(event: StoredOrchestrationEvent) -> None:
    document_id = str(event.payload.get("document_id", "")).strip() if isinstance(event.payload, dict) else ""
    document = store.get_document_import(document_id)
    if document is None:
        store.update_orchestration_event(event.id, status="failed", error="document_not_found")
        return
    if document.conversation_id:
        started_msg = await _generate_import_started_message(document.filename)
        store.create_interaction_event(
            document.conversation_id,
            "assistant",
            started_msg,
            status="completed",
            causation_event_id=event.parent_event_id,
        )
    store.update_document_import_status(document.id, status="processing")
    store.update_orchestration_event(event.id, status="processing", detail=f"Converting {document.filename} via MarkItDown MCP")
    try:
        server = await _ensure_markitdown_server_ready()
        uri = _markitdown_file_uri(document.stored_path)
        result = await call_mcp_tool(
            {
                "transport": server.transport,
                "command": server.command,
                "args": server.args,
                "url": server.url,
                "env": server.env,
            },
            settings.markitdown_mcp_tool_name,
            {"uri": uri},
        )
        markdown = _extract_markdown_from_mcp_result(result)
        if not markdown.strip():
            raise RuntimeError("Document produced no readable text")
        stored = await _store_chunks_for_source("document_import", document.id, markdown)
        if not stored:
            raise RuntimeError("Document could not be embedded")
        summary = preview_text(markdown, max_chars=140)
        store.create_orchestration_event(
            event_type="mcp_call",
            label="MarkItDown MCP",
            detail=f"{settings.markitdown_mcp_tool_name} -> {summary}",
            status="completed",
            conversation_id=document.conversation_id,
            parent_event_id=event.parent_event_id,
            document_id=document.id,
            payload={
                "server_name": server.name,
                "server_id": server.id,
                "tool_name": settings.markitdown_mcp_tool_name,
                "uri": uri,
            },
        )
        if document.conversation_id:
            done_msg = await _generate_import_done_message(document.filename)
            store.create_interaction_event(
                document.conversation_id,
                "assistant",
                done_msg,
                status="completed",
                causation_event_id=event.parent_event_id,
            )
        store.update_document_import_status(document.id, status="completed")
        store.update_orchestration_event(
            event.id,
            status="completed",
            detail=f"Indexed {document.filename}",
            payload={"summary": summary},
        )
    except Exception as exc:
        server = store.find_mcp_server_by_name("MarkItDown MCP")
        if server is not None:
            store.create_orchestration_event(
                event_type="mcp_call",
                label="MarkItDown MCP",
                detail=str(exc),
                status="failed",
                conversation_id=document.conversation_id,
                parent_event_id=event.parent_event_id,
                document_id=document.id,
                payload={
                    "server_name": server.name,
                    "server_id": server.id,
                    "tool_name": settings.markitdown_mcp_tool_name,
                },
            )
        if document.conversation_id:
            failed_msg = await _generate_import_failed_message(document.filename, str(exc))
            store.create_interaction_event(
                document.conversation_id,
                "assistant",
                failed_msg,
                status="completed",
                causation_event_id=event.parent_event_id,
            )
        store.update_document_import_status(document.id, status="failed", error=str(exc))
        store.update_orchestration_event(event.id, status="failed", detail=str(exc), error=str(exc))


async def run_worker() -> None:
    await _discover_enabled_mcp_servers_on_startup()
    while True:
        try:
            store.update_worker_heartbeat("orchestrator-worker")
        except sqlite3.OperationalError as exc:
            print(f"orchestrator-worker heartbeat skipped due to sqlite lock: {exc}")
            await asyncio.sleep(settings.worker_poll_interval_ms / 1000.0)
            continue
        event = store.claim_next_pending_orchestration_event()
        if event is None:
            await asyncio.sleep(settings.worker_poll_interval_ms / 1000.0)
            continue
        try:
            if event.event_type == "prepare_turn":
                await _process_prepare_turn(event)
            elif event.event_type == "finalize_turn":
                await _process_finalize_turn(event)
            elif event.event_type == "document_import":
                await _process_document_import(event)
            else:
                store.update_orchestration_event(event.id, status="failed", error=f"unsupported_event_type:{event.event_type}")
        except Exception as exc:
            store.update_orchestration_event(event.id, status="failed", detail=str(exc), error=str(exc))
        await asyncio.sleep(0)


if __name__ == "__main__":
    asyncio.run(run_worker())
