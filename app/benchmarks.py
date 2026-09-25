"""Benchmarks LLM (onglet 📊 Benchmarks) : catalogue et classements de BenchLM.ai.

- catalogue : une seule page (/benchmarks) dont le JSON Next.js (`__NEXT_DATA__`) décrit chaque
  benchmark (nom, description, tâches, format, difficulté, année, catégorie) ;
- détail à la demande (/benchmarks/<clé>) : papier source, précisions, classement des modèles et
  « trust card » de BenchLM (saturation, provenance des scores, fraîcheur), mis en cache en base.

robots.txt de benchlm.ai : tout est autorisé sauf /api/ et /admin/ — seules les pages publiques sont
lues, avec la protection SSRF et la taille bornée de la Review. Aucun chiffre n'est produit par un LLM :
l'analyse (`analyze`) ne reformule que les valeurs extraites, avec BenchLM comme source citée.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.config import settings
from app.news import Fetch, NewsError, default_fetch

_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_ANCHOR = re.compile(r'<a[^>]*href="/benchmarks/([^"#?/]+)"[^>]*>(.*?)</a>', re.S)
KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")  # identifiant BenchLM (ex. sciCode, mrcrv2_64_128)
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,99}$")  # chemin de la page (ex. scicode, mrcr-v2-64k-128k)


class BenchmarkError(NewsError):
    pass


class Benchmark(BaseModel):
    key: str = Field(pattern=KEY.pattern)
    slug: str | None = Field(None, pattern=SLUG.pattern)  # None : pas de page de détail repérée
    name: str = Field(min_length=1, max_length=120)
    full_name: str = Field("", max_length=300)
    category: str = Field(max_length=40)  # clé BenchLM (agentic, coding…)
    category_name: str = Field("", max_length=60)
    description: str = Field("", max_length=1500)
    tasks: str = Field("", max_length=400)
    format: str = Field("", max_length=200)
    difficulty: str = Field("", max_length=200)
    year: str = Field("", max_length=10)

    @property
    def url(self) -> str:
        return f"{settings.benchmarks_url}/benchmarks" + (f"/{self.slug}" if self.slug else "")

    def record(self) -> dict:
        return self.model_dump() | {"url": self.url}


class LeaderboardRow(BaseModel):
    model: str = Field(max_length=120)
    creator: str = Field("", max_length=80)
    source_type: str = Field("", max_length=40)  # Proprietary, Open Weight, Pending…
    score: float | None = None
    context_window: str | None = Field(None, max_length=20)


class BenchmarkDetail(BaseModel):
    key: str = Field(pattern=KEY.pattern)
    slug: str = Field(pattern=SLUG.pattern)
    paper_url: str | None = None
    paper_title: str = Field("", max_length=300)
    authors: str = Field("", max_length=300)
    details: str = Field("", max_length=3000)
    last_updated: str = Field("", max_length=40)
    leaderboard: list[LeaderboardRow] = Field(default_factory=list, max_length=300)
    saturation: dict = Field(default_factory=dict)  # state, flag, topThreeSpread, topTenSpread, text
    provenance: dict = Field(default_factory=dict)  # flag, providerPct, independentPct, total, text
    freshness: dict = Field(default_factory=dict)  # stalenessState, refreshCadence, questionAvailability, text

    @field_validator("paper_url")
    @classmethod
    def https_only(cls, value: str | None) -> str | None:
        return value if value and value.startswith("https://") and len(value) < 2000 else None


def next_data(page: str) -> dict:
    match = _NEXT_DATA.search(page)
    if not match:
        raise BenchmarkError("Données de la page BenchLM introuvables (structure du site modifiée ?).")
    try:
        return json.loads(match.group(1))["props"]["pageProps"]
    except (json.JSONDecodeError, KeyError) as error:
        raise BenchmarkError(f"Données BenchLM illisibles : {error}") from error


# --- Catalogue ------------------------------------------------------------------------------------


@dataclass
class CatalogReport:
    items: list[Benchmark] = field(default_factory=list)
    total_announced: int = 0
    period: str = ""
    invalid: int = 0


def page_slugs(page: str) -> dict[str, str]:
    """Liens des cartes du catalogue : chemin → texte de la carte (« 2026 DRACO Data Research… »)."""
    found: dict[str, str] = {}
    for slug, inner in _ANCHOR.findall(page):
        text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", inner))).strip()
        if SLUG.match(slug) and text:
            found.setdefault(slug, re.sub(r"^\d{4}\s+", "", text))
    return found


def resolve_slug(key: str, name: str, slugs: dict[str, str]) -> str | None:
    """Chemin de la page d'un benchmark : la clé si elle existe telle quelle, sinon l'unique carte dont
    le texte commence par son nom. Jamais deviné : None si ambigu ou absent."""
    if key.lower() in slugs:
        return key.lower()
    hits = [slug for slug, text in slugs.items() if text == name or text.startswith(f"{name} ")]
    return hits[0] if len(hits) == 1 else None


def _text(value: Any, limit: int) -> str:
    return str(value)[:limit] if isinstance(value, (str, int, float)) and not isinstance(value, bool) else ""


def parse_catalog(page: str) -> CatalogReport:
    props = next_data(page)
    slugs = page_slugs(page)
    report = CatalogReport(total_announced=int(props.get("totalBenchmarks") or 0),
                           period=str(props.get("pagePeriodLabel") or "")[:40])
    seen: set[str] = set()
    for category in props.get("categories", []):
        for raw in category.get("benchmarks", []):
            key, name = _text(raw.get("key"), 80), _text(raw.get("name"), 120)
            try:
                item = Benchmark(
                    key=key, slug=resolve_slug(key, name, slugs), name=name,
                    full_name=_text(raw.get("fullName"), 300), category=category.get("key", ""),
                    category_name=category.get("name", ""), description=_text(raw.get("description"), 1500),
                    tasks=_text(raw.get("tasks"), 400), format=_text(raw.get("format"), 200),
                    difficulty=_text(raw.get("difficulty"), 200), year=_text(raw.get("year"), 10),
                )
            except ValidationError:
                report.invalid += 1
                continue
            if item.key not in seen:  # un benchmark listé dans deux catégories garde la première
                seen.add(item.key)
                report.items.append(item)
    if not report.items:
        raise BenchmarkError("Aucun benchmark reconnu sur la page BenchLM.")
    return report


def crawl_catalog(fetch: Fetch = default_fetch) -> CatalogReport:
    return parse_catalog(fetch(f"{settings.benchmarks_url}/benchmarks"))


# --- Détail d'un benchmark ------------------------------------------------------------------------------


def parse_detail(key: str, slug: str, page: str) -> BenchmarkDetail:
    props = next_data(page)
    bench = props.get("benchmark") or {}
    trust = props.get("trustCard") or {}
    rows = []
    for raw in props.get("leaderboard") or []:
        try:
            rows.append(LeaderboardRow(model=raw.get("model") or "", creator=raw.get("creator") or "",
                                       source_type=raw.get("sourceType") or "",
                                       score=raw.get("score") if isinstance(raw.get("score"), (int, float)) else None,
                                       context_window=raw.get("contextWindow")))
        except ValidationError:
            continue
    keep = lambda d, keys: {k: d[k] for k in keys if k in d} if isinstance(d, dict) else {}  # noqa: E731
    return BenchmarkDetail(
        key=key, slug=slug, paper_url=bench.get("paperUrl"), paper_title=(bench.get("paperTitle") or "")[:300],
        authors=(bench.get("authors") or "")[:300], details=(bench.get("details") or "")[:3000],
        last_updated=str(props.get("lastUpdated") or "")[:40], leaderboard=rows[:300],
        saturation=keep(trust.get("saturation"), ("state", "flag", "topThreeSpread", "topTenSpread", "text")),
        provenance=keep(trust.get("provenance"), ("flag", "providerPct", "independentPct", "total", "text")),
        freshness=keep(trust.get("freshness"), ("stalenessState", "refreshCadence", "questionAvailability", "text")),
    )


def fetch_detail(key: str, slug: str | None, fetch: Fetch = default_fetch) -> BenchmarkDetail:
    if not KEY.match(key) or not slug or not SLUG.match(slug):
        raise BenchmarkError("Pas de page de détail connue pour ce benchmark sur BenchLM.")
    return parse_detail(key, slug, fetch(f"{settings.benchmarks_url}/benchmarks/{slug}"))


# --- Analyse (déterministe, à partir des seules valeurs extraites) ---------------------------------------------

OPEN = ("open weight", "open source", "open-weight")
SATURATION_NOTES = {
    "separating": " — le classement départage encore bien les modèles.",
    "clustering": " — les meilleurs modèles sont au coude-à-coude : un petit écart n'est pas significatif.",
    "saturated": " — benchmark saturé : il ne départage plus les modèles de pointe.",
}


def analyze(benchmark: dict, detail: dict | None) -> dict:
    """Lecture guidée d'un benchmark : leader, meilleur modèle à poids ouverts, écart, fiabilité des scores."""
    if not detail:
        return {"points": [], "leader": None, "open_leader": None, "models": 0}
    rows = [r for r in detail.get("leaderboard", []) if r.get("score") is not None]
    rows.sort(key=lambda r: r["score"], reverse=True)
    leader = rows[0] if rows else None
    open_leader = next((r for r in rows if r.get("source_type", "").lower() in OPEN), None)
    points: list[str] = []
    if leader:
        points.append(f"En tête : {leader['model']} ({leader['creator']}) avec {leader['score']:g}.")
    if open_leader and open_leader is not leader:
        gap = leader["score"] - open_leader["score"]
        points.append(f"Meilleur modèle à poids ouverts : {open_leader['model']} ({open_leader['score']:g}), "
                      f"à {gap:.1f} point(s) du leader.")
    elif open_leader:
        points.append("Le leader est un modèle à poids ouverts.")
    elif rows:
        points.append("Aucun modèle à poids ouverts classé sur ce benchmark.")
    saturation = detail.get("saturation") or {}
    if saturation.get("state"):
        spread = saturation.get("topThreeSpread")
        points.append(f"Saturation : {saturation['state']}"
                      + (f" (écart du top 3 : {spread:.1f} pt)" if isinstance(spread, (int, float)) else "")
                      + SATURATION_NOTES.get(saturation.get("flag"), "."))
    provenance = detail.get("provenance") or {}
    if isinstance(provenance.get("providerPct"), (int, float)):
        points.append(f"Provenance : {provenance['providerPct']:.0f} % des scores sont déclarés par les éditeurs eux-mêmes"
                      + (" — à confirmer par une évaluation indépendante." if provenance["providerPct"] >= 50 else "."))
    freshness = detail.get("freshness") or {}
    if freshness.get("stalenessState"):
        points.append(f"Fraîcheur : {freshness['stalenessState']}"
                      + (f", mise à jour {freshness['refreshCadence'].lower()}" if freshness.get("refreshCadence") else "")
                      + (f" ; questions : {freshness['questionAvailability'].lower()}" if freshness.get("questionAvailability") else "") + ".")
    return {"points": points, "leader": leader, "open_leader": open_leader, "models": len(rows)}


def catalog_overview(items: list[dict]) -> dict:
    """Répartition du catalogue : par catégorie, par année, part des benchmarks récents."""
    by_category: dict[str, int] = {}
    by_year: dict[str, int] = {}
    for item in items:
        by_category[item.get("category_name") or item["category"]] = by_category.get(item.get("category_name") or item["category"], 0) + 1
        if item.get("year"):
            by_year[item["year"]] = by_year.get(item["year"], 0) + 1
    return {"total": len(items), "by_category": dict(sorted(by_category.items(), key=lambda kv: -kv[1])),
            "by_year": dict(sorted(by_year.items(), reverse=True))}


def record_with_analysis(record: dict) -> dict[str, Any]:
    detail = record.get("detail")
    return record | {"analysis": analyze(record, detail)}
