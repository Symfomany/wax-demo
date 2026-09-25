"""Vidéos, podcasts et émissions IA (onglet 🎬 Médias).

- flux déclarés dans sources.toml (`[[media]]` : name, url, kind = video | podcast) : chaînes
  YouTube (flux Atom officiel de la chaîne) et podcasts (RSS avec enclosure audio) ;
- recherche web par l'API Claude (outil serveur `web_search`) : seules les URL des résultats sont
  gardées, titre et date recopiés des résultats.

La miniature d'une vidéo YouTube est dérivée de son identifiant (i.ytimg.com), jamais inventée ;
le lecteur intégré passe par youtube-nocookie.com. Chaque média est validé par `MediaItem`.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel, Field, HttpUrl, ValidationError, field_validator

from app.collectors.rss import clean_text, fetch_feed, parse_date
from app.config import settings
from app.news import NewsError, _normalize, claude_client, extract_json, page_age_date, web_search

Kind = Literal["video", "podcast"]
HOSTS = {"youtube.com": "YouTube", "m.youtube.com": "YouTube", "youtu.be": "YouTube", "open.spotify.com": "Spotify",
         "podcasts.apple.com": "Apple Podcasts", "vimeo.com": "Vimeo"}


class MediaItem(BaseModel):
    id: str
    url: HttpUrl
    title: str = Field(min_length=1, max_length=300)
    source: str = Field(min_length=1, max_length=80)  # chaîne, émission ou « Web (Claude) »
    origin: Literal["feed", "web_search"]
    kind: Kind
    published_at: date | None = None
    summary: str = Field("", max_length=600)
    why: str = Field("", max_length=400)
    thumbnail: str | None = None  # https uniquement
    audio_url: str | None = None  # podcast : fichier audio (enclosure), https uniquement
    youtube_id: str | None = Field(None, pattern=r"^[A-Za-z0-9_-]{11}$")
    duration: str = Field("", max_length=20)

    @field_validator("thumbnail", "audio_url")
    @classmethod
    def https_only(cls, value: str | None) -> str | None:
        return value if value and value.startswith("https://") and len(value) < 2000 else None

    def record(self) -> dict:
        return self.model_dump(mode="json")


def media_id(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:16]


def youtube_id(url: str) -> str | None:
    """Identifiant d'une vidéo YouTube (watch?v=, youtu.be/, /shorts/, /live/, /embed/)."""
    parts = urlsplit(url)
    host = parts.netloc.lower().removeprefix("www.").removeprefix("m.")
    candidate = None
    if host == "youtu.be":
        candidate = parts.path.strip("/").split("/")[0]
    elif host in ("youtube.com", "youtube-nocookie.com", "music.youtube.com"):
        if parts.path == "/watch":
            candidate = (parse_qs(parts.query).get("v") or [None])[0]
        elif match := re.match(r"^/(?:shorts|live|embed)/([^/?#]+)", parts.path):
            candidate = match.group(1)
    return candidate if candidate and re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate) else None


def make_media(**values: Any) -> MediaItem | None:
    url = str(values.get("url", ""))
    if (video := youtube_id(url)) and not values.get("youtube_id"):
        values |= {"youtube_id": video, "kind": "video"}
        values.setdefault("thumbnail", None)
        values["thumbnail"] = values["thumbnail"] or f"https://i.ytimg.com/vi/{video}/hqdefault.jpg"
    try:
        return MediaItem(id=media_id(url), **values)
    except (ValidationError, KeyError):
        return None


# --- Flux (chaînes YouTube, podcasts) --------------------------------------------------------------


def crawl_media_feed(source: dict, limit: int | None = None) -> list[MediaItem]:
    feed = fetch_feed(source["url"])
    kind = source.get("kind", "video")
    items = []
    for entry in feed.entries[: limit or settings.media_per_source]:
        published = parse_date(entry.get("published") or entry.get("updated"))
        audio = next((e.get("href") for e in entry.get("enclosures", []) if str(e.get("type", "")).startswith("audio")), None)
        thumbnail = ((entry.get("media_thumbnail") or [{}])[0].get("url")
                     or (entry.get("image") or {}).get("href") or (feed.feed.get("image") or {}).get("href"))
        item = make_media(
            url=entry.get("link", ""), title=clean_text(entry.get("title", ""))[:300], source=source["name"],
            origin="feed", kind="podcast" if audio else kind, published_at=published.date() if published else None,
            summary=clean_text(entry.get("summary", "") or entry.get("media_description", ""))[:600],
            thumbnail=thumbnail, audio_url=audio, youtube_id=entry.get("yt_videoid"),
            duration=str(entry.get("itunes_duration", ""))[:20],
        )
        if item:
            items.append(item)
    return items


@dataclass
class MediaReport:
    items: list[MediaItem] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    results_seen: int = 0
    rejected: list[str] = field(default_factory=list)
    searches: int = 0


def crawl_media(sources: dict, reader: Callable[[dict], list[MediaItem]] | None = None) -> MediaReport:
    report = MediaReport()
    for source in sources.get("media", []):
        try:
            items = (reader or crawl_media_feed)(source)
        except Exception as error:  # noqa: BLE001 — une source en panne n'arrête pas les autres
            report.errors[source["name"]] = f"{type(error).__name__} : {error}"
            continue
        report.items += items
        report.counts[source["name"]] = len(items)
    return report


# --- Recherche web par l'API Claude ------------------------------------------------------------------


class MediaPick(BaseModel):
    url: str = Field(min_length=8, max_length=2000)
    kind: Kind = "video"
    show: str = Field("", max_length=80)  # chaîne ou émission, telle qu'affichée dans les résultats
    summary: str = Field("", max_length=600)
    why: str = Field("", max_length=400)


class MediaAnswer(BaseModel):
    items: list[MediaPick] = Field(default_factory=list, max_length=15)


def search_media(topics: str, memory: str = "Aucun.", days: int | None = None, client: Any = None,
                 model: str | None = None, today: date | None = None) -> MediaReport:
    from app.harness.prompts import render_prompt

    days = days or settings.media_search_days
    today = today or datetime.now(timezone.utc).date()
    model = model or settings.news_model
    prompt = render_prompt("media-search", topics=topics, today=today.isoformat(), days=days, memory=memory)
    results, text, searches = web_search(prompt, client or claude_client(), model)
    try:
        answer = MediaAnswer.model_validate(extract_json(text))
    except ValidationError as error:
        raise NewsError(f"Réponse de Claude non conforme au schéma attendu : {error}") from error

    report = MediaReport(results_seen=len(results), searches=searches)
    oldest = today - timedelta(days=days + 2)
    seen = set()
    for pick in answer.items:
        found = results.get(_normalize(pick.url))
        if not found or found[0] in seen:
            report.rejected.append(pick.url)
            continue
        url, title, page_age = found
        published = page_age_date(page_age, today)
        if published and published < oldest:
            report.rejected.append(url)
            continue
        # Chaîne ou émission : gardée seulement si elle figure dans le titre du résultat (pas de nom inventé).
        host = urlsplit(url).netloc.lower().removeprefix("www.")
        show = pick.show if pick.show and pick.show.lower() in (title or "").lower() else HOSTS.get(host, host)
        item = make_media(url=url, title=(title or url)[:300], source=(show or "Web (Claude)")[:80],
                          origin="web_search", kind=pick.kind, published_at=published, summary=pick.summary,
                          why=pick.why)
        if item:
            seen.add(url)
            report.items.append(item)
    return report
