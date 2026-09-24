"""Sources par URL : détection (flux, blog, GitHub, arXiv), vérification, écriture de sources.toml."""

import json
import tomllib
from datetime import datetime, timezone

import httpx
import pytest

from app import sources_admin
from app.config import settings
from app.sources_admin import SourceError, add_source, inspect_source, remove_source

NOW = datetime(2026, 9, 24, tzinfo=timezone.utc)
TOML = """# Sources autorisées
[[rss]]
name = "Blog A"
url = "https://a.example.org/feed.xml"

# arXiv
[arxiv]
feeds = [
    "https://rss.arxiv.org/rss/cs.CL",
]
keywords = ["llm"]

[github]
repositories = [
    "vllm-project/vllm",
]
releases_per_repo = 3
"""


def rss(title: str, date: str | None = "Tue, 22 Sep 2026 10:00:00 GMT", link: str = "https://blog.example.org/p1") -> str:
    pub = f"<pubDate>{date}</pubDate>" if date else ""
    return (f'<?xml version="1.0"?><rss version="2.0"><channel><title>{title}</title>'
            f"<item><title>Post 1</title><link>{link}</link>{pub}</item></channel></rss>")


ROUTES = {
    "https://blog.example.org/": (200, "text/html", '<html><head><link rel="alternate" type="application/rss+xml" '
                                                   'href="/rss.xml"></head><body>Blog</body></html>'),
    "https://blog.example.org/rss.xml": (200, "application/rss+xml", rss("Example Blog")),
    "https://nofeed.example.org/": (200, "text/html", "<html><body>no feed</body></html>"),
    "https://undated.example.org/feed.xml": (200, "application/xml", rss("Undated", date=None)),
    "https://rss.arxiv.org/rss/cs.LG": (200, "application/rss+xml", rss("cs.LG updates", link="https://arxiv.org/abs/2609.1")),
    "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=5":
        (200, "application/json", json.dumps([{"name": "b6000", "tag_name": "b6000", "draft": False,
                                               "html_url": "https://github.com/ggml-org/llama.cpp/releases/tag/b6000",
                                               "published_at": "2026-09-23T08:00:00Z"}])),
    "https://api.github.com/repos/acme/norelease/releases?per_page=5": (200, "application/json", "[]"),
    "https://api.github.com/repos/acme/missing/releases?per_page=5": (404, "application/json", "{}"),
}


@pytest.fixture
def client():
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        status, content_type, body = ROUTES.get(url, ROUTES.get(url + "/", (404, "text/html", "not found")))
        return httpx.Response(status, headers={"content-type": content_type}, text=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def sources(tmp_path, monkeypatch):
    path = tmp_path / "sources.toml"
    path.write_text(TOML)
    monkeypatch.setattr(settings, "sources_path", path)
    monkeypatch.setattr(settings, "github_api_url", None)
    return path


def inspect(url, client):
    return inspect_source(url, client, resolve=lambda host: ["93.184.216.34"], now=NOW)


def test_blog_page_feed_is_discovered_and_verified(sources, client):
    candidate = inspect("blog.example.org", client)
    assert (candidate.kind, candidate.value, candidate.name) == ("rss", "https://blog.example.org/rss.xml", "Example Blog")
    assert (candidate.entries, candidate.sample_date, candidate.already_present) == (1, "2026-09-22", False)


def test_github_and_arxiv_urls(sources, client):
    repo = inspect("https://github.com/ggml-org/llama.cpp/tree/master", client)
    assert (repo.kind, repo.value, repo.sample_title) == ("github", "ggml-org/llama.cpp", "b6000")
    arxiv = inspect("https://arxiv.org/list/cs.LG/recent", client)
    assert (arxiv.kind, arxiv.value, arxiv.name) == ("arxiv", "https://rss.arxiv.org/rss/cs.LG", "arXiv cs.LG")


@pytest.mark.parametrize(("url", "error"), [
    ("https://techcrunch.com/ai/", "presse ou un agrégateur"),
    ("https://nofeed.example.org/", "Aucun flux RSS/Atom"),
    ("https://undated.example.org/feed.xml", "aucune n'est datée"),
    ("https://github.com/acme/norelease", "aucune release"),
    ("https://github.com/acme/missing", "introuvable"),
])
def test_unverifiable_or_secondary_sources_are_refused(sources, client, url, error):
    with pytest.raises(SourceError, match=error):
        inspect(url, client)


def test_private_addresses_are_refused(sources, client, monkeypatch):
    monkeypatch.setattr(settings, "review_allow_private", False)
    with pytest.raises(SourceError, match="non publique"):
        inspect_source("http://intranet.example.org/feed", client, resolve=lambda host: ["10.0.0.2"], now=NOW)


def test_add_then_remove_keeps_the_file_intact(sources, client):
    for url in ["https://blog.example.org/", "https://github.com/ggml-org/llama.cpp", "https://arxiv.org/list/cs.LG"]:
        add_source(inspect(url, client), name="Mon blog" if "blog" in url else None)
    parsed = tomllib.loads(sources.read_text())
    assert parsed["rss"][-1] == {"name": "Mon blog", "url": "https://blog.example.org/rss.xml"}
    assert parsed["github"]["repositories"] == ["vllm-project/vllm", "ggml-org/llama.cpp"]
    assert parsed["arxiv"]["feeds"][-1] == "https://rss.arxiv.org/rss/cs.LG"
    assert "# arXiv" in sources.read_text()  # commentaires préservés
    assert inspect("https://blog.example.org/", client).already_present

    with pytest.raises(SourceError, match="Déjà présente"):
        add_source(inspect("https://blog.example.org/", client))

    remove_source("rss", "https://blog.example.org/rss.xml")
    remove_source("github", "ggml-org/llama.cpp")
    remove_source("arxiv", "https://rss.arxiv.org/rss/cs.LG")
    assert sources.read_text() == TOML
    with pytest.raises(SourceError, match="absente"):
        remove_source("github", "ggml-org/llama.cpp")


def test_shipped_sources_file_is_readable():
    listed = sources_admin.list_sources(settings.sources_path.__class__("sources.toml"))
    assert listed["rss"] and listed["arxiv"] and listed["github"]
