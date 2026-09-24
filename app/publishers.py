"""Outils de publication déclenchés après validation d'un digest.

Ordre : fichiers (JSON + Markdown + mémoire SQLite) → rapport daté → Notion.
Seuls les fichiers sont critiques ; un échec du rapport ou de Notion est
consigné dans les erreurs du run sans annuler la publication.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from app import storage
from app.renderers.markdown import render_markdown
from app.reports import render_report, report_path


@dataclass
class Publication:
    run_id: str
    digest: dict
    critiques: list[dict]
    connection: object
    output_dir: Path
    reports_dir: Path
    templates_dir: Path
    model: str = ""
    collected: dict[str, int] = field(default_factory=dict)
    trace: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


Publisher = tuple[str, Callable[[Publication], dict], bool]  # (nom, outil, critique)


def publish_files(publication: Publication) -> dict:
    publication.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = publication.digest["generated_at"][:19].replace("-", "").replace(":", "").replace("T", "-")
    markdown_path = publication.output_dir / f"digest-{stamp}.md"
    json_path = publication.output_dir / f"digest-{stamp}.json"
    markdown_path.write_text(render_markdown(publication.digest, publication.critiques), encoding="utf-8")
    json_path.write_text(json.dumps(publication.digest, indent=2, ensure_ascii=False), encoding="utf-8")
    digest_id = storage.record_digest(
        publication.connection, publication.run_id, publication.digest, markdown_path, json_path
    )
    return {"markdown": str(markdown_path), "json": str(json_path), "digest_id": digest_id}


def publish_report(publication: Publication) -> dict:
    """Rapport daté en deux formats : Markdown (partage, Git) et HTML (lecture)."""
    path = report_path(publication.reports_dir, publication.digest["generated_at"])
    path.parent.mkdir(parents=True, exist_ok=True)
    context = dict(
        digest=publication.digest,
        critiques=publication.critiques,
        run_id=publication.run_id,
        model=publication.model,
        collected=publication.collected,
        trace=publication.trace,
        errors=publication.errors,
    )
    path.write_text(render_report(publication.templates_dir, "md", **context), encoding="utf-8")
    html_path = path.with_suffix(".html")
    html_path.write_text(render_report(publication.templates_dir, "html", **context), encoding="utf-8")
    return {"report": str(path), "report_html": str(html_path)}


def notion_publisher(sync: Callable) -> Callable[[Publication], dict]:
    def publish_notion(publication: Publication) -> dict:
        result = sync(publication.connection)
        return {"notion": result["url"]}

    return publish_notion


def default_publishers(notion_sync: Callable | None = None) -> list[Publisher]:
    publishers: list[Publisher] = [
        ("fichiers", publish_files, True),
        ("rapport", publish_report, False),
    ]
    if notion_sync is not None:
        publishers.append(("notion", notion_publisher(notion_sync), False))
    return publishers


def run_publishers(publication: Publication, publishers: list[Publisher]) -> tuple[dict, list[str]]:
    outputs: dict = {}
    errors: list[str] = []
    for name, publisher, critical in publishers:
        try:
            outputs.update(publisher(publication))
        except Exception as error:  # noqa: BLE001 — un outil secondaire ne bloque pas
            if critical:
                raise
            errors.append(f"publication {name} : {error}")
    return outputs, errors
