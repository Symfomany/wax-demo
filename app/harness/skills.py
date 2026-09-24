"""Chargement des Skills Claude Code (.claude/skills/*/SKILL.md).

Le même fichier sert de procédure à Claude Code et de consignes aux agents
LangGraph locaux : une seule source de vérité pour la méthode de veille.
"""

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str

    def section(self, title: str) -> str:
        match = re.search(
            rf"^#\s+{re.escape(title)}\s*$(.*?)(?=^#\s|\Z)",
            self.body,
            re.MULTILINE | re.DOTALL,
        )
        return match.group(1).strip() if match else ""


def load_skill(path: Path) -> Skill:
    text = path.read_text(encoding="utf-8")
    meta: dict[str, str] = {}
    body = text

    if text.startswith("---"):
        _, frontmatter, body = text.split("---", 2)
        for line in frontmatter.strip().splitlines():
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()

    return Skill(
        name=meta.get("name", path.parent.name),
        description=meta.get("description", ""),
        body=body.strip(),
    )
