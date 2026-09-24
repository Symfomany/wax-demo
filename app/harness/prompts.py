"""Prompts des agents, versionnés en Markdown dans app/prompts/."""

from pathlib import Path
from string import Template


PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def render_prompt(name: str, **values: object) -> str:
    template = Template((PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8"))
    # substitute (et non safe_substitute) : une variable oubliée doit échouer.
    return template.substitute({key: str(value) for key, value in values.items()})
