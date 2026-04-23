"""Smoke tests for the FastAPI app in `kernel.api.main`.

``main.py`` is ~2000 lines and had no tests before this file. These tests
sit just below the HTTP surface — they exercise real route handlers,
request validation, response model serialization, and the module-level
``ChatStore`` wiring, without relying on Ollama, MCP servers, or any
network traffic.

Coverage here is deliberately narrow:

* ``GET /health`` — boot canary. If this fails, the app did not import
  cleanly.
* ``POST /api/conversations`` + ``GET /api/conversations`` — the minimum
  write / read round-trip the WebUI depends on.
* ``POST /api/admin/delete-all-data`` — the reset endpoint that wraps
  ``store.delete_all_data`` (guarded separately in ``test_storage.py``
  as P0-1). Confirms the HTTP layer enforces the ``confirm=true`` guard
  and actually clears state.

Two implementation notes:

1. ``main.py`` instantiates ``store`` / ``settings`` / MCP defaults at
   import time, so this module sets ``CHAT_DB_PATH``, ``UPLOADS_DIR``,
   and ``MARKITDOWN_MCP_ENABLED`` **before** importing the app, via a
   session-scoped fixture. All tests share one app instance and reset
   state per test via ``store.delete_all_data()`` inside a function-
   scoped fixture.

2. Requests are issued through ``httpx.AsyncClient`` with an in-process
   ``ASGITransport`` (rather than ``fastapi.TestClient``). This keeps
   the request handler on the test's own event loop — necessary because
   ``ChatStore``'s sqlite connection is bound to its creation thread.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient

if TYPE_CHECKING:  # pragma: no cover - import only for type hints
    from fastapi import FastAPI

    from kernel.api.storage import ChatStore


@pytest.fixture
def anyio_backend() -> str:
    """Pin the ``anyio`` pytest plugin to the ``asyncio`` backend.

    Without this, the plugin parameterizes every async test across both
    asyncio and trio; we only need one.
    """
    return "asyncio"


@pytest.fixture(scope="session")
def api_app(tmp_path_factory):
    """Import ``kernel.api.main`` once per test session with a sandbox DB.

    Environment overrides are applied before import so module-level
    globals (``store``, ``settings``, the default MCP server bootstrap)
    resolve to an isolated tmp path and do not touch the real dev DB.

    ``MARKITDOWN_MCP_ENABLED=false`` short-circuits the MCP default-server
    setup so the tests do not depend on the markitdown container being
    reachable.
    """
    db_dir = tmp_path_factory.mktemp("api_db")
    uploads_dir = tmp_path_factory.mktemp("api_uploads")

    os.environ["CHAT_DB_PATH"] = str(db_dir / "chat.db")
    os.environ["UPLOADS_DIR"] = str(uploads_dir)
    os.environ["MARKITDOWN_MCP_ENABLED"] = "false"
    os.environ["AIGENT_VERSION"] = "test-version"
    os.environ["AIGENT_TENANT_ID"] = "test-tenant"

    from kernel.api import main as api_main  # noqa: WPS433 (late import is intentional)

    return api_main.app, api_main.store, api_main.settings


@pytest.fixture
def api_context(api_app: "tuple[FastAPI, ChatStore, object]"):
    """Per-test clean slate on the shared session-scoped app."""
    app, store, settings = api_app
    store.delete_all_data()
    return app, store, settings


def _client(app) -> AsyncClient:
    """Return an ``AsyncClient`` whose requests hit ``app`` in-process."""
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


@pytest.mark.p0
@pytest.mark.bvt
@pytest.mark.anyio
async def test_health_endpoint_reports_version_and_tenant(api_context):
    """``GET /health`` returns the wiring the WebUI sniffs on connect.

    Scenario: the app was imported with ``AIGENT_VERSION=test-version`` and
    ``AIGENT_TENANT_ID=test-tenant``. Call ``GET /health``.

    Expected: 200 response whose body reflects the version / tenant /
    model / ollama_base_url fields that the frontend's connection banner
    reads at page load. ``is_warm`` is ``False`` on a fresh process — the
    warmup endpoint has not been called.

    Why it matters: if the app fails to import (circular dependency, bad
    type hint in models.py, etc.), this is the first test that will
    notice. It also locks down the ``HealthResponse`` shape — adding or
    removing a field forces a deliberate update here.
    """
    app, _store, _settings = api_context

    async with _client(app) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == "test-version"
    assert body["tenant_id"] == "test-tenant"
    assert body["is_warm"] is False
    assert isinstance(body["ollama_base_url"], str) and body["ollama_base_url"]
    assert isinstance(body["embedding_base_url"], str) and body["embedding_base_url"]


@pytest.mark.p0
@pytest.mark.bvt
@pytest.mark.anyio
async def test_conversation_create_list_roundtrip(api_context):
    """``POST /api/conversations`` + ``GET /api/conversations`` round-trip.

    Scenario:
      1. Store is empty (per-test clean slate).
      2. ``GET /api/conversations`` → empty list.
      3. ``POST /api/conversations`` with ``{"title": "Hello world"}``.
      4. ``GET /api/conversations`` again → the new conversation appears.

    Expected:
      * list endpoint returns an empty list initially,
      * create endpoint returns 200 with a non-empty UUID ``id``, the
        submitted title, and an empty ``messages`` list,
      * the follow-up list call includes exactly one summary whose ``id``
        matches the created conversation.

    Why it matters: these two endpoints are the first calls the WebUI makes
    after ``/health``. If the ``ConversationDetail`` / ``ConversationSummary``
    response models drift out of sync with the ``ChatStore`` dataclasses,
    the frontend fails to render the sidebar.
    """
    app, _store, _settings = api_context

    async with _client(app) as client:
        empty = await client.get("/api/conversations")
        assert empty.status_code == 200
        assert empty.json() == []

        created = await client.post("/api/conversations", json={"title": "Hello world"})
        assert created.status_code == 200
        created_body = created.json()
        assert created_body["id"]
        assert created_body["title"] == "Hello world"
        assert created_body["messages"] == []

        listing = await client.get("/api/conversations")
        assert listing.status_code == 200
        summaries = listing.json()
        assert len(summaries) == 1
        assert summaries[0]["id"] == created_body["id"]
        assert summaries[0]["title"] == "Hello world"


@pytest.mark.p0
@pytest.mark.anyio
async def test_delete_all_data_endpoint_requires_confirmation_and_clears_state(api_context):
    """``POST /api/admin/delete-all-data`` enforces ``confirm=true`` and wipes.

    Scenario:
      1. Seed one conversation (via the API, not the store directly —
         this proves the HTTP path round-trips).
      2. Call the delete endpoint with ``{"confirm": false}`` → expect
         400 and the conversation is still there.
      3. Call the delete endpoint with ``{"confirm": true}`` → expect
         200 with ``ok=true`` and a ``deleted_at`` timestamp.
      4. ``GET /api/conversations`` returns an empty list.

    Why it matters: this endpoint is nuclear — it also removes uploads
    from disk. A regression that lets a bare ``POST`` through (without
    ``confirm=true``) is a data-loss incident. This test locks down the
    guard at the HTTP layer; the deeper ``ChatStore.delete_all_data``
    guarantees are regression-covered by
    ``test_delete_all_data_clears_every_table_and_store_remains_usable``.
    """
    app, _store, _settings = api_context

    async with _client(app) as client:
        seed = await client.post("/api/conversations", json={"title": "To be deleted"})
        assert seed.status_code == 200

        rejected = await client.post("/api/admin/delete-all-data", json={"confirm": False})
        assert rejected.status_code == 400
        still_present = await client.get("/api/conversations")
        assert still_present.json() != []

        accepted = await client.post("/api/admin/delete-all-data", json={"confirm": True})
        assert accepted.status_code == 200
        body = accepted.json()
        assert body["ok"] is True
        assert body["deleted_at"]

        cleared = await client.get("/api/conversations")
        assert cleared.json() == []
