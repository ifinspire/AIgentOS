from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class PromptBundle:
    agent_id: str
    system_prompt: str


@dataclass
class PromptComponent:
    id: str
    name: str
    file_path: str
    content: str
    order: int
    enabled: bool
    is_system: bool


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def load_prompt_bundle(repo_root: Path, default_agent_id: str) -> PromptBundle:
    prompt_root = repo_root / "agent-prompts"
    agent_file = prompt_root / "basic" / "agent.yaml"
    if not agent_file.exists():
        return PromptBundle(
            agent_id=default_agent_id,
            system_prompt="You are a helpful local AI assistant.",
        )

    config: dict[str, Any] = yaml.safe_load(agent_file.read_text(encoding="utf-8")) or {}
    agent_id = str(config.get("agent_id") or default_agent_id)

    persona_path = str(config.get("persona_prompt_set") or "agent-prompts/basic/components")
    components_dir = repo_root / persona_path
    component_parts: list[str] = []
    if components_dir.exists():
        for md_file in sorted(components_dir.glob("*.md")):
            text = _read_text(md_file)
            if text:
                component_parts.append(text)

    if not component_parts:
        system_prompt = "You are a helpful local AI assistant."
    else:
        system_prompt = "\n\n".join(component_parts)

    return PromptBundle(agent_id=agent_id, system_prompt=system_prompt)


def load_prompt_components(repo_root: Path) -> list[PromptComponent]:
    prompt_root = repo_root / "agent-prompts"
    agent_file = prompt_root / "basic" / "agent.yaml"
    if not agent_file.exists():
        return []

    config: dict[str, Any] = yaml.safe_load(agent_file.read_text(encoding="utf-8")) or {}
    persona_path = str(config.get("persona_prompt_set") or "agent-prompts/basic/components")
    components_dir = repo_root / persona_path
    if not components_dir.exists():
        return []

    components: list[PromptComponent] = []
    for idx, md_file in enumerate(sorted(components_dir.glob("*.md"))):
        text = _read_text(md_file)
        components.append(
            PromptComponent(
                id=md_file.stem,
                name=md_file.name,
                file_path=str(md_file.resolve()),
                content=text,
                order=idx,
                enabled=True,
                is_system=True,
            )
        )
    return components


def compose_system_prompt(components: list[PromptComponent]) -> str:
    enabled = [c.content.strip() for c in sorted(components, key=lambda x: x.order) if c.enabled and c.content.strip()]
    if not enabled:
        return "You are a helpful local AI assistant."
    return "\n\n".join(enabled)
