"""Mémoire long terme de la veille, sur le Store LangGraph (BaseStore).

Trois niveaux de mémoire coexistent dans le harness :

- SQLite `data/watch.db` : mémoire factuelle (documents, digests, items publiés,
  cache LLM, historique des runs) — app/storage.py.
- Store LangGraph `data/memory.db` : mémoire sémantique des agents
  (leçons humaines, préférences de thèmes, santé des sources) — ce module.
  Les nœuds la reçoivent par injection (`store: BaseStore`).
- Mémoire Claude Code `.claude/memory/veille.md` : export Markdown de ce Store,
  importé par CLAUDE.md (`@.claude/memory/veille.md`) pour que chaque session
  Claude Code démarre avec le contexte de veille.
"""

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from langgraph.store.base import BaseStore


LESSONS = ("memory", "lessons")
TAGS = ("memory", "tags")
SOURCES = ("memory", "sources")
INTERESTS = ("memory", "interests")

# Tags purement techniques, non informatifs pour les préférences.
IGNORED_TAGS = {"rss", "arxiv", "github", "github-mcp", "prerelease"}


class WatchMemory:
    def __init__(self, store: BaseStore) -> None:
        self.store = store

    # --- Leçons (retours humains) -------------------------------------------

    def add_lesson(self, note: str, approved: bool, run_id: str) -> None:
        if not note.strip():
            return
        self.store.put(
            LESSONS,
            str(uuid4()),
            {
                "note": note.strip(),
                "approved": approved,
                "run_id": run_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    def lessons(self, limit: int = 5) -> list[dict]:
        items = self.store.search(LESSONS, limit=200)
        ordered = sorted((item.value for item in items), key=lambda v: v["created_at"], reverse=True)
        return ordered[:limit]

    # --- Préférences de thèmes ----------------------------------------------

    def reinforce_tags(self, tags: list[str], weight: int = 1) -> None:
        for tag in {t.lower() for t in tags} - IGNORED_TAGS:
            if "/" in tag:  # dépôt « owner/repo » : trop spécifique
                continue
            current = self.store.get(TAGS, tag)
            score = (current.value["score"] if current else 0) + weight
            self.store.put(TAGS, tag, {"score": score})

    def top_tags(self, limit: int = 8) -> list[tuple[str, int]]:
        items = self.store.search(TAGS, limit=500)
        ranked = sorted(((i.key, i.value["score"]) for i in items), key=lambda kv: -kv[1])
        return [(tag, score) for tag, score in ranked if score > 0][:limit]

    # --- Santé des sources ---------------------------------------------------

    def record_source(self, name: str, ok: bool, detail: str = "") -> None:
        current = self.store.get(SOURCES, name)
        value = current.value if current else {"ok": 0, "failures": 0, "last_error": ""}
        if ok:
            value["ok"] += 1
        else:
            value["failures"] += 1
            value["last_error"] = detail[:300]
        value["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.store.put(SOURCES, name, value)

    def source_health(self) -> dict[str, dict]:
        return {item.key: item.value for item in self.store.search(SOURCES, limit=100)}

    # --- Centres d'intérêt (Grill-me) ------------------------------------------------

    def set_interests(self, profile: dict) -> None:
        self.store.put(INTERESTS, "current", profile)
        self.store.put(INTERESTS, f"history-{profile.get('answered_at', '')}", profile)

    def interests(self) -> dict | None:
        item = self.store.get(INTERESTS, "current")
        return item.value if item else None

    # --- Restitution -----------------------------------------------------------

    def prompt_context(self) -> str:
        lines = [f"- {lesson['note']}" for lesson in self.lessons()]
        if interests := self.interests():
            lines.append("- Centres d'intérêt déclarés (Grill-me) : " + interests.get("summary", ""))
            if interests.get("keywords"):
                lines.append("- Mots-clés à privilégier : " + ", ".join(interests["keywords"][:15]))
            if interests.get("exclusions"):
                lines.append("- À écarter : " + ", ".join(interests["exclusions"][:10]))
        tags = ", ".join(tag for tag, _ in self.top_tags())
        if tags:
            lines.append(f"- Thèmes retenus par le passé (préférences) : {tags}")
        return "\n".join(lines) or "Aucun."

    def export_markdown(self, path: Path) -> Path:
        lessons = self.lessons(limit=10)
        tags = self.top_tags(limit=12)
        health = self.source_health()
        lines = [
            "# Mémoire de veille (générée — ne pas éditer à la main)",
            "",
            "Export du Store LangGraph par `python -m app.main memory --export`.",
            "",
            "## Leçons des validations humaines",
            "",
            *([f"- {'✅' if l['approved'] else '❌'} {l['note']}" for l in lessons] or ["- Aucune."]),
            "",
            "## Thèmes privilégiés",
            "",
            *([f"- {tag} ({score})" for tag, score in tags] or ["- Aucun."]),
            "",
            "## Centres d'intérêt (Grill-me)",
            "",
            *(self._interests_lines() or ["- Aucun entretien Grill-me."]),
            "",
            "## Santé des sources",
            "",
            *(
                [
                    f"- {name} : {v['ok']} OK / {v['failures']} échec(s)"
                    + (f" — dernier : {v['last_error']}" if v["failures"] else "")
                    for name, v in sorted(health.items())
                ]
                or ["- Aucune donnée."]
            ),
            "",
        ]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    def _interests_lines(self) -> list[str]:
        interests = self.interests()
        if not interests:
            return []
        return [
            f"- Résumé : {interests.get('summary', '')}",
            f"- Priorités : {', '.join(interests.get('priorities', []))}",
            f"- Mots-clés : {', '.join(interests.get('keywords', []))}",
            f"- À écarter : {', '.join(interests.get('exclusions', []))}",
            f"- Entretien du {interests.get('answered_at', '')[:10]}",
        ]
