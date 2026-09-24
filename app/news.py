"""Actus en cartes (onglet 📰 Actus) : trois collectes, un seul format validé.

- pages de blog sans flux RSS (`[[blog]]` de sources.toml, ex. claude.com/blog) : crawl des
  cartes de la page de liste, puis de la meta description de chaque article ;
- flux RSS nommés dans `[news].rss` (ex. OpenAI News) ;
- recherche web par l'API Claude (outil serveur `web_search`) sur les centres d'intérêt.

Règle du projet : le LLM ne choisit que des URL ; titre, URL et date sont recopiés depuis la
page crawlée ou depuis les résultats `web_search` — une URL absente des résultats est écartée.
Chaque actu est validée par `NewsItem` (Pydantic) avant persistance.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal
from urllib.parse import urljoin, urlsplit

from pydantic import BaseModel, Field, HttpUrl, ValidationError, field_validator

from app.collectors.rss import clean_text, fetch_feed, parse_date
from app.config import settings
from app.schemas import Document


Origin = Literal["blog", "rss", "web_search"]
WEB_SOURCE = "Web (Claude)"

# Couleurs de fond des illustrations du blog Claude (variables --swatch--* de sa feuille de style).
SWATCHES = {
    "cactus": "#bcd1ca", "clay": "#d97757", "coral": "#ebcece", "fig": "#c46686", "heather": "#cbcadb",
    "mineral": "#629987", "oat": "#e3dacc", "olive": "#788c5d", "peach": "#ebc9b7", "plum": "#827dbd",
    "sky": "#6a9bcc",
}


class NewsError(RuntimeError):
    pass


class NewsItem(BaseModel):
    id: str
    url: HttpUrl
    title: str = Field(min_length=1, max_length=300)
    source: str = Field(min_length=1, max_length=80)
    origin: Origin
    published_at: date | None = None
    summary: str = Field("", max_length=600)
    why: str = Field("", max_length=400)  # recherche web : pourquoi ça compte
    category: str = Field("", max_length=60)
    image: str | None = None  # https uniquement
    accent: str | None = None  # couleur de fond de la carte (#rrggbb)
    query: str = Field("", max_length=300)  # recherche web : sujets demandés

    @field_validator("image")
    @classmethod
    def https_image(cls, value: str | None) -> str | None:
        return value if value and value.startswith("https://") and len(value) < 1500 else None

    @field_validator("accent")
    @classmethod
    def hex_color(cls, value: str | None) -> str | None:
        return value if value and re.fullmatch(r"#[0-9a-fA-F]{6}", value) else None

    def record(self) -> dict:
        return self.model_dump(mode="json")


def news_id(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:16]


def make_item(**values: Any) -> NewsItem | None:
    """NewsItem validé, ou None si la donnée collectée est inexploitable (URL invalide, titre vide…)."""
    try:
        return NewsItem(id=news_id(str(values["url"])), **values)
    except (ValidationError, KeyError):
        return None


# --- Téléchargement (SSRF bornée, même garde-fou que la Review) --------------------------------

Fetch = Callable[[str], str]


def default_fetch(url: str) -> str:
    from app.review import download

    return download(url, allowed_types=None)[2]


# --- Pages de blog Webflow (claude.com/blog) -----------------------------------------------------

_ITEM = re.compile(r'<div[^>]*role="listitem"[^>]*class="[^"]*(?:blog_cms_item|marquee_cms_blog_list_item)')
_FIELDS = {
    "heading": re.compile(r'fs-list-field="heading"[^>]*>([^<]+)<'),
    "title": re.compile(r'class="card_blog_title[^"]*"[^>]*>([^<]+)<'),
    "h2": re.compile(r'<h[23][^>]*>([^<]+)</h[23]>'),
    "date": re.compile(r'fs-list-field="date"[^>]*>([^<]+)<'),
    "caption": re.compile(r'class="u-text-style-caption u-foreground-tertiary[^"]*">([^<]+)<'),
    "category": re.compile(r'fs-list-field="category"[^>]*>([^<]+)<'),
    "link": re.compile(r'fs-list-element="item-link"[^>]*href="([^"#]+)"'),
    "href": re.compile(r'<a[^>]*href="([^"#]+)"[^>]*class="clickable_link'),
    "image": re.compile(r'<img[^>]*src="([^"]+)"[^>]*class="card_blog_illo'),
    "bg": re.compile(r'data-illustration-bg="([^"]+)"'),
}


def parse_blog_listing(page: str, base_url: str, source: str) -> list[NewsItem]:
    """Cartes d'une page de liste Webflow (grille et bandeau « à la une ») : titre, date, catégorie,
    illustration et couleur de fond. Une URL présente deux fois garde sa carte la plus complète."""
    found_items: dict[str, NewsItem] = {}
    for chunk in _ITEM.split(page)[1:]:
        found = {name: (m.group(1).strip() if (m := pattern.search(chunk)) else "") for name, pattern in _FIELDS.items()}
        href = found["link"] or found["href"]
        title = html.unescape(found["heading"] or found["title"] or found["h2"]).strip()
        if not href or not title:
            continue
        url = urljoin(base_url, html.unescape(href))
        if urlsplit(url).netloc != urlsplit(base_url).netloc:
            continue
        raw_date = html.unescape(found["date"] or found["caption"])
        published = parse_date(raw_date) if raw_date else None
        item = make_item(
            url=url, title=title, source=source, origin="blog",
            published_at=published.date() if published else None,
            category=html.unescape(found["category"]),
            image=html.unescape(found["image"]) or None,
            accent=SWATCHES.get(found["bg"].lower()),
        )
        previous = found_items.get(url)
        if item and (previous is None or (item.image and not previous.image)):
            found_items[url] = item
    return sorted(found_items.values(), key=lambda i: i.published_at or date.min, reverse=True)


_META = re.compile(r"<meta\s[^>]*>", re.I)
_ATTR = re.compile(r'([\w:-]+)\s*=\s*"([^"]*)"')


def page_meta(page: str) -> dict[str, str]:
    """Balises <meta> d'une page (name/property → content), quel que soit l'ordre des attributs."""
    meta: dict[str, str] = {}
    for tag in _META.findall(page[:300_000]):
        attrs = {k.lower(): v for k, v in _ATTR.findall(tag)}
        key = (attrs.get("property") or attrs.get("name") or "").lower()
        if key and "content" in attrs:
            meta.setdefault(key, html.unescape(attrs["content"]))
    return meta


_PAGINATION = re.compile(r'href="\?(\w+)_page=2"')
_PAGE_PARAMS: dict[str, str] = {}  # URL de la liste → paramètre de pagination Webflow (ex. b7eea976)


def listing_page_url(source: dict, page: int, fetch: Fetch) -> str | None:
    """URL de la page `page` d'une liste Webflow (?<id>_page=N), ou None si la liste n'est pas paginée."""
    if page <= 1:
        return source["url"]
    if source["url"] not in _PAGE_PARAMS:
        match = _PAGINATION.search(fetch(source["url"]))
        if not match:
            return None
        _PAGE_PARAMS[source["url"]] = match.group(1)
    return f"{source['url']}?{_PAGE_PARAMS[source['url']]}_page={page}"


def crawl_blog(source: dict, fetch: Fetch = default_fetch, limit: int | None = None,
               describe: bool = True, workers: int = 6, page: int = 1,
               known: set[str] | None = None) -> list[NewsItem]:
    """Page de liste → cartes ; `describe` ajoute la description (et l'image si la carte n'en a pas)
    de chaque article, lues dans ses balises meta (en parallèle). `page` > 1 : pages plus anciennes ;
    `known` : URL déjà enregistrées, ni redécrites ni renvoyées."""
    url = listing_page_url(source, page, fetch)
    if url is None:
        return []
    items = parse_blog_listing(fetch(url), source["url"], source["name"])
    if known is not None:
        items = [item for item in items if str(item.url) not in known]
    items = items[: limit or settings.news_per_source]
    if not items and page == 1 and known is None:
        raise NewsError(f"Aucune carte d'article reconnue sur {source['url']} (structure de page modifiée ?)")
    if describe:
        def enrich(item: NewsItem) -> NewsItem:
            try:
                meta = page_meta(fetch(str(item.url)))
            except Exception:  # noqa: BLE001 — une page illisible garde sa carte sans résumé
                return item
            summary = clean_text(meta.get("og:description") or meta.get("description") or "")[:600]
            image = item.image or meta.get("og:image") or meta.get("twitter:image")
            return NewsItem.model_validate(item.record() | {"summary": summary, "image": image})

        with ThreadPoolExecutor(max_workers=workers) as pool:
            items = list(pool.map(enrich, items))
    return items


def blog_documents(blogs: list[dict], fetch: Fetch = default_fetch, limit: int = 8) -> list[Document]:
    """Collecteur du pipeline de veille : les pages de blog deviennent des documents « rss » (blog officiel)."""
    from rich import print

    documents: list[Document] = []
    for source in blogs:
        try:
            items = crawl_blog(source, fetch, limit=limit)
        except Exception as error:  # noqa: BLE001 — une source en panne ne bloque pas la veille
            print(f"[yellow]Blog ignoré ({source['name']}) : {error}[/yellow]")
            continue
        for item in items:
            published = datetime.combine(item.published_at, datetime.min.time(), timezone.utc) if item.published_at else None
            text = " — ".join(part for part in (item.category, item.summary) if part)
            documents.append(Document(source="rss", title=item.title, url=item.url, published_at=published,
                                      summary=text, content=text, tags=["rss", source["name"]]))
    return documents


# --- Flux RSS -----------------------------------------------------------------------------------


def crawl_feed(source: dict, limit: int | None = None, start: int = 0) -> list[NewsItem]:
    feed = fetch_feed(source["url"])
    items: list[NewsItem] = []
    for entry in feed.entries[start: start + (limit or settings.news_per_source)]:
        published = parse_date(entry.get("published") or entry.get("updated"))
        media = (entry.get("media_content") or entry.get("media_thumbnail") or [{}])[0].get("url")
        item = make_item(
            url=entry.get("link", ""), title=clean_text(entry.get("title", "")), source=source["name"],
            origin="rss", published_at=published.date() if published else None,
            summary=clean_text(entry.get("summary", ""))[:600],
            category=((entry.get("tags") or [{}])[0].get("term") or "")[:60], image=media,
        )
        if item:
            items.append(item)
    return items


# --- Crawl de toutes les sources d'actus ---------------------------------------------------------


@dataclass
class CrawlReport:
    items: list[NewsItem] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


def news_sources(sources: dict) -> tuple[list[dict], list[dict]]:
    """(pages de blog, flux RSS) affichés dans l'onglet Actus."""
    wanted = set(sources.get("news", {}).get("rss", []))
    feeds = [s for s in sources.get("rss", []) if s["name"] in wanted]
    return sources.get("blog", []), feeds


def crawl_all(sources: dict, fetch: Fetch = default_fetch,
              feed_reader: Callable[[dict], list[NewsItem]] | None = None) -> CrawlReport:
    blogs, feeds = news_sources(sources)
    report = CrawlReport()
    jobs = [(s, lambda s=s: crawl_blog(s, fetch)) for s in blogs]
    jobs += [(s, lambda s=s: (feed_reader or crawl_feed)(s)) for s in feeds]
    for source, job in jobs:
        try:
            items = job()
        except Exception as error:  # noqa: BLE001 — affiché dans l'onglet, les autres sources continuent
            report.errors[source["name"]] = f"{type(error).__name__} : {error}"
            continue
        report.items += items
        report.counts[source["name"]] = len(items)
    return report


def crawl_older(sources: dict, page: int, known: set[str], fetch: Fetch = default_fetch,
                feed_reader: Callable[..., list[NewsItem]] | None = None) -> CrawlReport:
    """Défilement infini de l'onglet Actus : page `page` des blogs et suite des flux (entrées plus anciennes)."""
    blogs, feeds = news_sources(sources)
    report = CrawlReport()
    per = settings.news_per_source
    jobs = [(s, lambda s=s: crawl_blog(s, fetch, page=page, known=known)) for s in blogs]
    jobs += [(s, lambda s=s: [i for i in (feed_reader or crawl_feed)(s, limit=per, start=(page - 1) * per)
                              if str(i.url) not in known]) for s in feeds]
    for source, job in jobs:
        try:
            items = job()
        except Exception as error:  # noqa: BLE001
            report.errors[source["name"]] = f"{type(error).__name__} : {error}"
            continue
        report.items += items
        report.counts[source["name"]] = len(items)
    return report


# --- Recherche web par l'API Claude (outil serveur web_search) ------------------------------------


class WebPick(BaseModel):
    url: str = Field(min_length=8, max_length=2000)
    summary: str = Field("", max_length=600)
    why: str = Field("", max_length=400)
    category: str = Field("", max_length=60)


class WebAnswer(BaseModel):
    items: list[WebPick] = Field(default_factory=list, max_length=12)


@dataclass
class SearchReport:
    items: list[NewsItem]
    topics: str
    results_seen: int
    rejected: list[str]  # URL proposées mais absentes des résultats de recherche (ou hors fenêtre)
    searches: int
    model: str


_AGO = re.compile(r"(\d+)\s+(minute|hour|day|week)s?\s+ago", re.I)


def page_age_date(page_age: str | None, today: date | None = None) -> date | None:
    """Date d'un résultat web_search (`page_age` : « September 20, 2026 », « 3 days ago »…)."""
    if not page_age:
        return None
    today = today or datetime.now(timezone.utc).date()
    if match := _AGO.search(page_age):
        amount, unit = int(match.group(1)), match.group(2).lower()
        delta = {"minute": timedelta(minutes=amount), "hour": timedelta(hours=amount),
                 "day": timedelta(days=amount), "week": timedelta(weeks=amount)}[unit]
        return (datetime.combine(today, datetime.min.time()) - delta).date()
    parsed = parse_date(page_age)
    return parsed.date() if parsed else None


def _normalize(url: str) -> str:
    parts = urlsplit(url.strip())
    return f"{parts.netloc.lower().removeprefix('www.')}{parts.path.rstrip('/')}"


def parse_answer(text: str) -> WebAnswer:
    """Objet JSON final de Claude (éventuellement dans un bloc ```json), validé par Pydantic."""
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    raw = fenced.group(1) if fenced else text[text.find("{"): text.rfind("}") + 1]
    try:
        return WebAnswer.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as error:
        raise NewsError(f"Réponse de Claude non conforme au schéma attendu : {error}") from error


def interest_topics(interests: dict | None) -> tuple[str, list[str]]:
    """(sujets à chercher, mots à écarter) depuis le profil Grill-me."""
    interests = interests or {}
    keywords = interests.get("keywords") or ["LLM open weights", "model release", "inference", "agents"]
    topics = "\n".join(f"- {k}" for k in keywords[:15])
    if interests.get("summary"):
        topics += f"\n\nProfil : {interests['summary']}"
    return topics, list(interests.get("exclusions") or [])


def claude_client():
    import anthropic

    key = settings.claude_search_key
    if not key:
        raise NewsError("Clé API Claude absente : renseigner CLAUDE_API dans .env puis redémarrer le serveur "
                        "(bin/veille restart).")
    headers = {"anthropic-workspace-id": settings.claude_workspace_id} if settings.claude_workspace_id else None
    return anthropic.Anthropic(api_key=key, timeout=float(max(settings.llm_timeout, 120)), max_retries=2,
                               default_headers=headers)


WORKSPACE_HINT = ("Clé API Claude non rattachée à un workspace : ajoutez CLAUDE_WORKSPACE_ID (identifiant du "
                  "workspace, Console Claude → Settings → Workspaces) dans la configuration, ou utilisez une clé "
                  "créée dans un workspace, puis redémarrez le serveur.")


def explain_api_error(error: Exception) -> str:
    """Message actionnable pour les erreurs courantes de l'API Claude."""
    text = str(error)
    if "anthropic-workspace-id" in text:
        return WORKSPACE_HINT
    if "authentication_error" in text or "invalid x-api-key" in text:
        return "Clé API Claude refusée : vérifiez CLAUDE_API puis redémarrez le serveur."
    return f"API Claude : {type(error).__name__} : {text[:400]}"


def search_news(topics: str, exclusions: list[str] | None = None, memory: str = "Aucun.",
                days: int | None = None, client: Any = None, model: str | None = None,
                today: date | None = None) -> SearchReport:
    from app.harness.prompts import render_prompt

    days = days or settings.news_search_days
    model = model or settings.news_model
    today = today or datetime.now(timezone.utc).date()
    exclusions = exclusions or []
    client = client or claude_client()
    prompt = render_prompt("news-search", topics=topics, days=days, today=today.isoformat(), memory=memory,
                           exclusions=", ".join(exclusions) or "cours, tutoriels, listes « awesome »")
    tool = {"type": settings.news_search_tool, "name": "web_search", "max_uses": settings.news_search_max_uses}
    messages: list[dict] = [{"role": "user", "content": prompt}]

    results: dict[str, tuple[str, str, str | None]] = {}  # URL normalisée → (URL, titre, page_age)
    searches, text = 0, ""
    for _ in range(4):  # pause_turn : le serveur rend la main au milieu d'une longue boucle d'outils
        response = client.messages.create(model=model, max_tokens=6000, messages=messages, tools=[tool])
        for block in response.content:
            kind = getattr(block, "type", "")
            if kind == "server_tool_use":
                searches += 1
            elif kind == "web_search_tool_result" and isinstance(block.content, list):
                for result in block.content:
                    if getattr(result, "type", "") == "web_search_result":
                        results[_normalize(result.url)] = (result.url, result.title, result.page_age)
            elif kind == "text":
                text += block.text
        if response.stop_reason != "pause_turn":
            break
        messages = [messages[0], {"role": "assistant", "content": response.content}]
    if response.stop_reason == "refusal":
        raise NewsError("Claude a refusé la recherche.")

    answer = parse_answer(text)
    oldest = today - timedelta(days=days + 2)
    banned = [word.lower() for word in exclusions]
    items, rejected, seen = [], [], set()
    for pick in answer.items:
        found = results.get(_normalize(pick.url))
        if not found or found[0] in seen:
            rejected.append(pick.url)
            continue
        url, title, page_age = found
        published = page_age_date(page_age, today)
        if (published and published < oldest) or any(word in title.lower() for word in banned):
            rejected.append(url)
            continue
        item = make_item(url=url, title=(title or url)[:300], source=WEB_SOURCE, origin="web_search",
                         published_at=published, summary=pick.summary, why=pick.why,
                         category=pick.category, query=topics[:300])
        if item:
            seen.add(url)
            items.append(item)
    return SearchReport(items=items, topics=topics, results_seen=len(results), rejected=rejected,
                        searches=searches, model=model)
