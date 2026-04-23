"""Unit tests for `kernel.api.prompts` — the module that discovers persona
prompt files on disk, composes the system prompt, and exposes the per-component
breakdown used by the Prompt Studio UI.

These tests pin down the three public behaviors the rest of the kernel relies
on:

* ``load_prompt_bundle`` — resolves the active agent persona to a single system
  prompt string. Must stay deterministic even when ``agent.yaml`` is missing.
* ``load_prompt_components`` / ``load_orchestrator_prompts`` — enumerate the
  persona / orchestrator markdown files in a stable, lexicographic order so the
  UI and the composer agree on which component is which.
* ``compose_system_prompt`` — respects the ``enabled`` flag and ``order`` field
  so disabled components are dropped without reshuffling surviving ones.

Each test uses ``tmp_path`` as a sandbox repo root, so nothing here touches the
real ``agent-prompts/`` directory.
"""

from pathlib import Path

import pytest

from kernel.api.prompts import (
    PromptComponent,
    compose_system_prompt,
    load_orchestrator_prompts,
    load_prompt_bundle,
    load_prompt_components,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.mark.p0
@pytest.mark.bvt
def test_load_prompt_bundle_fallback(tmp_path):
    """Missing-config fallback path.

    Scenario: the repo root has no ``agent-prompts/basic/agent.yaml`` (e.g. a
    brand-new checkout or a misconfigured deployment).

    Expected: ``load_prompt_bundle`` returns the caller-supplied default agent
    id and the hard-coded built-in system prompt, rather than raising. This is
    what keeps the API bootable when persona files are not yet provisioned.
    """
    bundle = load_prompt_bundle(tmp_path, default_agent_id="fallback-agent")

    assert bundle.agent_id == "fallback-agent"
    assert bundle.system_prompt == "You are a helpful local AI assistant."


@pytest.mark.p0
@pytest.mark.bvt
def test_load_prompt_bundle_from_configured_path(tmp_path):
    """Custom persona directory resolved from ``agent.yaml``.

    Scenario: an operator has set ``persona_prompt_set: prompts/persona`` in
    ``agent.yaml`` and dropped two numbered markdown parts in that directory
    (``00_intro.md``, ``01_rules.md``).

    Expected:
      * ``agent_id`` is taken from the YAML (not the fallback),
      * the two markdown files are concatenated in lexicographic order with a
        blank line between them (the format the LLM sees),
      * ``load_prompt_components`` exposes them in the same order with
        ``enabled=True`` so the UI can toggle them individually.

    Guards against regressions where the path override is ignored or where
    component ordering becomes filesystem-dependent.
    """
    _write(
        tmp_path / "agent-prompts" / "basic" / "agent.yaml",
        "agent_id: custom-agent\npersona_prompt_set: prompts/persona\n",
    )
    _write(tmp_path / "prompts" / "persona" / "00_intro.md", "First component")
    _write(tmp_path / "prompts" / "persona" / "01_rules.md", "Second component")

    bundle = load_prompt_bundle(tmp_path, default_agent_id="fallback-agent")
    components = load_prompt_components(tmp_path)

    assert bundle.agent_id == "custom-agent"
    assert bundle.system_prompt == "First component\n\nSecond component"
    assert [component.id for component in components] == ["00_intro", "01_rules"]
    assert all(component.enabled for component in components)


@pytest.mark.p0
def test_prompt_composition_and_orchestrator_loading(tmp_path):
    """Orchestrator prompt discovery + composer ``enabled`` filtering.

    Scenario part 1: two markdown files live under
    ``agent-prompts/orchestrator/`` (``routing.md``, ``tooling.md``).
    ``load_orchestrator_prompts`` must return them in sorted order — the
    orchestrator worker stitches them into the tool-calling system prompt and
    ordering changes change model behavior.

    Scenario part 2: ``compose_system_prompt`` is given two synthetic
    components — one disabled, one enabled. The disabled component's text must
    NOT appear in the composed prompt. This is the contract the Prompt Studio
    toggle relies on: flipping ``enabled`` to False is how an operator removes
    a component's effect without deleting the file.
    """
    _write(tmp_path / "agent-prompts" / "orchestrator" / "routing.md", "Route carefully")
    _write(tmp_path / "agent-prompts" / "orchestrator" / "tooling.md", "Use tools when needed")

    orchestrator = load_orchestrator_prompts(tmp_path)
    prompt = compose_system_prompt(
        [
            PromptComponent(
                id="disabled",
                name="disabled.md",
                file_path=str(tmp_path / "disabled.md"),
                content="Should not appear",
                order=0,
                enabled=False,
                is_system=True,
            ),
            PromptComponent(
                id="active",
                name="active.md",
                file_path=str(tmp_path / "active.md"),
                content="Active component",
                order=1,
                enabled=True,
                is_system=True,
            ),
        ]
    )

    assert [component.id for component in orchestrator] == ["routing", "tooling"]
    assert prompt == "Active component"
