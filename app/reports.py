"""Rapports de veille datés : Markdown soigné rendu depuis templates/report.md.j2."""

from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from app.keywords import SOURCE_TAGS, extract_keywords

WEEKDAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MONTHS = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]
SOURCE_LABELS = {"rss": "📰 Blog officiel", "arxiv": "📄 arXiv", "github": "🐙 GitHub"}


def french_date(moment: datetime) -> str:
    return f"{WEEKDAYS[moment.weekday()]} {moment.day} {MONTHS[moment.month - 1]} {moment.year}"


def _parse(value) -> datetime | None:
    if not value:
        return None
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _environment(templates_dir: Path) -> Environment:
    return Environment(
        loader=FileSystemLoader(templates_dir),
        trim_blocks=True,
        lstrip_blocks=True,
        undefined=StrictUndefined,  # variable oubliée = erreur, pas un trou silencieux
        keep_trailing_newline=True,
    )


def report_context(
    digest: dict,
    critiques: list[dict],
    run_id: str,
    model: str,
    collected: dict[str, int] | None = None,
    trace: list[str] | None = None,
    errors: list[str] | None = None,
) -> dict:
    generated = _parse(digest["generated_at"])
    items = []
    for item in digest["items"]:
        date = _parse(item.get("date"))
        is_repo = "github-mcp" in item.get("tags", [])
        items.append(
            item
            | {
                "icon": "✨" if is_repo else SOURCE_LABELS.get(item["source"], "🔗").split()[0],
                "source_label": "✨ Nouveau dépôt GitHub" if is_repo
                else SOURCE_LABELS.get(item["source"], item["source"]),
                "date_short": date.strftime("%d/%m/%Y") if date else "non précisée",
                # tags d'affichage : ni tags de source, ni noms de flux ou de dépôt
                "tags": [t for t in item.get("tags", []) if t.lower() not in SOURCE_TAGS
                         and " " not in t and "/" not in t],
            }
        )
    return {
        "title": f"Veille LLM / GenAI — {generated:%d/%m/%Y}",
        "date_long": french_date(generated),
        "generated_at": digest["generated_at"],
        "run_id": run_id,
        "model": model,
        "digest": digest,
        "items": items,
        "top": items[:3],
        "keywords": extract_keywords(digest),
        "rejected": [c for c in critiques if c["verdict"] != "keep"],
        "collected": collected or {},
        "collected_total": sum((collected or {}).values()),
        "trace": trace or [],
        "errors": errors or [],
    }


def render_report(templates_dir: Path, **context_args) -> str:
    template = _environment(templates_dir).get_template("report.md.j2")
    return template.render(**report_context(**context_args))


def report_path(reports_dir: Path, generated_at: str) -> Path:
    moment = _parse(generated_at)
    return reports_dir / f"{moment:%Y}" / f"veille-{moment:%Y-%m-%d-%H%M}.md"


def list_reports(reports_dir: Path) -> list[Path]:
    return sorted(reports_dir.glob("*/veille-*.md"), reverse=True) if reports_dir.exists() else []
