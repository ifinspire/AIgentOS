"""Unit tests for `kernel.api.settings` — the single source of truth for
environment-driven configuration.

``Settings`` is a frozen dataclass produced by ``get_settings()``. Every value
originates from a specific environment variable and has a documented default,
so these tests lock down two guarantees the rest of the kernel depends on:

1. The private typed-env parsers (``_int_env``, ``_bool_env``) are strict:
   missing, non-numeric, and below-minimum inputs all fall through to the
   caller-supplied default rather than raising or yielding garbage. This keeps
   the kernel bootable when a ``.env`` file has a typo in production.

2. ``get_settings()`` actually wires each env var into the matching field.
   Regression guard: it's easy to add a new ``Settings`` field and forget to
   read the env var, or rename an env var without updating the default.

``monkeypatch`` is used for env isolation so tests do not leak into one
another or into the developer's shell.
"""

import pytest

from kernel.api.settings import _bool_env, _int_env, get_settings


@pytest.mark.p0
def test_int_env_defaulting(monkeypatch):
    """`_int_env` must reject three classes of bad input and return the default.

    Scenarios exercised:
      * variable is unset entirely,
      * variable is set to a non-numeric string (``"nope"``),
      * variable parses as int but violates ``min_value`` (``"5"`` < 10).

    Why it matters: ``OLLAMA_CONTEXT_WINDOW`` and siblings use this helper with
    strict minima. If this helper ever started propagating invalid values, the
    LLM client would be asked to allocate nonsensically small contexts.
    """
    monkeypatch.delenv("TEST_INT_ENV", raising=False)
    assert _int_env("TEST_INT_ENV", 42, min_value=10) == 42

    monkeypatch.setenv("TEST_INT_ENV", "nope")
    assert _int_env("TEST_INT_ENV", 42, min_value=10) == 42

    monkeypatch.setenv("TEST_INT_ENV", "5")
    assert _int_env("TEST_INT_ENV", 42, min_value=10) == 42


@pytest.mark.p0
def test_bool_env_parsing(monkeypatch):
    """`_bool_env` accepts a fixed set of truthy tokens, case-insensitively.

    Scenarios exercised:
      * variable unset → return caller default (here: ``True``),
      * variable set to ``"YES"`` → parse as True (case-insensitive, truthy
        token),
      * variable set to ``"off"`` → parse as False (falsy token overrides the
        caller's ``True`` default).

    Why it matters: ``MARKITDOWN_MCP_ENABLED`` is parsed through this helper.
    Operators commonly write ``True`` / ``yes`` / ``on`` and a quiet
    stringly-typed truthy would silently leave MCP integrations enabled when
    the operator meant to disable them.
    """
    monkeypatch.delenv("TEST_BOOL_ENV", raising=False)
    assert _bool_env("TEST_BOOL_ENV", True) is True

    monkeypatch.setenv("TEST_BOOL_ENV", "YES")
    assert _bool_env("TEST_BOOL_ENV", False) is True

    monkeypatch.setenv("TEST_BOOL_ENV", "off")
    assert _bool_env("TEST_BOOL_ENV", True) is False


@pytest.mark.p0
@pytest.mark.bvt
def test_get_settings_overrides(monkeypatch, tmp_path):
    """End-to-end check that env vars reach the matching ``Settings`` field.

    Scenario: simulate a production ``.env`` by exporting a representative
    subset of the supported variables (version, tenant, token budgets, worker
    tuning, on-disk paths, MCP toggle) and call ``get_settings()``.

    Expected: every overridden field in the returned ``Settings`` instance
    reflects the exported value and its declared type (ints are ints, bools
    are bools, paths are strings). Guards against three classes of bug:
      * field added to ``Settings`` but not read from env,
      * env var renamed in code but not in this test (so we also rename here),
      * int/bool parsers bypassed for a field, leaving it stringly-typed.
    """
    db_path = tmp_path / "chat.db"
    uploads_dir = tmp_path / "uploads"

    monkeypatch.setenv("AIGENT_VERSION", "1.2.3-test")
    monkeypatch.setenv("AIGENT_TENANT_ID", "tenant-123")
    monkeypatch.setenv("OLLAMA_CONTEXT_WINDOW", "8192")
    monkeypatch.setenv("OLLAMA_MAX_RESPONSE_TOKENS", "2048")
    monkeypatch.setenv("WORKER_POLL_INTERVAL_MS", "1200")
    monkeypatch.setenv("CHAT_DB_PATH", str(db_path))
    monkeypatch.setenv("UPLOADS_DIR", str(uploads_dir))
    monkeypatch.setenv("MARKITDOWN_MCP_ENABLED", "false")

    settings = get_settings()

    assert settings.aigent_version == "1.2.3-test"
    assert settings.aigent_tenant_id == "tenant-123"
    assert settings.ollama_context_window == 8192
    assert settings.ollama_max_response_tokens == 2048
    assert settings.worker_poll_interval_ms == 1200
    assert settings.chat_db_path == str(db_path)
    assert settings.uploads_dir == str(uploads_dir)
    assert settings.markitdown_mcp_enabled is False
