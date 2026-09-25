"""E2E de l'interface web : chat SSE, veille pilotée par l'API, rapports, mémoire, Notion.

Réels : FastAPI, LangGraph + checkpoints SQLite, Store SQLite, publishers, rendu
des rapports. Simulés : Ollama (routeur + modèle de chat), collecteurs, Notion.
"""

from datetime import date
import json
import threading
import time
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from app import storage
from app.config import settings
from app.harness.skills import load_skill
from app.llm import StructuredLLM
from app.runtime import open_graph
from app.web.runs import RunManager
from app.web.server import WebDeps, create_app
from app.workflow.state import HarnessContext
from app.review import FetchError, extract_page
from tests.conftest import ARTICLE_HTML, ARTICLE_URL, NOW, documents, fake_ollama, fake_review_llm, make_document

DOCS = documents(*(make_document(i, "rss") for i in range(4)))


def chat_router(tool="search_watch", query="quantization"):
    return lambda connection: StructuredLLM(
        lambda messages, schema: json.dumps({"tool": tool, "query": query}), model="fake-router"
    )


def fetch_article(url: str):
    if "broken" in url:
        raise FetchError("La page a répondu HTTP 404.")
    return extract_page(ARTICLE_HTML, url)


def fake_inspect(url: str):
    from app.sources_admin import SourceCandidate, SourceError, is_present

    if "techcrunch" in url:
        raise SourceError("techcrunch.com est un site de presse ou un agrégateur")
    candidate = SourceCandidate(kind="rss", name="vLLM Blog", value="https://vllm.ai/blog/rss.xml", input_url=url,
                                entries=50, sample_title="Post", sample_link="https://vllm.ai/blog/p", sample_date="2026-09-22")
    candidate.already_present = is_present("rss", candidate.value)
    return candidate


def fake_news_crawl(sources):
    from app.news import CrawlReport, make_item

    items = [make_item(url="https://claude.com/blog/opus", title="Opus is out", source="Claude Blog", origin="blog",
                       published_at=date(2026, 9, 24), category="Claude Code", accent="#6a9bcc"),
             make_item(url="https://openai.com/index/x", title="OpenAI X", source="OpenAI News", origin="rss",
                       published_at=date(2026, 9, 23))]
    return CrawlReport(items=items, counts={"Claude Blog": 1, "OpenAI News": 1}, errors={})


NEWS_SEARCHES = []


def fake_news_search(topics, exclusions, memory, days):
    from app.news import SearchReport, make_item

    NEWS_SEARCHES.append({"topics": topics, "days": days})
    item = make_item(url="https://qwen.ai/blog/qwen4", title="Qwen4", source="Web (Claude)", origin="web_search",
                     summary="Poids ouverts.", why="Licence", query=topics)
    return SearchReport(items=[item], topics=topics, results_seen=5, rejected=["https://fake.example.org"],
                        searches=2, model="claude-test")


def fake_events_search(topics, memory, horizon):
    from app.events import EventReport, make_event

    return EventReport(items=[
        make_event(url="https://conf.example.org/2026", title="AI Conf 2026", source="Web (Claude)", origin="web_search",
                   kind="conference", starts_on=date(2026, 10, 5), date_verified=True, location="Paris"),
        make_event(url="https://tbd.example.org/", title="Meetup TBD", source="Web (Claude)", origin="web_search", kind="meetup"),
    ], results_seen=4, rejected=["https://invented.example.org"], unverified_dates=1, searches=2)


def fake_media_search(topics, memory, days):
    from app.media import MediaReport, make_media

    return MediaReport(items=[make_media(url="https://youtu.be/abcdefghijk", title="Agents talk", source="YouTube",
                                         origin="web_search", kind="video", published_at=date(2026, 9, 22))],
                       results_seen=3, searches=1)


def fake_screenshots(items, limit=None):
    from app.screenshots import ScreenshotReport, screenshot_path

    report = ScreenshotReport()
    for item in items[:1]:
        path = screenshot_path(item["id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\xff\xd8\xff" + b"0" * 20_000)
        report.saved[item["id"]] = str(path)
    return report


def fake_benchmark_pages(url):
    from tests.test_benchmarks import CATALOG, DETAIL

    return CATALOG if url.endswith("/benchmarks") else DETAIL


def fake_news_older(sources, page, known):
    from app.news import CrawlReport, make_item

    items = [make_item(url=f"https://claude.com/blog/old-{page}", title=f"Ancienne {page}", source="Claude Blog",
                       origin="blog", published_at=date(2026, 1, page)),
             make_item(url="https://claude.com/blog/opus", title="déjà connue", source="Claude Blog", origin="blog")]
    return CrawlReport(items=items if page < 4 else [], counts={"Claude Blog": 2 if page < 4 else 0}, errors={})


PUBLISHED_REVIEWS = []


def fake_review_publisher(connection, review_id):
    PUBLISHED_REVIEWS.append(review_id)
    return {"url": "https://www.notion.so/veille-e2e", "blocks": 1, "title": "Review"}


ASSISTANT_CALLS = []


def fake_assistant(messages, context, web):
    ASSISTANT_CALLS.append({"messages": [m.content for m in messages], "context": context, "web": web})
    yield {"type": "token", "text": "Bonjour "}
    yield {"type": "token", "text": "!"}
    yield {"type": "final", "text": "Bonjour !", "sources": [{"url": "https://c.org", "title": "C"}],
           "model": "claude-test", "searches": 0}


@pytest.fixture
def notion_calls():
    return []


@pytest.fixture
def client(isolated_settings, notion_calls):
    def notion_sync(connection):
        notion_calls.append(connection)
        return {"url": "https://www.notion.so/veille-e2e", "digests": 1, "blocks": 9, "page_id": "p", "archived": None}

    @contextmanager
    def graph_factory():
        connection = storage.connect(settings.database_path)
        context = HarnessContext(
            llm=StructuredLLM(fake_ollama(), model="fake", connection=connection),
            connection=connection,
            skill=load_skill(settings.skill_path),
            output_dir=settings.output_dir,
            collectors={"rss": lambda: DOCS, "arxiv": lambda: [], "github_releases": lambda: [], "github_mcp": lambda: []},
            human_approval=True,
            reports_dir=settings.reports_dir,
            notion_sync=notion_sync,
            batch_size=4,
            now=lambda: NOW,
        )
        with open_graph(context) as graph:
            yield graph, context

    deps = WebDeps(
        router_llm=chat_router(),
        chat_model=lambda: GenericFakeChatModel(messages=iter([AIMessage("FP8 arrive dans vLLM [1].")])),
        graph_factory=graph_factory,
        notion_sync=notion_sync,
        github_search=lambda query: [],
        fetch_page=fetch_article,
        inspect_source=fake_inspect,
        news_crawl=fake_news_crawl,
        news_search=fake_news_search,
        news_older=fake_news_older,
        review_publisher=fake_review_publisher,
        assistant_stream=fake_assistant,
        events_search=fake_events_search,
        media_search=fake_media_search,
        screenshot_capture=fake_screenshots,
        benchmarks_fetch=fake_benchmark_pages,
        review_llm=lambda connection: StructuredLLM(fake_review_llm(), model="fake-review", connection=connection),
    )
    with TestClient(create_app(deps)) as test_client:
        yield test_client


def sse(response) -> list[dict]:
    return [json.loads(line[6:]) for line in response.text.split("\n\n") if line.startswith("data: ")]


def wait_status(client, run_id, expected, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] in expected:
            return run
        time.sleep(0.1)
    raise AssertionError(f"statut {run['status']} au lieu de {expected}")


def test_index_serves_the_chat_interface(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "Votre veille LLM / GenAI" in page.text and "/api/chat" in page.text


def test_api_token_guards_the_api_but_not_the_page(isolated_settings, monkeypatch):
    monkeypatch.setattr(settings, "web_api_token", "s3cret")
    deps = WebDeps(router_llm=chat_router(), chat_model=lambda: None, graph_factory=lambda: None,
                   notion_sync=None, github_search=None)
    with TestClient(create_app(deps)) as anonymous:
        assert anonymous.get("/").status_code == 200
        assert anonymous.get("/api/health").status_code == 200  # sonde de bin/veille et de la TUI
        assert anonymous.get("/api/reports").status_code == 401
        assert anonymous.post("/api/runs", json={}).status_code == 401
        assert anonymous.get("/api/reports", headers={"Authorization": "Bearer faux"}).status_code == 401
        assert anonymous.get("/api/reports", headers={"Authorization": "Bearer s3cret"}).status_code == 200
        assert anonymous.get("/?token=faux", follow_redirects=False).status_code == 401
        login = anonymous.get("/?token=s3cret", follow_redirects=False)
        assert login.status_code == 303 and "httponly" in login.headers["set-cookie"].lower()
        assert anonymous.get("/api/reports").status_code == 200  # cookie posé par la connexion


def test_login_locks_every_page_and_api(isolated_settings, monkeypatch):
    import base64

    from app.web.auth import Authenticator, Throttle

    deps = WebDeps(router_llm=chat_router(), chat_model=lambda: None, graph_factory=lambda: None,
                   notion_sync=None, github_search=None,
                   authenticator=Authenticator("veille", "Str0ng!Pass", throttle=Throttle(max_failures=3)))
    with TestClient(create_app(deps)) as browser:
        page = browser.get("/", headers={"Accept": "text/html"}, follow_redirects=False)
        assert page.status_code == 303 and page.headers["location"] == "/login?next=%2F"
        assert browser.get("/static/index.html").status_code == 401
        assert browser.get("/api/reports").status_code == 401
        assert browser.get("/api/health").json() == {"status": "ok", "login": True}  # sonde sans détail
        assert "Se connecter" in browser.get("/login").text

        assert browser.post("/api/login", json={"username": "veille", "password": "faux"}).status_code == 401
        login = browser.post("/api/login", json={"username": "veille", "password": "Str0ng!Pass"})
        assert login.status_code == 200
        cookie = login.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie
        assert browser.get("/api/reports").status_code == 200
        assert "model" in browser.get("/api/health").json()
        assert browser.get("/api/me").json() == {"user": "veille", "login": True}
        browser.post("/api/logout")
        browser.cookies.clear()
        assert browser.get("/api/reports").status_code == 401

    with TestClient(create_app(deps)) as tui:
        basic = "Basic " + base64.b64encode(b"veille:Str0ng!Pass").decode()
        assert tui.get("/api/reports", headers={"Authorization": basic}).status_code == 200
        wrong = {"Authorization": "Basic " + base64.b64encode(b"veille:x").decode()}
        codes = [tui.get("/api/reports", headers=wrong).status_code for _ in range(4)]
        assert codes == [401, 401, 401, 429]  # anti-force brute, y compris par /api/health
        assert tui.get("/api/health", headers=wrong).status_code == 429
        assert tui.post("/api/login", json={"username": "veille", "password": "Str0ng!Pass"}).status_code == 429


def test_events_media_and_screenshots_endpoints(client, isolated_settings, monkeypatch):
    monkeypatch.setattr(settings, "screenshot_dir", isolated_settings / "shots")
    page = client.get("/").text
    assert all(f'id="{view}"' in page for view in ("events", "media", "help", "drawer"))

    found = client.post("/api/events/search", json={"topics": "agents"}).json()
    assert (found["saved"], found["unverified_dates"]) == (2, 1)
    upcoming = client.get("/api/events").json()["items"]
    assert [e["title"] for e in upcoming] == ["AI Conf 2026", "Meetup TBD"]  # datés d'abord, puis à confirmer
    ics = client.get("/api/events.ics")
    assert ics.headers["content-type"].startswith("text/calendar") and ics.text.count("BEGIN:VEVENT") == 1
    assert client.get("/api/events?when=later").status_code == 400

    assert client.post("/api/media/search", json={}).json()["saved"] == 1
    media = client.get("/api/media?kind=video").json()
    assert media["items"][0]["youtube_id"] == "abcdefghijk" and media["sources"][0]["source"] == "YouTube"

    client.post("/api/news/crawl")
    shots = client.post("/api/news/screenshots", json={}).json()
    assert shots["saved"] == 1
    shot = next(n for n in client.get("/api/news").json()["items"] if n.get("screenshot"))
    image = client.get(shot["screenshot"])
    assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"
    assert client.get("/api/news/0123456789abcdef/screenshot").status_code == 404
    assert client.get("/api/health").json()["screenshots"] is True


def test_benchmarks_catalog_detail_and_analysis(client):
    assert client.get("/api/benchmarks").json()["overview"]["total"] == 0
    assert client.post("/api/benchmarks/crawl").json()["saved"] == 3

    listed = client.get("/api/benchmarks?category=agentic").json()
    assert listed["overview"]["by_category"] == {"Agentic": 3} and listed["items"][0]["analysis"] is None
    assert [b["key"] for b in client.get("/api/benchmarks?q=MRCR").json()["items"]] == ["mrcrv2_64_128"]

    detail = client.get("/api/benchmarks/DRACO").json()  # casse indifférente
    assert detail["key"] == "draco" and detail["analysis"]["open_leader"]["model"] == "Open B"
    by_path = client.get("/api/benchmarks/mrcr-v2-64k-128k").json()  # chemin de page accepté
    assert by_path["key"] == "mrcrv2_64_128"
    assert client.get("/api/benchmarks/draco").json()["detail_fetched_at"]  # en cache
    assert client.get("/api/benchmarks/inconnu").status_code == 404
    items = {b["key"]: b for b in client.get("/api/benchmarks").json()["items"]}
    assert items["draco"]["analysis"]["leader"]["model"] == "Closed A"  # la liste reprend l'analyse en cache
    assert items["gaia"]["analysis"] is None


def test_health_reports_components(client):
    health = client.get("/api/health").json()
    assert set(health) >= {"ollama", "model", "model_available", "tracing", "notion", "memory"}
    assert health["notion"] is True
    assert health["tracing"] == {"langfuse": False, "langsmith": False}


def test_chat_streams_events_and_keeps_the_conversation(client):
    storage.save_documents(storage.connect(settings.database_path), DOCS)

    events = sse(client.post("/api/chat", json={"message": "Que sait-on sur la quantization ?"}))

    types = [event["type"] for event in events]
    assert types[0] == "conversation" and types[-1] == "final"
    assert {"tool_start", "tool_end", "token"} <= set(types)
    final = events[-1]
    assert final["text"] == "FP8 arrive dans vLLM [1]." and final["sources"]
    conversation_id = events[0]["id"]

    assert client.get("/api/conversations").json()[0]["id"] == conversation_id
    history = client.get(f"/api/conversations/{conversation_id}").json()
    assert [m["role"] for m in history] == ["human", "ai"]
    # Chaque réponse a une trace de son parcours dans le graphe du chat
    assert final["trace_url"] == history[1]["trace_url"] == f"/trace/{final['trace_id']}"
    assert history[1]["engaged"]["tool"] == "search_watch"
    assert final["engaged"]["tool"] == "search_watch"
    trace = client.get(f"/api/traces/{final['trace_id']}").json()
    assert [step["node"] for step in trace["steps"]] == ["route", "act", "respond", "guard"]
    assert "class route,act,respond,guard visited;" in trace["diagrams"][0]["mermaid"]
    assert client.get(final["trace_url"]).text.startswith("<!doctype html>")


def test_chat_rejects_empty_messages(client):
    assert client.post("/api/chat", json={"message": ""}).status_code == 422


def test_watch_run_from_api_to_dated_report_and_notion(client, notion_calls):
    run_id = client.post("/api/runs", json={"collect": True, "mcp": False}).json()["id"]

    waiting = wait_status(client, run_id, {"awaiting_approval"})
    kinds = [event["type"] for event in waiting["events"]]
    assert "collect" in kinds and "agent" in kinds and "awaiting_approval" in kinds
    assert "Veille LLM" in waiting["markdown"]
    assert client.post(f"/api/runs/{run_id}/decision", json={"approved": True, "note": "x"}).status_code == 200

    published = wait_status(client, run_id, {"published"})
    assert published["outputs"]["notion"] == "https://www.notion.so/veille-e2e" and notion_calls
    assert any(e["type"] == "decision" for e in published["events"])

    reports = client.get("/api/reports").json()
    assert len(reports) == 1 and reports[0]["name"].startswith("2026/veille-2026-09-24")
    report = client.get(f"/api/reports/{reports[0]['name']}").json()
    assert report["markdown"].startswith("---\ntitle:") and "html" not in report
    page = client.get(report["html_url"])
    assert page.headers["content-type"].startswith("text/html")
    assert "script-src 'none'" in page.headers["content-security-policy"]
    assert '<article class="signal"' in page.text

    trace = client.get(f"/api/traces/{run_id}").json()
    graphs = {step["graph"] for step in trace["steps"]}
    assert {"veille", "research", "review", "editorial"} <= graphs
    assert trace["engaged"]["agents"][:2] == ["supervisor", "collector"]
    assert "scout.md" in trace["engaged"]["prompts"]
    research = next(d for d in trace["diagrams"] if d["graph"] == "research")
    assert "class scout_batch,rank visited;" in research["mermaid"]

    memory = client.get("/api/memory").json()
    assert memory["lessons"][0]["note"] == "x"
    assert memory["sources"]["rss"]["ok"] == 1


def test_decision_requires_a_waiting_run(client):
    assert client.post("/api/runs/inconnu/decision", json={"approved": True}).status_code == 404


def test_chat_can_launch_a_watch(client):
    deps_client = client
    events = sse(deps_client.post("/api/chat", json={"message": "lance une veille"}))
    final = events[-1]
    assert final["tool"] == "run_watch" and final["data"]["run_id"]
    wait_status(client, final["data"]["run_id"], {"awaiting_approval"})


def test_report_path_traversal_is_refused(client):
    assert client.get("/api/reports/..%2F..%2F/CLAUDE.md").status_code == 404
    assert client.get("/api/reports/2026/..%2F..%2Fpyproject.toml").status_code == 404


def test_notion_sync_endpoint(client, notion_calls):
    assert client.post("/api/notion/sync").json()["url"] == "https://www.notion.so/veille-e2e"


def test_only_one_run_at_a_time(tmp_path):
    release = threading.Event()

    class Graph:
        def stream(self, *args, **kwargs):
            release.wait(5)
            return iter(())

        def get_state(self, config):
            class Snapshot:
                next, values = (), {}
            return Snapshot()

    @contextmanager
    def factory():
        class Context:
            connection = storage.connect(tmp_path / "busy.db")
        yield Graph(), Context()

    manager = RunManager(factory)
    run_id = manager.start()
    with pytest.raises(RuntimeError, match="déjà en cours"):
        manager.start()
    release.set()
    manager.wait(run_id)
    assert manager.get(run_id).status == "failed"



def test_filtered_search_endpoint(client):
    storage.save_documents(storage.connect(settings.database_path), DOCS)

    found = client.post("/api/search", json={"keywords": ["quantization"], "sources": ["rss"], "limit": 2}).json()
    assert found["count"] == 2 and all(r["source"] == "rss" for r in found["results"])
    assert client.post("/api/search", json={"keywords": ["quantization"], "sources": ["arxiv"]}).json()["count"] == 0
    assert client.post("/api/search", json={"keywords": ["x"], "sources": ["twitter"]}).status_code == 400


def test_targeted_run_from_api(client):
    run_id = client.post("/api/runs", json={"keywords": ["inexistant-xyz"], "sources": ["rss"],
                                            "max_documents": 3}).json()["id"]
    run = wait_status(client, run_id, {"blocked", "awaiting_approval"})
    assert run["options"]["keywords"] == ["inexistant-xyz"] and run["options"]["sources"] == ["rss"]
    assert run["status"] == "blocked"  # aucun document ne correspond : digest vide bloqué par les guards


def test_prompt_editing_api(client):
    prompts = {p["name"] for p in client.get("/api/prompts").json()}
    assert prompts == {"scout", "critic", "claims", "editor", "router", "chat-system", "grill", "review", "news-search",
                       "events-search", "media-search"}
    original = client.get("/api/prompts/critic").json()
    assert original["overridden"] is False and original["required"] == ["signals"]

    bad = client.put("/api/prompts/critic", json={"text": "Sans variable"})
    assert bad.status_code == 400 and "signals" in bad.json()["detail"]
    saved = client.put("/api/prompts/critic", json={"text": "Juge strictement.\n$signals"}).json()
    assert saved["overridden"] is True and len(saved["history"]) == 1

    reset = client.delete("/api/prompts/critic").json()
    assert reset["overridden"] is False and reset["text"] == original["text"]
    assert client.get("/api/prompts/inconnu").status_code == 404


def test_grill_me_interview_through_the_api(client):
    step = client.post("/api/grill").json()
    session, question = step["session"], step["question"]
    assert question["id"] == "domains" and question["recommended"]

    step = client.post(f"/api/grill/{session}/answer", json={"options": ["robotics"]}).json()
    asked = [step["question"]["id"]]
    while "question" in step:
        reply = {"text": "Jetson Thor"} if step["question"]["id"] == "keywords" else {"recommended": True}
        step = client.post(f"/api/grill/{session}/answer", json=reply).json()
        if "question" in step:
            asked.append(step["question"]["id"])

    assert asked[:2] == ["robotics_topics", "robotics_angle"] and asked[-1] == "keywords"
    profile = step["profile"]
    assert "Jetson Thor" in profile["keywords"] and profile["domains"] == ["robotics"]
    assert client.get("/api/grill/profile").json()["keywords"] == profile["keywords"]
    assert client.get("/api/memory").json()["interests"]["summary"] == profile["summary"]
    assert "Centres d'intérêt (Grill-me)" in settings.claude_memory_path.read_text()
    # entretien terminé : une réponse de plus est refusée
    assert client.post(f"/api/grill/{session}/answer", json={"options": []}).status_code == 409


def test_chat_opens_grill_me(client):
    final = sse(client.post("/api/chat", json={"message": "Grill me sur ma veille"}))[-1]
    assert final["tool"] == "grill_me" and final["data"] == {"grill": True}
    assert final["engaged"]["skills"] == ["grill-me"]


def test_chat_answer_has_no_langfuse_link_when_disabled(client):
    final = sse(client.post("/api/chat", json={"message": "quoi de neuf ?"}))[-1]
    assert final["langfuse"] is None


# --- Base de connaissances ---------------------------------------------------------------------

NOTE = "---\ntitle: Notes\ntype: glossaire\n---\n\n## Late chunking\nDomaine : RAG\n\nDécouper après l'encodage."


def test_knowledge_base_api(client, isolated_settings):
    base = client.get("/api/knowledge").json()
    assert {f["name"] for f in base["files"]} >= {"glossaire.md", "regles-metiers.md", "prompts-veille.md"}
    assert "Inférence" in base["domains"] and base["errors"] == []
    assert any(item["term"] == "RAG" for item in client.get("/api/knowledge/index").json())
    assert client.get("/api/knowledge/search", params={"q": "kv cache"}).json()[0]["title"] == "KV cache"

    assert client.post("/api/knowledge", json={"filename": "Mes notes.md", "content": NOTE}).status_code == 201
    assert (isolated_settings / "knowledge" / "mes-notes.md").exists()
    assert client.post("/api/knowledge", json={"filename": "mes-notes.md", "content": NOTE}).status_code == 409
    assert client.post("/api/knowledge", json={"filename": "mes-notes.md", "content": NOTE, "replace": True}).status_code == 201
    assert client.post("/api/knowledge", json={"filename": "x.md", "content": "## A\nSource : nope\n\nt"}).status_code == 400
    entries = client.get("/api/knowledge").json()["entries"]
    assert any(e["id"] == "mes-notes/late-chunking" and e["origin"] == "upload" for e in entries)
    assert client.get("/api/knowledge/files/mes-notes.md").json()["origin"] == "upload"

    assert client.delete("/api/knowledge/mes-notes.md").status_code == 200
    assert client.delete("/api/knowledge/glossaire.md").status_code == 404  # fichier du dépôt : protégé
    assert client.get("/api/knowledge/files/..%2F.env").status_code == 404


# --- Review d'une actualité --------------------------------------------------------------------


def test_review_from_url_then_challenge_and_revise(client):
    events = sse(client.post("/api/reviews", json={"url": ARTICLE_URL}))
    assert [e["node"] for e in events if e["type"] == "step"] == ["fetch", "analyze", "guard", "save"]
    final = events[-1]
    review = final["review"]
    assert review["title"] == "vLLM 0.9 adds an FP8 KV cache"
    assert [c["status"] for c in review["analysis"]["claims"]] == ["etaye", "non_etaye"]
    assert any("Citation introuvable" in w for w in review["warnings"])

    trace = client.get(final["trace_url"].replace("/trace/", "/api/traces/")).json()
    assert trace["kind"] == "article" and trace["diagrams"][0]["graph"] == "review"
    assert client.get("/api/reviews").json()[0]["id"] == review["id"]
    assert client.get(f"/api/reviews/{review['id']}").json()["trace_url"] == final["trace_url"]

    # Pas de révision sans débat
    assert client.post(f"/api/reviews/{review['id']}/revise").status_code == 409

    chat = sse(client.post("/api/chat", json={"message": "Le 1.8x est-il mesuré ?", "review_id": review["id"]}))
    assert chat[0] == {"type": "conversation", "id": review["conversation_id"]}
    assert chat[-1]["tool"] == "challenge_review"
    assert chat[-1]["sources"][0]["url"] == ARTICLE_URL
    assert client.get("/api/conversations").json()[0]["title"].startswith("🔬 vLLM 0.9")

    revised = sse(client.post(f"/api/reviews/{review['id']}/revise"))
    assert [e["node"] for e in revised if e["type"] == "step"][0] == "fetch"
    assert revised[-1]["review"]["revision"] == 2
    assert revised[-1]["review"]["objections"].startswith("Utilisateur : Le 1.8x est-il mesuré ?")


def test_review_errors_are_reported(client):
    events = sse(client.post("/api/reviews", json={"url": "https://news.example.org/broken"}))
    assert events[-1] == {"type": "error", "text": "La page a répondu HTTP 404."}
    assert client.get("/api/reviews").json() == []
    assert client.post("/api/chat", json={"message": "x", "review_id": "absent"}).status_code == 404
    assert client.get("/api/reviews/absent").status_code == 404


# --- Sources par URL ---------------------------------------------------------------------------


def test_sources_can_be_inspected_added_and_removed(client, isolated_settings):
    listed = client.get("/api/sources").json()
    assert [r["name"] for r in listed["rss"]] == ["Blog test"] and "health" in listed

    preview = client.post("/api/sources/inspect", json={"url": "https://blog.vllm.ai"}).json()
    assert (preview["label"], preview["already_present"]) == ("flux RSS/Atom", False)
    assert "vllm.ai" not in (isolated_settings / "sources.toml").read_text()  # l'inspection n'écrit rien

    added = client.post("/api/sources", json={"url": "https://blog.vllm.ai", "name": "vLLM"})
    assert added.status_code == 201
    assert {"name": "vLLM", "url": "https://vllm.ai/blog/rss.xml"} in added.json()["sources"]["rss"]
    assert client.post("/api/sources", json={"url": "https://blog.vllm.ai"}).status_code == 409
    assert client.post("/api/sources/inspect", json={"url": "https://techcrunch.com/ai"}).status_code == 400

    removed = client.delete("/api/sources", params={"kind": "rss", "value": "https://vllm.ai/blog/rss.xml"})
    assert removed.status_code == 200 and all(r["name"] != "vLLM" for r in removed.json()["rss"])
    assert client.delete("/api/sources", params={"kind": "rss", "value": "https://absent"}).status_code == 404
    assert client.delete("/api/sources", params={"kind": "x", "value": "y"}).status_code == 400


def test_news_crawl_search_and_filters(client):
    assert client.get("/api/news").json()["items"] == []
    assert client.get("/api/health").json()["claude_search"] is True

    crawl = client.post("/api/news/crawl").json()
    assert crawl == {"counts": {"Claude Blog": 1, "OpenAI News": 1}, "errors": {}, "saved": 2}

    found = client.post("/api/news/search", json={"topics": "Qwen, vLLM", "days": 3}).json()
    assert (found["saved"], found["searches"], found["rejected"]) == (1, 2, ["https://fake.example.org"])
    assert NEWS_SEARCHES[-1] == {"topics": "- Qwen\n- vLLM", "days": 3}

    listed = client.get("/api/news").json()
    assert {n["source"] for n in listed["items"]} == {"Claude Blog", "OpenAI News", "Web (Claude)"}
    assert {s["source"] for s in listed["sources"]} == {"Claude Blog", "OpenAI News", "Web (Claude)"}
    assert [n["title"] for n in client.get("/api/news?source=Claude Blog").json()["items"]] == ["Opus is out"]
    assert [n["title"] for n in client.get("/api/news?origin=web_search").json()["items"]] == ["Qwen4"]
    assert [n["title"] for n in client.get("/api/news?q=openai").json()["items"]] == ["OpenAI X"]
    assert client.post("/api/news/search", json={"days": 99}).status_code == 422

    page = client.get("/").text
    assert 'id="claude-search"' in page and 'id="news"' in page and '{ id: "news"' in page


def test_news_infinite_scroll_crawls_older_pages_and_documents_open(client):
    client.post("/api/news/crawl")
    first = client.get("/api/news?limit=1&offset=0").json()["items"]
    second = client.get("/api/news?limit=1&offset=1").json()["items"]
    assert [first[0]["title"], second[0]["title"]] == ["Opus is out", "OpenAI X"]

    older = client.post("/api/news/older", json={"page": 2}).json()
    assert older["saved"] == 1 and older["page"] == 2  # l'URL déjà connue n'est pas réenregistrée
    assert client.get("/api/news?offset=2").json()["items"][0]["title"] == "Ancienne 2"
    assert client.post("/api/news/older", json={"page": 1}).status_code == 422

    detail = client.get("/api/document", params={"url": "https://claude.com/blog/opus"}).json()
    assert detail["title"] == "Opus is out" and detail["news"]["accent"] == "#6a9bcc"
    assert client.get("/api/document", params={"url": "https://nulle.part/x"}).status_code == 404


def test_review_can_be_published_to_notion(client):
    assert client.post("/api/reviews/inconnue/notion").status_code == 404
    events = sse(client.post("/api/reviews", json={"url": "https://blog.example.org/fp8-article"}))
    review_id = next(e for e in events if e["type"] == "review")["review"]["id"]

    published = client.post(f"/api/reviews/{review_id}/notion").json()

    assert published["url"] == "https://www.notion.so/veille-e2e" and PUBLISHED_REVIEWS[-1] == review_id


def test_assistant_streams_claude_answer_with_news_context(client):
    client.post("/api/news/crawl")
    events = sse(client.post("/api/assistant", json={"messages": [{"role": "user", "content": "Quoi de neuf ?"}], "web": True}))

    assert [e["type"] for e in events] == ["token", "token", "final"]
    assert events[-1]["sources"] == [{"url": "https://c.org", "title": "C"}]
    call = ASSISTANT_CALLS[-1]
    assert call["messages"] == ["Quoi de neuf ?"] and call["web"] is True and "Opus is out" in call["context"]
    assert client.post("/api/assistant", json={"messages": []}).status_code == 422
    assert client.get("/api/health").json()["assistant_model"]


def test_why_page_and_rule_decisions(client):
    from app.memory import WatchMemory

    assert "Pourquoi cette veille ?" in client.get("/why").text
    memory = WatchMemory(client.app.state.store)
    for _ in range(3):
        memory.count_outcome(["hype"], approved=False)
    memory.suggest_rules(3)

    why = client.get("/api/why").json()
    assert {"profile", "records", "tags", "ranking", "evidence_rules", "last_digest"} <= set(why)
    assert [r["status"] for r in why["records"] if r["kind"] == "rule"] == ["suggested"]

    assert client.post("/api/rules/exclude:hype/accept").json()["status"] == "active"
    assert memory.active_exclusions() == ["hype"]
    assert client.post("/api/rules/exclude:absent/accept").status_code == 404
    assert client.post("/api/rules/exclude:hype/maybe").status_code == 400
