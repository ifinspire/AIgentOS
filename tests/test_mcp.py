"""Unit tests for the pure protocol parsers in `kernel.api.mcp`.

Every outbound MCP request (stdio or HTTP) eventually funnels through two
module-private helpers:

* ``_extract_tools_from_result`` — turns a ``tools/list`` JSON-RPC result into
  the list of tool dicts the orchestrator actually feeds to the LLM.
* ``_normalize_tool_call_result`` — turns a ``tools/call`` JSON-RPC response
  (or error envelope) into the structured payload the orchestrator consumes.

Both are dependency-free (dicts in, dicts out) which makes them cheap to test
and, importantly, lets us pin down the tolerance behavior for malformed server
responses without spinning up a real MCP server. These tests guard the
parsing surface that every MCP tool call passes through — a regression here
would break tool calling silently, so they are marked ``p0`` and ``bvt``.
"""

import pytest

from kernel.api.mcp import (
    McpClientError,
    _extract_tools_from_result,
    _normalize_tool_call_result,
)


@pytest.mark.p0
@pytest.mark.bvt
def test_extract_tools_returns_well_formed_entries():
    """Happy path: valid ``tools/list`` result yields clean tool dicts.

    Scenario: a server replies with a ``tools`` list containing one fully-
    populated tool (name + description + inputSchema).

    Expected:
      * the returned list has one entry,
      * the entry carries the name, a trimmed description, and the
        ``inputSchema`` copied as a plain dict (not the original reference —
        so the orchestrator can't accidentally mutate the server's payload).

    Why it matters: this is the shape the LLM eventually sees as its tool
    catalog. If trimming or key names drift, the model stops recognizing
    tools.
    """
    result = {
        "tools": [
            {
                "name": "convert_to_markdown",
                "description": "  Convert a document to Markdown.  ",
                "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}},
            }
        ]
    }

    tools = _extract_tools_from_result(result)

    assert len(tools) == 1
    assert tools[0]["name"] == "convert_to_markdown"
    assert tools[0]["description"] == "Convert a document to Markdown."
    assert tools[0]["inputSchema"] == {
        "type": "object",
        "properties": {"path": {"type": "string"}},
    }
    # inputSchema must be a shallow copy, not the literal server reference.
    assert tools[0]["inputSchema"] is not result["tools"][0]["inputSchema"]


@pytest.mark.p0
def test_extract_tools_tolerates_malformed_entries():
    """The parser drops junk entries instead of propagating garbage.

    Scenario: a misbehaving server returns a mix of entries —
      * no ``tools`` key at all,
      * ``tools`` is not a list (``None`` / string / dict),
      * list contains a non-dict element (``None``, string),
      * list contains a dict with a missing or empty ``name``,
      * list contains a dict with ``description`` that is not a string,
      * list contains a dict with ``inputSchema`` that is not a mapping.

    Expected: the parser returns only the well-formed entries. It never
    raises and never propagates non-string descriptions or non-mapping
    schemas.

    Why it matters: the orchestrator uses this list as a fallback when an
    MCP server is partially implemented or sends unexpected data. A ``None``
    description leaking into the system prompt would crash downstream JSON
    serialization.
    """
    assert _extract_tools_from_result({}) == []
    assert _extract_tools_from_result({"tools": None}) == []
    assert _extract_tools_from_result({"tools": "not-a-list"}) == []

    mixed = {
        "tools": [
            None,
            "nope",
            {"name": ""},
            {"name": "   "},
            {"name": "good_tool"},
            {"name": "with_bad_desc", "description": 123},
            {"name": "with_bad_schema", "inputSchema": "not-a-mapping"},
        ]
    }

    tools = _extract_tools_from_result(mixed)

    assert [tool["name"] for tool in tools] == [
        "good_tool",
        "with_bad_desc",
        "with_bad_schema",
    ]
    assert "description" not in tools[1]
    assert "inputSchema" not in tools[2]


@pytest.mark.p0
@pytest.mark.bvt
def test_normalize_tool_call_result_prefers_structured_content():
    """A ``structuredContent`` field wins over plain ``content``.

    Scenario: the MCP ``tools/call`` response carries both
    ``structuredContent`` (a JSON-parseable object) and ``content`` (the
    fallback text array the spec allows). The orchestrator prefers structured
    data when present.

    Expected:
      * returned dict has ``structured`` set to the structured payload,
      * returned dict still carries ``content`` so the orchestrator can show
        the human-readable rendering if it chooses.
    """
    payload = {
        "result": {
            "structuredContent": {"markdown": "# Title"},
            "content": [{"type": "text", "text": "# Title"}],
        }
    }

    normalized = _normalize_tool_call_result(payload)

    assert normalized == {
        "structured": {"markdown": "# Title"},
        "content": [{"type": "text", "text": "# Title"}],
    }


@pytest.mark.p0
def test_normalize_tool_call_result_falls_back_to_raw_result():
    """Without ``structuredContent``, the full ``result`` dict is returned.

    Scenario: a spec-compliant server returns a ``tools/call`` result with
    just a ``content`` array. ``_normalize_tool_call_result`` should pass the
    result through untouched (as a plain dict copy) so the orchestrator can
    render the text.

    Also covers the non-Mapping fallback: if ``result`` is not a dict
    (e.g. a bare string or None), the parser wraps it under ``{"raw": ...}``
    rather than dropping information.
    """
    text_only = {"result": {"content": [{"type": "text", "text": "ok"}]}}
    assert _normalize_tool_call_result(text_only) == {
        "content": [{"type": "text", "text": "ok"}]
    }

    bare_string = {"result": "literal"}
    assert _normalize_tool_call_result(bare_string) == {"raw": "literal"}


@pytest.mark.p0
def test_normalize_tool_call_result_raises_on_error_envelope():
    """A JSON-RPC ``error`` envelope becomes an ``McpClientError``.

    Scenario A: well-formed error object with a ``message`` key. The parser
    raises ``McpClientError`` carrying that message, so the orchestrator can
    surface it to the user without parsing the envelope itself.

    Scenario B: malformed error (a bare string instead of a mapping). The
    parser still raises, using the string as the message or the generic
    fallback if empty.
    """
    with pytest.raises(McpClientError, match="tool failed"):
        _normalize_tool_call_result({"error": {"code": -1, "message": "tool failed"}})

    with pytest.raises(McpClientError, match="permission denied"):
        _normalize_tool_call_result({"error": "permission denied"})

    with pytest.raises(McpClientError, match="MCP tool call failed"):
        _normalize_tool_call_result({"error": ""})
