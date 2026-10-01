"""Image Docker obsolète : dépendances de pyproject.toml absentes de l'image construite.

Le dépôt est monté sur /app : un `git pull` qui ajoute une dépendance (ex. prometheus-client) fait
tourner du code neuf dans une image ancienne, qui plantait au démarrage sur un ImportError obscur
— et, pour le conteneur web, sans écoute du bouton MQTT. Le Dockerfile fige la liste installée dans
/opt/venv/requirements.baked ; web et cron la comparent à pyproject.toml avant de démarrer.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

BAKED = Path("/opt/venv/requirements.baked")


def declared(pyproject: Path) -> set[str]:
    project = tomllib.loads(pyproject.read_text())["project"]
    return {line.strip() for line in [*project["dependencies"], *project["optional-dependencies"]["dev"]]
            if line.strip()}


def missing_dependencies(pyproject: Path, baked: Path | None = None) -> list[str] | None:
    """Dépendances déclarées mais absentes de l'image ; None hors Docker (pas de liste figée)."""
    baked = baked or BAKED
    if not baked.is_file():
        return None
    installed = {line.strip() for line in baked.read_text().splitlines() if line.strip()}
    return sorted(declared(pyproject) - installed)


def stale_image_message(pyproject: Path, baked: Path | None = None) -> str | None:
    missing = missing_dependencies(pyproject, baked)
    if not missing:
        return None
    return ("Image Docker obsolète : pyproject.toml déclare des dépendances absentes de l'image ("
            + ", ".join(missing) + "). Reconstruire : docker compose up -d --build")
