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
from tests.conftest import NOW, documents, fake_ollama, make_document

DOCS = documents(*(make_document(i, "rss") for i in range(4)))


def chat_router(tool="search_watch", query="quantization"):
    return lambda connection: StructuredLLM(
        lambda messages, schema: json.dumps({"tool": tool, "query": query}), model="fake-router"
    )


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
    assert "<table>" in report["html"] and "🛰️ Veille LLM / GenAI" in report["html"]
    assert report["markdown"].startswith("---\ntitle:")

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
