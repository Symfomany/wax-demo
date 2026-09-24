"""Ajout et retrait de sources de veille par URL (procédure du skill `ajout-source`, automatisée).

1. `inspect_source(url)` identifie le type de source et la VÉRIFIE sans rien écrire :
   - dépôt GitHub (`github.com/owner/repo`) → releases publiées via l'API GitHub ;
   - catégorie arXiv (`arxiv.org/list/cs.CL`, `rss.arxiv.org/rss/cs.CL`) → flux d'annonces ;
   - flux RSS/Atom direct, ou page de blog dont on découvre le flux (`<link rel="alternate">`,
     puis chemins usuels /feed, /rss.xml…).
   Une source n'est proposée que si elle répond et contient au moins une entrée datée ;
   la presse et les agrégateurs sont refusés (sources primaires uniquement).
2. `add_source(candidate)` écrit `sources.toml` en préservant commentaires et mise en forme,
   puis relit le fichier (tomllib) pour valider le résultat avant de le remplacer.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal
from urllib.parse import urljoin, urlsplit

import feedparser
import httpx
from pydantic import BaseModel

from app.collectors.rss import clean_text, parse_date
from app.config import settings
from app.review import FetchError, download, system_resolve

Kind = Literal["rss", "arxiv", "github"]
KIND_LABELS = {"rss": "flux RSS/Atom", "arxiv": "catégorie arXiv", "github": "dépôt GitHub (releases)"}

# Presse et agrégateurs : refusés (le skill ajout-source n'accepte que des sources primaires).
SECONDARY_DOMAINS = {
    "news.ycombinator.com", "reddit.com", "techcrunch.com", "theverge.com", "venturebeat.com", "wired.com",
    "zdnet.com", "arstechnica.com", "thenextweb.com", "marktechpost.com", "analyticsvidhya.com",
    "towardsdatascience.com", "lemondeinformatique.fr", "01net.com", "numerama.com", "siecledigital.fr",
    "usine-digitale.fr", "journaldunet.com", "feedly.com", "news.google.com", "digg.com", "producthunt.com",
}
FEED_PATHS = ["feed", "feed/", "rss", "rss.xml", "feed.xml", "atom.xml", "index.xml", "blog/rss.xml",
              "blog/feed.xml", "blog/feed", "news/rss.xml", "feeds/posts/default"]
GITHUB_REPO = re.compile(r"^https?://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?(?:/.*)?$")
ARXIV_CATEGORY = re.compile(r"^https?://(?:www\.|export\.|rss\.)?arxiv\.org/(?:list|rss)/([a-z-]+(?:\.[A-Za-z-]+)?)", re.I)
STALE_AFTER = timedelta(days=180)


class SourceError(ValueError):
    pass


class SourceCandidate(BaseModel):
    kind: Kind
    name: str
    value: str  # URL du flux, ou « owner/repo »
    input_url: str
    entries: int
    sample_title: str
    sample_link: str
    sample_date: str | None
    warnings: list[str] = []
    already_present: bool = False

    @property
    def label(self) -> str:
        return KIND_LABELS[self.kind]


# --- Lecture de sources.toml ---------------------------------------------------------------


def load(path: Path | None = None) -> dict:
    with (path or settings.sources_path).open("rb") as handle:
        return tomllib.load(handle)


def list_sources(path: Path | None = None) -> dict:
    sources = load(path)
    return {
        "rss": [{"name": s["name"], "url": s["url"]} for s in sources.get("rss", [])],
        "arxiv": list(sources.get("arxiv", {}).get("feeds", [])),
        "github": list(sources.get("github", {}).get("repositories", [])),
        "github_mcp": list(sources.get("github_mcp", {}).get("queries", [])),
        "blog": [{"name": s["name"], "url": s["url"]} for s in sources.get("blog", [])],
        "news_rss": list(sources.get("news", {}).get("rss", [])),
    }


def is_present(kind: Kind, value: str, path: Path | None = None) -> bool:
    current = list_sources(path)
    if kind == "rss":
        return any(s["url"].rstrip("/") == value.rstrip("/") for s in current["rss"])
    return value.lower() in {v.lower() for v in current[kind]}


# --- Vérification ---------------------------------------------------------------------------


class _FeedLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "link" and "alternate" in a.get("rel", "").lower() and \
                any(t in a.get("type", "").lower() for t in ("rss", "atom", "xml")) and a.get("href"):
            self.links.append(a["href"])


def _domain(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def _check_primary(url: str) -> None:
    domain = _domain(url)
    if any(domain == d or domain.endswith("." + d) for d in SECONDARY_DOMAINS):
        raise SourceError(f"{domain} est un site de presse ou un agrégateur : la veille n'accepte que des "
                          "sources primaires (blog officiel, release notes, arXiv, dépôt de l'éditeur).")


def _dated_entries(feed) -> list[tuple[datetime, dict]]:
    dated = []
    for entry in feed.entries:
        date = parse_date(entry.get("published") or entry.get("updated"))
        if date and entry.get("link"):
            dated.append((date, entry))
    return sorted(dated, key=lambda item: item[0], reverse=True)


def _feed_candidate(kind: Kind, feed_url: str, text: str, input_url: str, now: datetime) -> SourceCandidate | None:
    feed = feedparser.parse(text)
    if not feed.entries:
        return None
    dated = _dated_entries(feed)
    if not dated:
        raise SourceError(f"Le flux {feed_url} contient {len(feed.entries)} entrée(s) mais aucune n'est datée : "
                          "impossible de respecter la fenêtre de fraîcheur de la veille.")
    date, entry = dated[0]
    warnings = []
    if now - date > STALE_AFTER:
        warnings.append(f"Flux peu actif : dernière entrée du {date:%d/%m/%Y}.")
    name = clean_text(feed.feed.get("title", "")) or _domain(feed_url)
    return SourceCandidate(
        kind=kind, name=name[:80], value=feed_url, input_url=input_url, entries=len(feed.entries),
        sample_title=clean_text(entry.get("title", ""))[:200], sample_link=entry["link"],
        sample_date=date.date().isoformat(), warnings=warnings,
    )


def _inspect_github(owner: str, repo: str, input_url: str, now: datetime,
                    client: httpx.Client | None) -> SourceCandidate:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
    base = settings.github_api_url or "https://api.github.com"
    own = client is None
    client = client or httpx.Client(timeout=20)
    try:
        response = client.get(f"{base}/repos/{owner}/{repo}/releases", params={"per_page": 5}, headers=headers)
    except httpx.HTTPError as error:
        raise SourceError(f"API GitHub injoignable : {error}") from error
    finally:
        if own:
            client.close()
    if response.status_code == 404:
        raise SourceError(f"Dépôt GitHub introuvable : {owner}/{repo}")
    if response.status_code in {403, 429}:
        raise SourceError("Quota de l'API GitHub atteint : définissez GITHUB_TOKEN puis réessayez.")
    if response.status_code >= 400:
        raise SourceError(f"API GitHub : HTTP {response.status_code}")
    releases = [r for r in response.json() if not r.get("draft")]
    if not releases:
        raise SourceError(f"{owner}/{repo} n'a publié aucune release : ajoutez plutôt une requête de "
                          "découverte MCP ([github_mcp].queries) ou suivez son blog.")
    latest = releases[0]
    date = parse_date(latest.get("published_at"))
    warnings = [f"Dépôt peu actif : dernière release du {date:%d/%m/%Y}."] if date and now - date > STALE_AFTER else []
    return SourceCandidate(
        kind="github", name=f"{owner}/{repo}", value=f"{owner}/{repo}", input_url=input_url, entries=len(releases),
        sample_title=latest.get("name") or latest.get("tag_name", ""), sample_link=latest.get("html_url", ""),
        sample_date=date.date().isoformat() if date else None, warnings=warnings,
    )


def inspect_source(url: str, client: httpx.Client | None = None,
                   resolve: Callable[[str], list[str]] = system_resolve,
                   now: datetime | None = None) -> SourceCandidate:
    """Identifie et vérifie une source ; lève SourceError avec un message actionnable."""
    url = url.strip()
    now = now or datetime.now(timezone.utc)
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    if match := GITHUB_REPO.match(url):
        candidate = _inspect_github(match.group(1), match.group(2), url, now, client)
    else:
        _check_primary(url)
        try:
            if match := ARXIV_CATEGORY.match(url):
                feed_url = f"https://rss.arxiv.org/rss/{match.group(1)}"
                _, _, text = download(feed_url, client, resolve, allowed_types=None)
                candidate = _feed_candidate("arxiv", feed_url, text, url, now)
                if candidate is None:
                    raise SourceError(f"Catégorie arXiv sans annonce : {match.group(1)}")
                candidate.name = f"arXiv {match.group(1)}"
            else:
                candidate = _discover_feed(url, client, resolve, now)
        except FetchError as error:
            raise SourceError(str(error)) from error
    candidate.already_present = is_present(candidate.kind, candidate.value)
    return candidate


def _discover_feed(url: str, client, resolve, now: datetime) -> SourceCandidate:
    final_url, content_type, text = download(url, client, resolve, allowed_types=None)
    direct = _feed_candidate("rss", final_url, text, url, now)
    if direct:
        return direct
    if "html" not in content_type:
        raise SourceError(f"Ni flux RSS/Atom ni page HTML ({content_type}).")
    parser = _FeedLinks()
    parser.feed(text)
    root = f"{urlsplit(final_url).scheme}://{urlsplit(final_url).netloc}/"
    base = final_url if final_url.endswith("/") else final_url + "/"
    tried: list[str] = []
    for href in [*parser.links, *(urljoin(base, p) for p in FEED_PATHS), *(urljoin(root, p) for p in FEED_PATHS)]:
        feed_url = urljoin(final_url, href)
        if feed_url in tried or len(tried) >= 12:
            continue
        tried.append(feed_url)
        if _domain(feed_url) != _domain(final_url):
            _check_primary(feed_url)  # flux hébergé ailleurs (feedburner…)
        try:
            real_url, _, feed_text = download(feed_url, client, resolve, allowed_types=None)
        except FetchError:
            continue
        candidate = _feed_candidate("rss", real_url, feed_text, url, now)
        if candidate:
            if not parser.links:
                candidate.warnings.append(f"Flux deviné ({real_url}) : aucun lien RSS déclaré par la page.")
            return candidate
    raise SourceError("Aucun flux RSS/Atom trouvé pour cette page (ni <link rel=\"alternate\">, ni /feed, /rss.xml…). "
                      "Collez directement l'URL du flux, d'un dépôt GitHub ou d'une catégorie arXiv.")


# --- Écriture de sources.toml (texte préservé) ------------------------------------------------


def _string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)  # chaîne TOML « basique » valide


def _insert_in_array(text: str, table: str, key: str, value: str) -> str:
    section = re.search(rf"^\[{re.escape(table)}\]\s*$", text, re.M)
    if not section:
        return text.rstrip("\n") + f"\n\n[{table}]\n{key} = [\n    {_string(value)},\n]\n"
    array = re.compile(rf"^{re.escape(key)}\s*=\s*\[", re.M).search(text, section.end())
    next_table = re.compile(r"^\[", re.M).search(text, section.end())
    if not array or (next_table and array.start() > next_table.start()):
        return text[:section.end()] + f"\n{key} = [\n    {_string(value)},\n]" + text[section.end():]
    close = text.index("]", array.end())
    inner = text[array.end():close].rstrip()
    separator = "" if not inner.strip() or inner.endswith(",") else ","
    return text[:array.end()] + inner + f"{separator}\n    {_string(value)},\n" + text[close:]


def _insert_rss(text: str, name: str, url: str) -> str:
    block = f"[[rss]]\nname = {_string(name)}\nurl = {_string(url)}\n"
    headers = list(re.finditer(r"^\[\[rss\]\]\s*$", text, re.M))
    if not headers:
        return text.rstrip("\n") + "\n\n" + block
    lines = text[headers[-1].end():].split("\n")
    offset = headers[-1].end()
    for line in lines[1:]:  # clés du dernier bloc [[rss]]
        if not line.strip() or line.lstrip().startswith(("[", "#")):
            break
        offset += len(line) + 1
    offset += 1
    return text[:offset] + "\n" + block + text[offset:]


def _remove_rss(text: str, url: str) -> str:
    pattern = re.compile(r"^\[\[rss\]\]\s*\n(?:(?!\[)[^\n#]*=[^\n]*\n)*", re.M)
    for match in pattern.finditer(text):
        if f"url = {_string(url)}" in match.group(0) or f"url = '{url}'" in match.group(0):
            return text[:match.start()] + text[match.end():].lstrip("\n")
    raise SourceError(f"Flux absent de sources.toml : {url}")


def _remove_from_array(text: str, value: str) -> str:
    quoted = re.escape(_string(value))
    new, count = re.subn(rf"^[ \t]*{quoted},?[ \t]*\n", "", text, flags=re.M)
    if not count:
        new, count = re.subn(rf"{quoted},?\s*", "", text)
    if not count:
        raise SourceError(f"Source absente de sources.toml : {value}")
    return new


def _write(text: str, path: Path, check: Callable[[dict], bool]) -> None:
    try:
        parsed = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise SourceError(f"Écriture annulée : sources.toml deviendrait invalide ({error})") from error
    if not check(parsed):
        raise SourceError("Écriture annulée : la modification n'apparaît pas dans sources.toml relu.")
    temporary = path.with_suffix(".toml.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def add_source(candidate: SourceCandidate, name: str | None = None, path: Path | None = None) -> dict:
    path = path or settings.sources_path
    if is_present(candidate.kind, candidate.value, path):
        raise SourceError(f"Déjà présente dans sources.toml : {candidate.value}")
    text = path.read_text(encoding="utf-8")
    if candidate.kind == "rss":
        label = (name or candidate.name).strip() or candidate.name
        text = _insert_rss(text, label, candidate.value)
    elif candidate.kind == "arxiv":
        text = _insert_in_array(text, "arxiv", "feeds", candidate.value)
    else:
        text = _insert_in_array(text, "github", "repositories", candidate.value)
    _write(text, path, lambda parsed: is_present_in(parsed, candidate.kind, candidate.value))
    return list_sources(path)


def remove_source(kind: Kind, value: str, path: Path | None = None) -> dict:
    path = path or settings.sources_path
    text = path.read_text(encoding="utf-8")
    text = _remove_rss(text, value) if kind == "rss" else _remove_from_array(text, value)
    _write(text, path, lambda parsed: not is_present_in(parsed, kind, value))
    return list_sources(path)


def is_present_in(parsed: dict, kind: Kind, value: str) -> bool:
    if kind == "rss":
        return any(s.get("url") == value for s in parsed.get("rss", []))
    table, key = ("arxiv", "feeds") if kind == "arxiv" else ("github", "repositories")
    return value in parsed.get(table, {}).get(key, [])
