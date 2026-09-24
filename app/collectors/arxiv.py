"""arXiv via les flux RSS officiels (rss.arxiv.org).

L'API export.arxiv.org renvoie des HTTP 406 dès qu'on enchaîne les requêtes ;
les flux d'annonces quotidiennes sont plus fiables pour une veille.
"""

import re

from pydantic import ValidationError
from rich import print

from app.collectors.rss import clean_text, fetch_feed, parse_date
from app.schemas import Document


ANNOUNCE_PREFIX = re.compile(
    r"^arXiv:\S+\s+Announce Type:\s*(\S+)\s*Abstract:\s*", re.IGNORECASE
)


def collect_arxiv(
    feeds: list[str],
    keywords: list[str],
    max_results: int = 20,
) -> list[Document]:
    documents: dict[str, Document] = {}

    for url in feeds:
        try:
            feed = fetch_feed(url)
        except Exception as error:
            print(f"[yellow]arXiv ignoré ({url}) : {error}[/yellow]")
            continue

        for entry in feed.entries:
            link = entry.get("link")
            if not link or link in documents:
                continue

            summary = clean_text(entry.get("summary", ""))
            announce = ANNOUNCE_PREFIX.match(summary)
            # « replace » = nouvelle version d'un article déjà annoncé
            if announce and announce.group(1).lower().startswith("replace"):
                continue
            abstract = ANNOUNCE_PREFIX.sub("", summary)

            title = clean_text(entry.get("title", ""))
            haystack = f"{title} {abstract}".lower()
            if not any(keyword in haystack for keyword in keywords):
                continue

            try:
                documents[link] = Document(
                    source="arxiv",
                    title=title,
                    url=link,
                    published_at=parse_date(entry.get("published")),
                    summary=abstract,
                    content=abstract,
                    tags=["arxiv", *[tag.term for tag in entry.get("tags", [])]],
                )
            except ValidationError:
                continue

            if len(documents) >= max_results:
                return list(documents.values())

    return list(documents.values())
