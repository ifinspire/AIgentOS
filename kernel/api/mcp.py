from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import Mapping

import httpx

from .settings import get_settings


settings = get_settings()


class McpClientError(RuntimeError):
    pass


def _json_rpc_payload(method: str, params: dict | None = None, *, request_id: str | None = None) -> dict:
    payload = {
        "jsonrpc": "2.0",
        "method": method,
    }
    if params is not None:
        payload["params"] = params
    if request_id is not None:
        payload["id"] = request_id
    return payload


def _initialize_payload() -> dict:
    return _json_rpc_payload(
        "initialize",
        {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {
                "name": "AIgentOS",
                "version": settings.aigent_version,
            },
        },
        request_id=f"init-{uuid.uuid4().hex}",
    )


async def _write_stdio_message(writer: asyncio.StreamWriter, payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8")
    writer.write(header + body)
    await writer.drain()


async def _read_stdio_message(reader: asyncio.StreamReader) -> dict:
    headers: dict[str, str] = {}
    while True:
        line = await reader.readline()
        if not line:
            raise McpClientError("MCP stdio server closed the stream unexpectedly")
        if line in {b"\r\n", b"\n"}:
            break
        decoded = line.decode("utf-8", errors="ignore").strip()
        if ":" not in decoded:
            continue
        key, value = decoded.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    content_length = int(headers.get("content-length", "0"))
    if content_length <= 0:
        raise McpClientError("MCP stdio response missing Content-Length")
    body = await reader.readexactly(content_length)
    try:
        return json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise McpClientError("MCP stdio response was not valid JSON") from exc


async def _read_stdio_response_for_id(reader: asyncio.StreamReader, request_id: str, *, max_messages: int = 50) -> dict:
    """Read stdio messages until we find the one matching request_id.

    Caps at max_messages to prevent infinite loops if the server sends
    non-matching IDs indefinitely (e.g. notifications or other requests).
    """
    for _ in range(max_messages):
        payload = await _read_stdio_message(reader)
        if payload.get("id") == request_id:
            return payload
    raise McpClientError(f"MCP stdio server sent {max_messages} messages without matching request {request_id}")


def _extract_tools_from_result(result: dict) -> list[dict]:
    """Extract tool metadata from an MCP tools/list response.

    Returns a list of dicts with keys: name, description, inputSchema.
    """
    tools = result.get("tools")
    if not isinstance(tools, list):
        return []
    extracted: list[dict] = []
    for item in tools:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        entry: dict = {"name": name}
        description = item.get("description")
        if isinstance(description, str) and description.strip():
            entry["description"] = description.strip()
        input_schema = item.get("inputSchema")
        if isinstance(input_schema, Mapping):
            entry["inputSchema"] = dict(input_schema)
        extracted.append(entry)
    return extracted


def _normalize_tool_call_result(payload: dict) -> dict:
    if "error" in payload:
        error = payload["error"]
        if isinstance(error, Mapping):
            message = str(error.get("message", "MCP tool call failed")).strip()
        else:
            message = str(error).strip() or "MCP tool call failed"
        raise McpClientError(message)
    result = payload.get("result")
    if not isinstance(result, Mapping):
        return {"raw": result}
    structured = result.get("structuredContent")
    content = result.get("content")
    if structured is not None:
        return {"structured": structured, "content": content}
    return dict(result)


async def _stdio_session(server: Mapping[str, object], method: str, params: dict) -> dict:
    """Spawn a stdio MCP server process, run initialize + one method, then terminate.

    A new subprocess is created per call. This is intentional for a self-hosted
    single-user system: connection volume is low, and persistent process management
    would add complexity (health checks, restart logic, resource leaks) that isn't
    justified at this scale. Future optimization is documented but deferred.
    """
    command = str(server.get("command") or "").strip()
    if not command:
        raise McpClientError("Stdio MCP server is missing a command")
    args = [str(item) for item in (server.get("args") or [])]
    env = os.environ.copy()
    env.update({str(k): str(v) for k, v in dict(server.get("env") or {}).items()})
    proc = await asyncio.create_subprocess_exec(
        command,
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    if proc.stdin is None or proc.stdout is None:
        raise McpClientError("Failed to open stdio pipes for MCP server")
    try:
        init_payload = _initialize_payload()
        await _write_stdio_message(proc.stdin, init_payload)
        init_response = await asyncio.wait_for(
            _read_stdio_response_for_id(proc.stdout, str(init_payload["id"])),
            timeout=settings.mcp_timeout_seconds,
        )
        if "error" in init_response:
            raise McpClientError(str(init_response["error"]))
        await _write_stdio_message(proc.stdin, _json_rpc_payload("notifications/initialized", {}))
        request_id = f"req-{uuid.uuid4().hex}"
        await _write_stdio_message(proc.stdin, _json_rpc_payload(method, params, request_id=request_id))
        return await asyncio.wait_for(
            _read_stdio_response_for_id(proc.stdout, request_id),
            timeout=settings.mcp_timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        raise McpClientError("Timed out waiting for MCP stdio server") from exc
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()


async def _http_post(client: httpx.AsyncClient, url: str, payload: dict) -> dict:
    response = await client.post(
        url,
        json=payload,
        headers={
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        },
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise McpClientError("MCP HTTP response was not a JSON object")
    return data


async def _http_session(server: Mapping[str, object], method: str, params: dict) -> dict:
    """Open a short-lived HTTP session with MCP initialize handshake.

    Per the MCP spec (2025-03-26), clients must send initialize →
    notifications/initialized before any other method. A new httpx client is
    created per session — this is intentional for a self-hosted single-user
    system where connection volume is low and many different MCP servers may
    be registered. Persistent pooling would add complexity for little gain.
    """
    url = str(server.get("url") or "").strip()
    if not url:
        raise McpClientError("HTTP MCP server is missing a URL")
    timeout = httpx.Timeout(settings.mcp_timeout_seconds)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            # MCP initialize handshake
            init_response = await _http_post(client, url, _initialize_payload())
            if "error" in init_response:
                raise McpClientError(f"MCP initialize failed: {init_response['error']}")
            # Send initialized notification (no response expected, but send as POST)
            try:
                await _http_post(client, url, _json_rpc_payload("notifications/initialized", {}))
            except Exception:
                pass  # Notifications may not return a response body
            # Actual method call
            payload = _json_rpc_payload(method, params, request_id=f"req-{uuid.uuid4().hex}")
            return await _http_post(client, url, payload)
    except McpClientError:
        raise
    except httpx.HTTPError as exc:
        raise McpClientError(f"MCP HTTP request failed: {exc}") from exc


async def _request(server: Mapping[str, object], method: str, params: dict) -> dict:
    transport = str(server.get("transport") or "").strip()
    if transport == "stdio":
        return await _stdio_session(server, method, params)
    if transport == "streamable_http":
        return await _http_session(server, method, params)
    raise McpClientError(f"Unsupported MCP transport: {transport or 'unknown'}")


async def discover_tools(server: Mapping[str, object]) -> list[dict]:
    payload = await _request(server, "tools/list", {})
    if "error" in payload:
        error = payload["error"]
        if isinstance(error, Mapping):
            message = str(error.get("message", "MCP discovery failed")).strip()
        else:
            message = str(error).strip() or "MCP discovery failed"
        raise McpClientError(message)
    result = payload.get("result")
    if not isinstance(result, Mapping):
        return []
    return _extract_tools_from_result(dict(result))


async def call_tool(server: Mapping[str, object], tool_name: str, arguments: dict | None = None) -> dict:
    payload = await _request(
        server,
        "tools/call",
        {
            "name": tool_name,
            "arguments": arguments or {},
        },
    )
    return _normalize_tool_call_result(payload)


def ensure_default_markitdown_server(store: object, settings: object) -> None:
    """Register the bundled MarkItDown MCP server if it doesn't exist yet.

    Shared between the kernel API startup and the orchestrator worker so the
    default server is always present regardless of which process starts first.
    """
    if not getattr(settings, "markitdown_mcp_enabled", False):
        return
    if store.find_mcp_server_by_name("MarkItDown MCP") is not None:  # type: ignore[union-attr]
        return
    store.create_mcp_server(  # type: ignore[union-attr]
        name="MarkItDown MCP",
        transport="streamable_http",
        url=getattr(settings, "markitdown_mcp_url", ""),
        enabled=True,
    )


async def discover_enabled_servers(store: object, *, retries: int = 5) -> None:
    """Run tool discovery on all enabled MCP servers with retry logic.

    Shared between the kernel API startup and the orchestrator worker so both
    processes have up-to-date discovered_tools after boot.
    """
    for server in store.list_mcp_servers():  # type: ignore[union-attr]
        if not server.enabled:
            continue
        last_error: str | None = None
        for attempt in range(retries):
            try:
                tools = await discover_tools(
                    {
                        "transport": server.transport,
                        "command": server.command,
                        "args": server.args,
                        "url": server.url,
                        "env": server.env,
                    }
                )
                store.set_mcp_server_discovery_result(server.id, discovered_tools=tools, status="connected", last_error=None)  # type: ignore[union-attr]
                last_error = None
                break
            except McpClientError as exc:
                last_error = str(exc)
                if attempt < retries - 1:
                    await asyncio.sleep(1.5 * (attempt + 1))
        if last_error is not None:
            store.set_mcp_server_error(server.id, error=last_error, status="error")  # type: ignore[union-attr]
