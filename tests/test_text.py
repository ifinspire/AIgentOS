"""Unit tests for the small, pure helpers in `kernel.shared.text` and
`kernel.shared.metrics`.

These helpers are shared by the API, the dialogue worker, and the
orchestrator worker. They are intentionally dependency-free (stdlib only) and
their contracts are tight, so tests here are fast, deterministic, and double
as executable documentation for the helpers' edge-case behavior.

Covered behaviors:
  * ``chunk_text`` — word-boundary chunking used by the RAG ingest path.
  * ``cosine_similarity`` — used when ranking retrieved chunks. Specifically
    documents the sentinel return value (``-1.0``) for invalid inputs, so
    callers can sort without special-casing.
  * ``extract_visible_text`` — strips reasoning/thinking blocks from model
    output before it is shown to the user or stored as an assistant message.
  * ``preview_text`` + token estimators — used for sidebar previews and for
    the token-budget accounting displayed in the Performance panel.
"""

import pytest

from kernel.shared.metrics import allocate_estimated_tokens, estimate_tokens_for_text
from kernel.shared.text import chunk_text, cosine_similarity, extract_visible_text, preview_text


@pytest.mark.p0
@pytest.mark.bvt
def test_chunk_text_chunks_words():
    """Word-boundary chunking honors ``max_chars`` without splitting words.

    Scenario: six single-word tokens of 4–7 chars each fed to ``chunk_text``
    with ``max_chars=12``. The greedy packer should fit two words per chunk
    (``"alpha beta"`` = 10 chars, adding ``" gamma"`` would exceed 12).

    Expected: three chunks of two words each, with a single space separator.

    Why it matters: this function is called during document ingest before
    embeddings are computed. If it ever started producing mid-word splits, RAG
    quality would silently degrade (embeddings of partial words are garbage).
    """
    text = "alpha beta gamma delta epsilon zeta"

    chunks = chunk_text(text, max_chars=12)

    assert chunks == ["alpha beta", "gamma delta", "epsilon zeta"]


@pytest.mark.p0
def test_cosine_similarity_cases():
    """Cosine similarity returns the sentinel ``-1.0`` for invalid inputs.

    Scenarios:
      * identical unit vectors ``[1,0]`` → ``1.0`` (sanity baseline),
      * mismatched dimensionality ``[1]`` vs ``[1,2]`` → ``-1.0`` (caller
        passed in a malformed embedding; we must not ``IndexError``),
      * zero-magnitude vector ``[0,0]`` → ``-1.0`` (avoid a div-by-zero).

    The sentinel matters because retrieval callers sort descending by score
    and expect invalid pairings to rank last, not crash the request.
    """
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine_similarity([1.0], [1.0, 2.0]) == -1.0
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == -1.0


@pytest.mark.p0
@pytest.mark.bvt
def test_extract_visible_text_filters_think_blocks():
    """``<think>…</think>`` blocks are stripped before text is surfaced.

    Scenario: a model that emits reasoning inside ``<think>`` tags (e.g.
    SmolLM3, DeepSeek-R1-family). The user-visible output must strip the tag
    and its contents and return the remaining text trimmed of leading/trailing
    whitespace.

    Why it matters: if reasoning text leaked into stored assistant messages it
    would (a) confuse end users, (b) inflate token accounting, and (c) poison
    retrieval if that message was later embedded into the RAG index.
    """
    text = "Visible<think>hidden reasoning</think> answer"

    assert extract_visible_text(text) == "Visible answer"


@pytest.mark.p0
def test_preview_and_token_helpers():
    """Preview truncation and the ~4-char-per-token estimator stay stable.

    Scenario A — ``preview_text``: input has runs of consecutive whitespace
    (``"one   two   three   four"``). The function first collapses whitespace,
    then truncates to ``max_chars=10``. Because an ellipsis (``"..."``) is
    appended after truncation, the returned string is longer than 10 chars by
    design — callers that need strict length bounds must account for the
    3-char suffix.

    Scenario B — ``estimate_tokens_for_text``: the char/4 heuristic rounds up,
    so 8 characters become 2 tokens.

    Scenario C — ``allocate_estimated_tokens``: given a total budget of 20
    tokens and a character split of (2, 3, 5), the proportional allocation
    is (4, 6, 10). The assistant share absorbs rounding to ensure the parts
    sum exactly to the total — a property the Performance panel relies on.
    """
    preview = preview_text("one   two   three   four", max_chars=10)

    assert preview == "one two th..."
    assert estimate_tokens_for_text("12345678") == 2
    assert allocate_estimated_tokens(20, 2, 3, 5) == (4, 6, 10)
