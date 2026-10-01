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


# --- Fetch résilient : pages protégées (Cloudflare, 403, 429…) ----------------------------------

from types import SimpleNamespace  # noqa: E402

from app.review import fetch_archive, fetch_local, fetch_resilient, fetch_with_claude  # noqa: E402
from app.schemas import Document  # noqa: E402

HTML = {"content-type": "text/html; charset=utf-8"}
CHALLENGE_HTML = "<html><head><title>Just a moment...</title></head><body><p>Checking your browser.</p></body></html>"


def test_browser_headers_are_sent_and_blocking_errors_allow_a_fallback(monkeypatch):
    monkeypatch.setattr("app.review.settings.review_allow_private", False)
    seen = {}

    def handler(request):
        seen.update(request.headers)
        return {
            ARTICLE_URL: httpx.Response(200, headers=HTML, text=ARTICLE_HTML),
            "https://news.example.org/cf": httpx.Response(403, headers={"cf-mitigated": "challenge",
                                                                         "server": "cloudflare"}, text="x"),
            "https://news.example.org/busy": httpx.Response(503, headers=HTML, text="x"),
            "https://news.example.org/pdf": httpx.Response(200, headers={"content-type": "application/pdf"}, text="%PDF"),
            "https://news.example.org/wall": httpx.Response(200, headers=HTML, text=CHALLENGE_HTML),
        }[str(request.url)]

    browser = httpx.Client(transport=httpx.MockTransport(handler), headers=__import__("app.review").review.BROWSER_HEADERS)
    fetch_page(ARTICLE_URL, browser, resolve)
    assert "Chrome/" in seen["user-agent"] and "fr-FR" in seen["accept-language"]

    with pytest.raises(FetchError, match="Cloudflare") as blocked:
        fetch_page("https://news.example.org/cf", browser, resolve)
    assert blocked.value.fallback and not blocked.value.transient
    with pytest.raises(FetchError) as busy:
        fetch_page("https://news.example.org/busy", browser, resolve)
    assert busy.value.fallback and busy.value.transient
    with pytest.raises(FetchError, match="anti-robot") as wall:
        fetch_page("https://news.example.org/wall", browser, resolve)
    assert wall.value.fallback
    with pytest.raises(FetchError) as pdf:
        fetch_page("https://news.example.org/pdf", browser, resolve)
    assert not pdf.value.fallback


def blocked_client(extra: dict | None = None, calls: list | None = None) -> httpx.Client:
    def handler(request):
        url = str(request.url)
        if calls is not None:
            calls.append(url)
        for prefix, response in (extra or {}).items():
            if url.startswith(prefix):
                return response() if callable(response) else response
        return httpx.Response(403, headers={"cf-mitigated": "challenge"}, text="blocked")

    return httpx.Client(transport=httpx.MockTransport(handler))


def resolve_all(host):
    return ["93.184.216.34"]


@pytest.fixture
def no_claude(monkeypatch):
    monkeypatch.setattr("app.review.settings.review_allow_private", False)
    monkeypatch.setattr("app.review.settings.review_claude_fetch", False)


def test_transient_errors_are_retried_once_before_any_fallback(no_claude):
    answers = iter([httpx.Response(503, headers=HTML, text="busy"), httpx.Response(200, headers=HTML, text=ARTICLE_HTML)])
    naps = []
    page_ = fetch_resilient(ARTICLE_URL, None, blocked_client({ARTICLE_URL: lambda: next(answers)}), resolve_all,
                            sleep=naps.append)
    assert page_.via == "direct" and naps == [1.5] and page_.fetch_notes == []


def test_protected_page_falls_back_to_the_archived_copy(no_claude):
    archived = ARTICLE_HTML.replace("<html>", "<html><!-- archive -->")
    calls = []
    client_ = blocked_client({
        "https://web.archive.org/web/20260929101500id_/": httpx.Response(200, headers=HTML, text=archived),
        "https://web.archive.org/web/": httpx.Response(
            302, headers={"location": f"https://web.archive.org/web/20260929101500id_/{ARTICLE_URL}"}, text="")}, calls)

    page_ = fetch_resilient(ARTICLE_URL, None, client_, resolve_all, sleep=lambda s: None)

    assert page_.via == "archive" and str(page_.url) == ARTICLE_URL
    assert page_.title == "vLLM 0.9 adds an FP8 KV cache" and page_.published_at == "2026-09-20"
    assert page_.fetch_notes[0].startswith("page d'origine inaccessible — La page a répondu HTTP 403")
    assert "copie archivée du 2026-09-29" in page_.fetch_notes[1]
    assert calls[0] == ARTICLE_URL and calls[1].startswith("https://web.archive.org/web/")


def test_archive_rate_limit_is_retried_then_reported():
    naps = []
    with pytest.raises(FetchError, match="aucune copie archivée exploitable .*429"):
        fetch_archive(ARTICLE_URL, blocked_client({"https://web.archive.org/": httpx.Response(429, text="")}),
                      resolve_all, sleep=naps.append)
    assert naps == [3]


def claude_response(text: str | None = None, error_code: str | None = None):
    content = (SimpleNamespace(type="web_fetch_tool_error", error_code=error_code) if error_code else
               SimpleNamespace(type="web_fetch_result", url=ARTICLE_URL, content=SimpleNamespace(
                   title="vLLM 0.9 adds an FP8 KV cache", source=SimpleNamespace(type="text", data=text))))
    return SimpleNamespace(content=[SimpleNamespace(type="server_tool_use"),
                                    SimpleNamespace(type="web_fetch_tool_result", content=content),
                                    SimpleNamespace(type="text", text="OK")])


def fake_claude(response, calls: list):
    create = lambda **kwargs: calls.append(kwargs) or response  # noqa: E731
    return SimpleNamespace(messages=SimpleNamespace(create=create))


def test_claude_web_fetch_returns_only_the_tool_text(monkeypatch):
    calls = []
    text = "vLLM 0.9 adds an FP8 KV cache\n\n" + "The release halves memory usage on Hopper GPUs. " * 10
    page_ = fetch_with_claude(ARTICLE_URL, fake_claude(claude_response(text), calls), model="claude-test")
    assert page_.via == "claude" and "halves memory usage" in page_.text
    assert page_.title == "vLLM 0.9 adds an FP8 KV cache"
    assert calls[0]["tools"][0]["name"] == "web_fetch" and calls[0]["tools"][0]["max_uses"] == 1
    assert ARTICLE_URL in calls[0]["messages"][0]["content"]
    with pytest.raises(FetchError, match="url_not_accessible"):
        fetch_with_claude(ARTICLE_URL, fake_claude(claude_response(error_code="url_not_accessible"), []), model="m")


def test_every_attempt_is_reported_when_nothing_works(no_claude, connection):
    storage.save_documents(connection, [Document.model_validate({"url": ARTICLE_URL, "source": "rss", "title": "vLLM 0.9",
                                         "published_at": "2026-09-20T00:00:00+00:00", "summary": "Short feed summary.",
                                         "content": "Short feed summary.", "tags": ["rss"]})])
    archive_down = lambda url: (_ for _ in ()).throw(FetchError("aucune copie archivée exploitable"))  # noqa: E731

    with pytest.raises(FetchError) as error:
        fetch_resilient(ARTICLE_URL, connection, blocked_client(), resolve_all, archive=archive_down,
                        sleep=lambda s: None)

    message = str(error.value)
    assert message.startswith("Impossible de récupérer le texte de l'article.")
    assert "page d'origine : La page a répondu HTTP 403 (protection anti-robot Cloudflare)" in message
    assert "archive : aucune copie archivée" in message
    assert "veille : copie collectée trop courte" in message


def test_collected_copy_is_the_last_resort(no_claude, connection):
    body = "vLLM 0.9 adds an FP8 KV cache that halves memory usage on Hopper GPUs. " * 5
    storage.save_documents(connection, [Document.model_validate({"url": ARTICLE_URL, "source": "rss", "title": "vLLM 0.9 FP8",
                                         "published_at": "2026-09-20T00:00:00+00:00", "summary": "Short.",
                                         "content": body, "tags": ["rss"]})])
    archive_down = lambda url: (_ for _ in ()).throw(FetchError("aucune copie"))  # noqa: E731
    page_ = fetch_resilient(ARTICLE_URL, connection, blocked_client(), resolve_all, archive=archive_down,
                            sleep=lambda s: None)
    assert page_.via == "local" and page_.title == "vLLM 0.9 FP8" and page_.published_at == "2026-09-20"
    with pytest.raises(FetchError, match="aucune copie collectée"):
        fetch_local("https://news.example.org/inconnue", connection)


def test_unsafe_urls_never_fall_back(no_claude):
    with pytest.raises(FetchError, match="non publique"):
        fetch_resilient("http://internal.example.org/admin", None, blocked_client(), resolve,
                        archive=lambda url: pytest.fail("pas de repli pour une URL interne"))
