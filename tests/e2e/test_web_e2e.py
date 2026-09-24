"""E2E de l'interface web : chat SSE, veille pilotée par l'API, rapports, mémoire, Notion.

Réels : FastAPI, LangGraph + checkpoints SQLite, Store SQLite, publishers, rendu
des rapports. Simulés : Ollama (routeur + modèle de chat), collecteurs, Notion.
"""

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
    assert prompts == {"scout", "critic", "editor", "router", "chat-system", "grill", "review"}
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
