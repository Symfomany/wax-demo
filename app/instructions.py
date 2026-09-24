"""Instructions Claude templatées : un profil TOML → CLAUDE.md, demande de veille, prompt du chat."""

import tomllib
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import ChoiceLoader, Environment, FileSystemLoader, StrictUndefined

from app.config import settings

TARGETS = {
    "claude-md": "CLAUDE.md.j2",
    "demande": "demande-veille.md.j2",
    "chat": "chat-system.md.j2",
}


def load_profile(path: Path | None = None) -> dict:
    with (path or settings.templates_dir / "claude" / "profil.toml").open("rb") as handle:
        return tomllib.load(handle)


def render_instructions(target: str, profile_path: Path | None = None, **values) -> str:
    if target not in TARGETS:
        raise ValueError(f"Cible inconnue : {target} (attendu : {', '.join(TARGETS)})")
    environment = Environment(
        # Une surcharge éditée depuis l'interface (data/prompts) prime sur le template livré.
        loader=ChoiceLoader([
            FileSystemLoader(settings.prompt_overrides_dir),
            FileSystemLoader(settings.templates_dir / "claude"),
        ]),
        trim_blocks=True,
        lstrip_blocks=True,
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    defaults = {
        "today": datetime.now(timezone.utc).strftime("%d/%m/%Y"),
        "period": "7 derniers jours",
        "model": settings.llm_model,
        "memory": "Aucun.",
        "tool": "aucun",
        "context": "",
    }
    return environment.get_template(TARGETS[target]).render(
        **load_profile(profile_path), **(defaults | values)
    )
