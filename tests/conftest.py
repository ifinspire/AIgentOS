"""Shared pytest fixtures for the kernel unit tests.

Fixtures defined here are auto-discovered by pytest without per-file imports.
Keep this file dependency-free and fast — anything expensive should live in a
narrower, opt-in fixture inside the relevant ``test_*.py`` module.
"""

from __future__ import annotations

import pytest

from kernel.api.storage import ChatStore


@pytest.fixture
def chat_store(tmp_path) -> ChatStore:
    """A fresh ``ChatStore`` rooted at a per-test temp directory.

    The store auto-creates its parent directory and runs ``_init_db`` /
    ``_repair_db_if_needed`` on first use, so tests get a fully-migrated schema
    with no hand-rolled setup. ``tmp_path`` is torn down by pytest after the
    test, so there is no cross-test state leak.
    """
    return ChatStore(str(tmp_path / "data" / "chat.db"))
