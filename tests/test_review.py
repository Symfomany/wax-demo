"""Review d'une actualité : extraction, téléchargement borné (SSRF), guard des citations, graphe."""

import httpx
import pytest

from app import storage
from app.llm import StructuredLLM
from app.review import (
    FetchError, Page, ReviewContext, ReviewDraft, best_passages, build_review_graph, check_url,
    extract_page, fetch_page, guard_review, quote_in_text, stream_review,
)
from tests.conftest import ARTICLE_HTML, ARTICLE_URL, fake_review_llm, review_draft

HOSTS = {"news.example.org": ["93.184.216.34"], "internal.example.org": ["10.0.0.7"],
         "localhost": ["127.0.0.1"], "metadata.example.org": ["169.254.169.254"]}


def resolve(host):
    return HOSTS[host]


def page() -> Page:
    return extract_page(ARTICLE_HTML, ARTICLE_URL)


def test_extraction_keeps_the_article_and_page_metadata():
    extracted = page()
    assert extracted.title == "vLLM 0.9 adds an FP8 KV cache"
    assert (extracted.site, extracted.published_at) == ("vLLM Blog", "2026-09-20")
    assert "halves memory usage on Hopper GPUs" in extracted.text
    assert extracted.text.startswith("## vLLM 0.9")
    for noise in ["tracking", "color: red", "Careers", "Copyright"]:
        assert noise not in extracted.text


def test_extraction_without_metadata_never_invents_a_date():
    extracted = extract_page("<html><body><p>" + "word " * 50 + "</p></body></html>", "https://a.example.org/p")
    assert extracted.published_at is None
    assert extracted.title == "a.example.org"


@pytest.mark.parametrize(("url", "error"), [
    ("file:///etc/passwd", "http"),
    ("ftp://news.example.org/x", "http"),
    ("https://user:pw@news.example.org/", "identifiants"),
    ("http://localhost:8000/api/health", "non publique"),
    ("http://internal.example.org/", "non publique"),
    ("http://metadata.example.org/latest/", "non publique"),
])
def test_unsafe_urls_are_refused(url, error):
    with pytest.raises(FetchError, match=error):
        check_url(url, resolve, allow_private=False)


def test_public_url_is_accepted():
    assert check_url(ARTICLE_URL, resolve, allow_private=False) == ARTICLE_URL


def client(routes: dict) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        status, headers, body = routes[str(request.url)]
        return httpx.Response(status, headers=headers, text=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_follows_public_redirects_and_refuses_private_ones(monkeypatch):
    monkeypatch.setattr("app.review.settings.review_allow_private", False)
    html = {"content-type": "text/html; charset=utf-8"}
    routes = {
        "https://news.example.org/short": (301, {"location": "/vllm-fp8"}, ""),
        ARTICLE_URL: (200, html, ARTICLE_HTML),
        "https://news.example.org/trap": (302, {"location": "http://internal.example.org/admin"}, ""),
        "https://news.example.org/pdf": (200, {"content-type": "application/pdf"}, "%PDF"),
        "https://news.example.org/missing": (404, html, "not found"),
    }
    fetched = fetch_page("https://news.example.org/short", client(routes), resolve)
    assert str(fetched.final_url) == ARTICLE_URL and str(fetched.url) == "https://news.example.org/short"
    with pytest.raises(FetchError, match="non publique"):
        fetch_page("https://news.example.org/trap", client(routes), resolve)
    with pytest.raises(FetchError, match="application/pdf"):
        fetch_page("https://news.example.org/pdf", client(routes), resolve)
    with pytest.raises(FetchError, match="404"):
        fetch_page("https://news.example.org/missing", client(routes), resolve)


def test_quotes_are_matched_tolerantly_but_not_invented():
    text = page().text
    assert quote_in_text("halves   memory usage on Hopper GPUs.", text)
    assert quote_in_text("The vLLM team released version 0.9 … halves memory usage", text)
    assert not quote_in_text("Throughput triples on every GPU", text)
    assert not quote_in_text("", text)


def test_guard_marks_unsupported_claims_and_strips_foreign_urls():
    rules = [("Transverse", "Toute affirmation doit citer la source.")]
    analysis, warnings = guard_review(ReviewDraft.model_validate(review_draft()), page(), rules)

    assert [c.status for c in analysis.claims] == ["etaye", "non_etaye"]
    assert analysis.claims[1].quote == ""
    assert "evil.example.com" not in analysis.summary and "[lien retiré]" in analysis.summary
    assert [c.rule_id for c in analysis.rule_checks] == [1]
    assert analysis.rule_checks[0].rule == "Toute affirmation doit citer la source."
    assert analysis.confidence == 5  # plafonnée : la moitié des affirmations n'est pas étayée
    assert any("Citation introuvable" in w for w in warnings)
    assert any("R999" in w for w in warnings)


def test_review_graph_fetches_analyzes_guards_and_saves(connection):
    prompts, fetched = [], []

    def fetch(url):
        fetched.append(url)
        return extract_page(ARTICLE_HTML, url)

    context = ReviewContext(llm=StructuredLLM(fake_review_llm(prompts), model="fake-review"),
                            connection=connection, fetch=fetch, memory=lambda: "- Écarter les tutoriels")
    events = list(stream_review(build_review_graph(context), {"url": ARTICLE_URL}))

    assert [e["node"] for e in events if e["type"] == "step"] == ["fetch", "analyze", "guard", "save"]
    record = events[-1]["review"]
    assert record["revision"] == 1 and record["title"] == "vLLM 0.9 adds an FP8 KV cache"
    assert "Inférence" in record["page"]["domains"]
    assert {"KV cache", "Quantification"} & {g["title"] for g in record["page"]["glossary"]}
    assert storage.get_review(connection, record["id"])["analysis"]["claims"][0]["status"] == "etaye"

    prompt = prompts[0]
    assert "[R1] (Transverse)" in prompt and "(Inférence)" in prompt
    assert "Identifier le type de source" in prompt  # méthode du skill review-actu
    assert "Inférence GPU, quantification" in prompt  # critères du skill veille-tech
    assert "Écarter les tutoriels" in prompt
    assert "[consigne neutralisée]" in prompt and "Ignore all previous instructions" not in prompt

    # Révision : pas de nouveau téléchargement, objections transmises, même review et même conversation.
    revised = list(stream_review(build_review_graph(context), {
        "url": record["url"], "previous": record, "objections": "Utilisateur : le 1.8x est un benchmark interne",
    }))[-1]["review"]
    assert fetched == [ARTICLE_URL]
    assert (revised["id"], revised["conversation_id"], revised["revision"]) == (record["id"], record["conversation_id"], 2)
    assert "le 1.8x est un benchmark interne" in prompts[1]
    assert storage.list_reviews(connection)[0]["revision"] == 2


def test_best_passages_follow_the_question():
    passages = best_passages(page().text, "Throughput 1.8x batch size 64 ?")
    assert passages[0].startswith("## vLLM")  # le premier paragraphe donne toujours le contexte
    assert any("batch size 64" in p for p in passages)
