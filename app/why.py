"""« Pourquoi cette veille ? » : ce qui oriente la sélection, en un seul endroit auditable.

Rassemble le profil d'impact versionné, les souvenirs typés (provenance, confiance,
expiration), les règles actives ou suggérées, les réglages du ranking et de la sélection
diversifiée, les règles de preuve, et l'explication item par item du dernier digest.
Lecture seule : les décisions sur les règles passent par `WatchMemory.decide_rule`.
"""

from __future__ import annotations

from app import storage
from app.config import settings
from app.memory import WatchMemory
from app.profile import load_profile
from app.workflow.quality import DEFAULT_WEIGHTS

EVIDENCE_RULES = [
    "Chaque fait publié porte une citation retrouvée mot pour mot dans la source ; sinon il est écarté.",
    "Un benchmark précise matériel, modèle, batch, contexte, version et méthode ; sinon « protocole incomplet ».",
    "Analyse et hypothèse de l'Editor sont marquées comme telles, séparées des faits.",
    "Une source secondaire seule ne suffit jamais pour « confirmé ».",
    "Une contradiction entre sources est affichée dans le rapport, jamais lissée.",
    "Tout chiffre de la prose de l'Editor doit figurer dans la source (sinon réparation ou blocage).",
]


def why_payload(connection, store) -> dict:
    memory = WatchMemory(store)
    profile = load_profile(settings.impact_profile_path)
    digests = storage.recent_digests(connection, limit=1)
    last = digests[0]["digest"] if digests else None
    return {
        "profile": profile.model_dump() | {"label": profile.label, "path": str(settings.impact_profile_path)}
        if profile else None,
        "records": [record.model_dump(mode="json") for record in memory.records()],
        "tags": memory.top_tags(limit=12),
        "ranking": {"weights": DEFAULT_WEIGHTS | settings.rank_weights, "diversity_penalty": settings.diversity_penalty,
                    "max_signals": settings.max_signals, "min_relevance": settings.min_relevance},
        "evidence_rules": EVIDENCE_RULES,
        "last_digest": None if last is None else {
            "generated_at": last["generated_at"],
            "profile_version": last.get("profile_version"),
            "items": [{key: item.get(key) for key in ("title", "url", "score", "rank_reasons", "impact_reasons",
                                                      "confidence", "confidence_reasons")}
                      for item in last["items"]],
        },
    }
