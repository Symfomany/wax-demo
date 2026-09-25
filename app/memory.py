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

Les souvenirs sont typés (`MemoryRecord`) : type, provenance, confiance et
expiration. Une leçon expirée n'est plus injectée dans les prompts ; une règle
suggérée après des rejets récurrents n'agit qu'une fois acceptée par l'humain.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from langgraph.store.base import BaseStore
from pydantic import BaseModel, Field


LESSONS = ("memory", "lessons")
TAGS = ("memory", "tags")
SOURCES = ("memory", "sources")
INTERESTS = ("memory", "interests")
RULES = ("memory", "rules")

# Tags purement techniques, non informatifs pour les préférences.
IGNORED_TAGS = {"rss", "arxiv", "github", "github-mcp", "prerelease"}

MemoryKind = Literal["lesson", "preference", "rule", "profile"]


class MemoryRecord(BaseModel):
    """Souvenir typé : d'où il vient, quelle confiance lui accorder, jusqu'à quand."""

    key: str = ""
    kind: MemoryKind
    content: str
    provenance: str  # « run:<id> », « grill:<date> », « feedback:… », « profile:<label> »
    confidence: float = Field(0.5, ge=0, le=1)
    created_at: datetime
    expires_at: datetime | None = None
    status: Literal["active", "suggested", "dismissed"] = "active"
    data: dict = Field(default_factory=dict)

    def expired(self, now: datetime) -> bool:
        return self.expires_at is not None and self.expires_at <= now


def _now() -> datetime:
    return datetime.now(timezone.utc)


def lesson_record(key: str, value: dict) -> MemoryRecord:
    """Leçon stockée → MemoryRecord (les leçons antérieures n'ont ni confiance ni expiration)."""
    return MemoryRecord(
        key=key, kind="lesson", content=value["note"], provenance=value.get("provenance") or f"run:{value.get('run_id', '')}",
        confidence=value.get("confidence", 0.9), created_at=value["created_at"], expires_at=value.get("expires_at"),
        data={"approved": value.get("approved", False)},
    )


class WatchMemory:
    def __init__(self, store: BaseStore) -> None:
        self.store = store

    # --- Leçons (retours humains) -------------------------------------------

    def add_lesson(self, note: str, approved: bool, run_id: str, ttl_days: int | None = 180) -> None:
        if not note.strip():
            return
        now = _now()
        self.store.put(
            LESSONS,
            str(uuid4()),
            {
                "note": note.strip(),
                "approved": approved,
                "run_id": run_id,
                "created_at": now.isoformat(),
                # Retour humain explicite : forte confiance, mais les goûts évoluent (expiration).
                "kind": "lesson",
                "provenance": f"run:{run_id}",
                "confidence": 0.9,
                "expires_at": (now + timedelta(days=ttl_days)).isoformat() if ttl_days else None,
            },
        )

    def lessons(self, limit: int = 5, now: datetime | None = None) -> list[dict]:
        """Leçons non expirées, les plus récentes d'abord."""
        now = now or _now()
        items = self.store.search(LESSONS, limit=200)
        alive = [item.value for item in items if not lesson_record(item.key, item.value).expired(now)]
        ordered = sorted(alive, key=lambda v: v["created_at"], reverse=True)
        return ordered[:limit]

    # --- Préférences de thèmes ----------------------------------------------

    def reinforce_tags(self, tags: list[str], weight: int = 1) -> None:
        for tag in {t.lower() for t in tags} - IGNORED_TAGS:
            if "/" in tag:  # dépôt « owner/repo » : trop spécifique
                continue
            current = self.store.get(TAGS, tag)
            value = current.value if current else {"score": 0}
            value["score"] = value.get("score", 0) + weight
            self.store.put(TAGS, tag, value)

    def count_outcome(self, tags: list[str], approved: bool) -> None:
        """Une voix par digest et par thème (validé ou rejeté) : base des suggestions de règles."""
        outcome = "approved" if approved else "rejected"
        for tag in {t.lower() for t in tags} - IGNORED_TAGS:
            if "/" in tag:
                continue
            current = self.store.get(TAGS, tag)
            value = current.value if current else {"score": 0}
            value[outcome] = value.get(outcome, 0) + 1
            self.store.put(TAGS, tag, value)

    # --- Règles suggérées après des rejets récurrents ------------------------------------

    def suggest_rules(self, min_rejections: int = 3, ttl_days: int = 30) -> list[MemoryRecord]:
        """Un thème présent dans ≥ min_rejections digests rejetés, et au moins deux fois plus rejeté
        qu'approuvé, devient une règle d'exclusion *suggérée* (jamais active sans accord humain)."""
        now, created = _now(), []
        for item in self.store.search(TAGS, limit=500):
            rejected, approved = item.value.get("rejected", 0), item.value.get("approved", 0)
            key = f"exclude:{item.key}"
            if rejected < min_rejections or rejected < 2 * approved or self.store.get(RULES, key):
                continue
            record = MemoryRecord(
                key=key, kind="rule", content=f"Écarter les documents du thème ou de la source « {item.key} »",
                provenance=f"feedback:{rejected} rejet(s), {approved} validation(s)",
                confidence=round(rejected / (rejected + approved), 2), created_at=now,
                expires_at=now + timedelta(days=ttl_days), status="suggested",
                data={"action": "exclude", "term": item.key, "rejected": rejected, "approved": approved},
            )
            self.store.put(RULES, key, record.model_dump(mode="json"))
            created.append(record)
        return created

    def rules(self, now: datetime | None = None) -> list[MemoryRecord]:
        """Règles actives et suggestions non expirées (une règle acceptée n'expire pas)."""
        now = now or _now()
        records = [MemoryRecord.model_validate(item.value) for item in self.store.search(RULES, limit=200)]
        return sorted((r for r in records if r.status != "dismissed" and not r.expired(now)),
                      key=lambda r: (r.status != "active", -r.confidence))

    def decide_rule(self, key: str, accept: bool) -> MemoryRecord:
        item = self.store.get(RULES, key)
        if item is None:
            raise KeyError(key)
        record = MemoryRecord.model_validate(item.value)
        update = ({"status": "active", "expires_at": None, "provenance": f"{record.provenance} · acceptée"}
                  if accept else {"status": "dismissed"})
        record = record.model_copy(update=update)
        self.store.put(RULES, key, record.model_dump(mode="json"))
        return record

    def active_exclusions(self) -> list[str]:
        return [r.data["term"] for r in self.rules() if r.status == "active" and r.data.get("action") == "exclude"]

    # --- Vue unifiée (page « Pourquoi cette veille ? ») -----------------------------------

    def records(self, now: datetime | None = None) -> list[MemoryRecord]:
        """Leçons, règles et centres d'intérêt en souvenirs typés, sans les expirés."""
        now = now or _now()
        records = [lesson_record(item.key, item.value) for item in self.store.search(LESSONS, limit=200)]
        records = [r for r in records if not r.expired(now)]
        if interests := self.interests():
            answered = interests.get("answered_at") or now.isoformat()
            records.append(MemoryRecord(
                key="interests", kind="preference", content=interests.get("summary", ""),
                provenance=f"grill:{answered[:10]}", confidence=0.8, created_at=answered,
                data={"keywords": interests.get("keywords", []), "exclusions": interests.get("exclusions", [])},
            ))
        return [*records, *self.rules(now)]

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
        if exclusions := self.active_exclusions():
            lines.append("- Règles acceptées, à écarter : " + ", ".join(exclusions[:10]))
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
            "## Règles issues des retours",
            "",
            *([f"- {'✅ active' if r.status == 'active' else '💡 suggérée'} : {r.content} ({r.provenance})"
               for r in self.rules()] or ["- Aucune."]),
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
