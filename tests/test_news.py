from datetime import date
from types import SimpleNamespace

import pytest

from app import news
from app.news import (NewsError, NewsItem, blog_documents, crawl_all, crawl_blog, interest_topics, make_item,
                      page_age_date, page_meta, parse_answer, parse_blog_listing, search_news)


def grid_card(slug, title, day, category="Claude Code", bg="Sky"):
    return f"""<div role="listitem" class="blog_cms_item w-dyn-item"><div class="card_blog_wrap">
    <div data-illustration-bg="{bg}" class="card_blog_visual_bg"></div>
    <img src="https://cdn.example.com/{slug}.svg" loading="lazy" alt="" class="card_blog_illo"/>
    <div class="u-text-style-caption u-foreground-tertiary u-mb-1-5">{day[:3]}</div>
    <div class="card_blog_title u-text-style-h6">{title}</div>
    <div fs-list-field="category" class="u-text-style-caption">{category}</div>
    <div class="u-display-none"><div fs-list-field="heading">{title}</div>
    <div fs-list-fieldtype="date" fs-list-field="date">{day}</div></div>
    <a data-cta="Blog page" href="/blog/{slug}" class="clickable_link w-inline-block"></a>
    <a aria-hidden="true" fs-list-element="item-link" href="/blog/{slug}" class="u-hide-if-empty"></a></div></div>"""


def hero_item(slug, title, day):
    return f"""<div role="listitem" class="marquee_cms_blog_list_item w-dyn-item"><div class="marquee_cms_blog_list_item_content">
    <h2 class="u-text-style-h6 u-mb-1">{title}</h2><div class="u-text-style-caption u-foreground-tertiary">{day}</div></div>
    <div class="clickable_wrap"><a data-cta="Blog page" href="/blog/{slug}" data-wf-event-ids="" class="clickable_link w-inline-block"></a></div></div>"""


LISTING = "<html><body>" + "".join([
    hero_item("opus", "Opus is out", "September 24, 2026"),  # aussi dans la grille : la carte illustrée gagne
    hero_item("chrome", "Claude in Chrome &amp; more", "August 26, 2026"),
    grid_card("opus", "Opus is out", "September 24, 2026"),
    grid_card("marketplace", "Claude Marketplace", "September 23, 2026", "Product announcements", "Clay"),
    '<div role="listitem" class="blog_cms_item"><a href="https://evil.example.org/x" class="clickable_link">x</a></div>',
]) + "</body></html>"

ARTICLE = """<html><head><meta content="Résumé &amp; contexte de l'article." name="description"/>
<meta content="https://cdn.example.com/og.jpg" property="og:image"/></head></html>"""


def test_blog_listing_parses_grid_and_hero_cards():
    items = parse_blog_listing(LISTING, "https://claude.com/blog", "Claude Blog")

    assert [str(i.url) for i in items] == ["https://claude.com/blog/opus", "https://claude.com/blog/marketplace",
                                           "https://claude.com/blog/chrome"]  # du plus récent au plus ancien
    opus, market, chrome = items
    assert (opus.published_at, opus.category, opus.accent) == (date(2026, 9, 24), "Claude Code", "#6a9bcc")
    assert opus.image == "https://cdn.example.com/opus.svg"
    assert (market.category, market.accent) == ("Product announcements", "#d97757")
    assert (chrome.title, chrome.image, chrome.origin) == ("Claude in Chrome & more", None, "blog")


def test_page_meta_ignores_attribute_order():
    meta = page_meta(ARTICLE + '<meta property="og:title" content="T"/>')
    assert meta["description"] == "Résumé & contexte de l'article."
    assert meta["og:image"] == "https://cdn.example.com/og.jpg"
    assert meta["og:title"] == "T"


def test_crawl_blog_adds_summaries_and_missing_images():
    pages = {"https://claude.com/blog": LISTING, "https://claude.com/blog/opus": ARTICLE,
             "https://claude.com/blog/chrome": ARTICLE}

    def fetch(url):
        if url not in pages:
            raise RuntimeError("HTTP 404")
        return pages[url]

    items = {i.title: i for i in crawl_blog({"name": "Claude Blog", "url": "https://claude.com/blog"}, fetch)}

    assert items["Opus is out"].summary == "Résumé & contexte de l'article."
    assert items["Opus is out"].image == "https://cdn.example.com/opus.svg"  # l'illustration de la carte est gardée
    assert items["Claude in Chrome & more"].image == "https://cdn.example.com/og.jpg"
    assert items["Claude Marketplace"].summary == ""  # article illisible : carte sans résumé

    with pytest.raises(NewsError, match="Aucune carte"):
        crawl_blog({"name": "Vide", "url": "https://claude.com/blog"}, lambda url: "<html></html>")


def test_crawl_all_reports_errors_per_source():
    sources = {"blog": [{"name": "Claude Blog", "url": "https://claude.com/blog"}],
               "rss": [{"name": "OpenAI News", "url": "https://openai.com/news/rss.xml"},
                       {"name": "Autre", "url": "https://example.org/feed"}],
               "news": {"rss": ["OpenAI News"]}}

    def feed_reader(source):
        raise RuntimeError("HTTP 503")

    report = crawl_all(sources, fetch=lambda url: LISTING if url.endswith("/blog") else "", feed_reader=feed_reader)

    assert report.counts == {"Claude Blog": 3}
    assert report.errors == {"OpenAI News": "RuntimeError : HTTP 503"}  # « Autre » n'est pas une source d'actus


def test_blog_documents_feed_the_watch_pipeline():
    documents = blog_documents([{"name": "Claude Blog", "url": "https://claude.com/blog"}],
                               fetch=lambda url: LISTING if url.endswith("/blog") else ARTICLE)
    assert {d.source for d in documents} == {"rss"}
    opus = next(d for d in documents if d.title == "Opus is out")
    assert opus.tags == ["rss", "Claude Blog"] and opus.published_at.date() == date(2026, 9, 24)
    assert "Résumé" in opus.summary


def test_news_item_validation():
    assert make_item(url="pas une url", title="x", source="s", origin="blog") is None
    item = make_item(url="https://a.org/x", title="T", source="s", origin="rss",
                     image="http://insecure.org/i.png", accent="red")
    assert item.image is None and item.accent is None and len(item.id) == 16


def test_page_age_dates():
    today = date(2026, 9, 24)
    assert page_age_date("3 days ago", today) == date(2026, 9, 21)
    assert page_age_date("2 weeks ago", today) == date(2026, 9, 10)
    assert page_age_date("September 20, 2026", today) == date(2026, 9, 20)
    assert page_age_date("n'importe quoi", today) is None
    assert page_age_date(None, today) is None


def test_parse_answer_accepts_fenced_json_and_rejects_the_rest():
    fenced = 'Voici :\n```json\n{"items": [{"url": "https://a.org/x", "summary": "S"}]}\n```'
    assert parse_answer(fenced).items[0].url == "https://a.org/x"
    assert parse_answer('{"items": []}').items == []
    with pytest.raises(NewsError, match="non conforme"):
        parse_answer("Je n'ai rien trouvé.")


# --- Recherche web : faux client Anthropic -----------------------------------------------------------


def block(**values):
    return SimpleNamespace(**values)


def result(url, title, page_age):
    return block(type="web_search_result", url=url, title=title, page_age=page_age, encrypted_content="…")


class FakeClaude:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


ANSWER = """{"items": [
  {"url": "https://qwen.ai/blog/qwen4/", "summary": "Qwen 4 sort en poids ouverts.", "why": "Licence Apache", "category": "Modèle"},
  {"url": "https://invented.example.org/fake", "summary": "URL inventée"},
  {"url": "https://old.example.org/news", "summary": "Trop ancienne"},
  {"url": "https://www.example.org/awesome-llm", "summary": "Liste"}
]}"""


def test_search_news_keeps_only_urls_returned_by_web_search():
    first = block(stop_reason="pause_turn", content=[
        block(type="server_tool_use", name="web_search", input={"query": "Qwen open weights"}),
        block(type="web_search_tool_result", content=[
            result("https://qwen.ai/blog/qwen4", "Qwen4: open weights", "2 days ago"),
            result("https://old.example.org/news", "Old news", "March 1, 2025"),
        ]),
    ])
    second = block(stop_reason="end_turn", content=[
        block(type="server_tool_use", name="web_search", input={"query": "awesome llm"}),
        block(type="web_search_tool_result", content=[result("https://example.org/awesome-llm", "Awesome LLM list", None)]),
        block(type="text", text=ANSWER),
    ])
    client = FakeClaude([first, second])

    report = search_news("- Qwen", exclusions=["awesome"], client=client, model="claude-test", days=7,
                         today=date(2026, 9, 24))

    assert [str(i.url) for i in report.items] == ["https://qwen.ai/blog/qwen4"]  # URL recopiée des résultats
    qwen = report.items[0]
    assert (qwen.title, qwen.published_at, qwen.origin, qwen.source) == (
        "Qwen4: open weights", date(2026, 9, 22), "web_search", "Web (Claude)")
    assert (qwen.why, qwen.category) == ("Licence Apache", "Modèle")
    assert set(report.rejected) == {"https://invented.example.org/fake", "https://old.example.org/news",
                                    "https://example.org/awesome-llm"}
    assert (report.searches, report.results_seen) == (2, 3)
    assert client.calls[0]["tools"][0]["name"] == "web_search" and client.calls[0]["model"] == "claude-test"
    assert client.calls[1]["messages"][1]["role"] == "assistant"  # reprise après pause_turn
    assert "Qwen" in client.calls[0]["messages"][0]["content"]


def test_search_news_requires_a_key(monkeypatch):
    monkeypatch.setattr(news.settings, "claude_api", None)
    monkeypatch.setattr(news.settings, "anthropic_api_key", None)
    with pytest.raises(NewsError, match="CLAUDE_API"):
        search_news("- LLM")


def test_interest_topics_come_from_the_grill_profile():
    topics, exclusions = interest_topics({"keywords": ["vLLM", "Qwen"], "summary": "Poids ouverts", "exclusions": ["tutorial"]})
    assert topics.startswith("- vLLM\n- Qwen") and "Poids ouverts" in topics
    assert exclusions == ["tutorial"]
    assert interest_topics(None)[0].startswith("- ")


def test_records_round_trip_through_json():
    item = make_item(url="https://a.org/x", title="T", source="s", origin="blog", published_at=date(2026, 9, 1))
    assert NewsItem.model_validate(item.record()) == item
    assert item.record()["published_at"] == "2026-09-01"


def test_api_errors_are_explained_and_workspace_header_is_sent(monkeypatch):
    from app.news import WORKSPACE_HINT, claude_client, explain_api_error

    assert explain_api_error(RuntimeError("must include the anthropic-workspace-id header")) == WORKSPACE_HINT
    assert "refusée" in explain_api_error(RuntimeError("authentication_error: invalid x-api-key"))
    monkeypatch.setattr(news.settings, "claude_api", "sk-test")
    monkeypatch.setattr(news.settings, "claude_workspace_id", "wrkspc_123")
    assert claude_client()._custom_headers["anthropic-workspace-id"] == "wrkspc_123"
