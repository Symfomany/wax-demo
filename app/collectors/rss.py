import html
import re
from datetime import timezone

import feedparser
from dateutil import parser as date_parser
from pydantic import ValidationError
from rich import print

from app.schemas import Document


USER_AGENT = "llm-watch-harness/0.2 (+local research digest)"


def clean_text(raw: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def parse_date(raw_date: str | None):
    if not raw_date:
        return None
    try:
        published_at = date_parser.parse(raw_date)
    except (TypeError, ValueError, OverflowError):
        return None
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    return published_at.astimezone(timezone.utc)


def fetch_feed(url: str):
    feed = feedparser.parse(url, agent=USER_AGENT)
    status = feed.get("status")
    if status and status >= 400:
        raise RuntimeError(f"HTTP {status}")
    if feed.bozo and not feed.entries:
        raise RuntimeError(str(feed.get("bozo_exception", "flux illisible")))
    return feed


def collect_rss(feeds: list[dict], limit_per_feed: int = 10) -> list[Document]:
    documents: list[Document] = []

    for source in feeds:
        try:
            feed = fetch_feed(source["url"])
        except Exception as error:  # une source en panne ne bloque pas la veille
            print(f"[yellow]RSS ignoré ({source['name']}) : {error}[/yellow]")
            continue

        for entry in feed.entries[:limit_per_feed]:
            if not entry.get("link"):
                continue
            summary = clean_text(entry.get("summary", ""))
            try:
                documents.append(
                    Document(
                        source="rss",
                        title=clean_text(entry.get("title", "Sans titre")),
                        url=entry["link"],
                        published_at=parse_date(entry.get("published") or entry.get("updated")),
                        summary=summary,
                        content=summary,
                        tags=["rss", source["name"]],
                    )
                )
            except ValidationError:
                continue

    return documents
