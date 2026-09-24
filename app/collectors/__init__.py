import tomllib
from collections.abc import Callable
from functools import partial
from pathlib import Path

from app.collectors.arxiv import collect_arxiv
from app.collectors.github import collect_github_releases
from app.collectors.github_mcp import collect_github_mcp
from app.collectors.rss import collect_rss
from app.config import settings
from app.schemas import Document


Collector = Callable[[], list[Document]]


def load_sources(path: Path) -> dict:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def default_collectors(sources: dict | None = None) -> dict[str, Collector]:
    """Un collecteur par tâche `collect:<nom>` du Task Graph."""
    sources = sources or load_sources(settings.sources_path)
    arxiv = sources.get("arxiv", {})
    github = sources.get("github", {})
    github_mcp = sources.get("github_mcp", {})

    def collect_blogs_and_feeds() -> list[Document]:
        from app.news import blog_documents  # pages de blog sans flux RSS ([[blog]])

        documents = collect_rss(sources.get("rss", []), limit_per_feed=settings.rss_max_items)
        return documents + blog_documents(sources.get("blog", []), limit=settings.rss_max_items)

    collectors: dict[str, Collector] = {
        "rss": collect_blogs_and_feeds,
        "arxiv": partial(
            collect_arxiv,
            arxiv.get("feeds", []),
            [keyword.lower() for keyword in arxiv.get("keywords", [])],
            max_results=settings.arxiv_max_results,
        ),
        "github_releases": partial(
            collect_github_releases,
            github.get("repositories", []),
            token=settings.github_token,
            per_repo=github.get("releases_per_repo", 3),
        ),
    }
    if github_mcp.get("enabled", False):
        collectors["github_mcp"] = partial(
            collect_github_mcp,
            github_mcp,
            token=settings.github_token,
            api_url=settings.github_api_url,
        )
    return collectors
