"""État partagé, contexte d'exécution et contrats d'état des nœuds."""

from __future__ import annotations

import operator
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, TypedDict

from pydantic import BaseModel, ConfigDict

from app.config import PROJECT_ROOT
from app.harness.skills import Skill
from app.llm import StructuredLLM
from app.schemas import Document


def merge_dicts(left: dict | None, right: dict | None) -> dict:
    """Réducteur : les tâches parallèles mettent à jour des clés distinctes."""
    return {**(left or {}), **(right or {})}


class WatchState(TypedDict, total=False):
    run_id: str
    plan: dict  # TaskGraph sérialisé
    status: Annotated[dict[str, str], merge_dicts]
    collected: Annotated[dict[str, int], merge_dicts]
    errors: Annotated[list[str], operator.add]
    trace: Annotated[list[str], operator.add]  # décisions du supervisor
    candidates: list[dict]
    # Sous-graphe quality : URL → {sources, flags, duplicates} ; items écartés avec leur raison
    quality: dict[str, dict]
    filtered: list[dict]
    min_relevance: int
    review_round: int
    # Options du run : keywords, match_all, max_age_days, max_documents
    options: dict
    signals: list[dict]
    critiques: list[dict]
    accepted: list[dict]
    digest: dict
    violations: list[str]
    repair_round: int
    approval: bool
    note: str
    outputs: dict


@dataclass
class HarnessContext:
    llm: StructuredLLM
    connection: sqlite3.Connection
    skill: Skill
    output_dir: Path
    collectors: dict[str, Callable[[], list[Document]]] = field(default_factory=dict)
    human_approval: bool = True
    max_documents: int = 24
    batch_size: int = 8
    max_signals: int = 8
    max_age_days: int = 14
    min_relevance: int = 6
    min_accepted: int = 3
    max_review_rounds: int = 1
    max_repair_rounds: int = 1
    claude_memory_path: Path | None = None
    reports_dir: Path | None = None  # défaut : <output_dir>/../reports
    templates_dir: Path = PROJECT_ROOT / "templates"
    # Outil de publication Notion (None si NOTION_TOKEN / NOTION_PARENT_PAGE_ID absents)
    notion_sync: Callable[[sqlite3.Connection], dict] | None = None
    # Dépôts découverts par MCP à écarter (sources.toml, [github_mcp].exclude_keywords)
    exclude_keywords: list[str] = field(default_factory=list)
    # Qualité : seuil de similarité des titres (Jaccard), pool du prefilter (× max_documents),
    # poids du ranking hybride (app/workflow/quality.py, DEFAULT_WEIGHTS)
    dedup_threshold: float = 0.7
    quality_pool_factor: int = 3
    rank_weights: dict[str, float] = field(default_factory=dict)
    now: Callable[[], datetime] = field(default=lambda: datetime.now(timezone.utc))


# --- Contrats d'état (guards LangGraph) ------------------------------------
# extra="forbid" : un nœud ne peut écrire que les clés prévues.


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CollectorUpdate(_Contract):
    status: dict[str, str]
    collected: dict[str, int] = {}
    errors: list[str] = []


class PrefilterUpdate(_Contract):
    status: dict[str, str]
    candidates: list[dict]


class QualityUpdate(_Contract):
    status: dict[str, str]
    candidates: list[dict]
    quality: dict[str, dict]
    filtered: list[dict]
    trace: list[str] = []


class ResearchUpdate(_Contract):
    status: dict[str, str]
    signals: list[dict]
    errors: list[str] = []


class ReviewUpdate(_Contract):
    status: dict[str, str]
    critiques: list[dict]
    accepted: list[dict]


class EditorialUpdate(_Contract):
    status: dict[str, str]
    digest: dict
    violations: list[str]
    repair_round: int
