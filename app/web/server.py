"""Interface web de la veille : chat (SSE), veilles en direct, rapports, mémoire, Notion.

    python -m app.main web      →  http://127.0.0.1:8000
"""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import ExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.sqlite import SqliteSaver
from pydantic import BaseModel, Field

from app import observability, storage
from app.chat.agent import ChatContext, build_chat_graph, intent_text, stream_chat
from app.chat.tools import ChatServices
from app.config import settings
from app.memory import WatchMemory
from app.harness.prompts import PromptError, load_prompt, prompt_names, reset_prompt, save_prompt
from app.reports import list_reports
from app.web.runs import RunManager

STATIC = Path(__file__).resolve().parent / "static"


@dataclass
class WebDeps:
    """Dépendances remplaçables (tests E2E : faux LLM, faux collecteurs)."""

    router_llm: Callable  # connection -> StructuredLLM
    chat_model: Callable  # () -> BaseChatModel
    graph_factory: Callable  # () -> contextmanager[(graph, context)]
    notion_sync: Callable | None
    github_search: Callable[[str], list] | None


def production_deps() -> WebDeps:
    from app.collectors.github_mcp import discover, stdio_connection
    from app.llm import get_chat_model, get_llm
    from app.mcp_client import run_mcp
    from app.runtime import make_context, notion_sync, open_graph

    @contextmanager
    def graph_factory():
        connection = storage.connect(settings.database_path)
        context = make_context(connection, human_approval=True)
        with open_graph(context) as graph:
            yield graph, context

    def github_search(query: str) -> list:
        return run_mcp(discover([query], stdio_connection(settings.github_token, settings.github_api_url),
                                max_repos=5, per_query=5, readme_chars=500))

    return WebDeps(
        router_llm=get_llm,
        chat_model=get_chat_model,
        graph_factory=graph_factory,
        notion_sync=notion_sync(),
        github_search=github_search,
    )


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = None


class RunRequest(BaseModel):
    collect: bool = True
    mcp: bool = True
    keywords: list[str] = Field(default_factory=list, max_length=10)
    match_all: bool = False
    sources: list[str] = Field(default_factory=list)  # rss, arxiv, github_releases, github_mcp
    max_age_days: int | None = Field(None, ge=1, le=365)
    max_documents: int | None = Field(None, ge=1, le=60)


class SearchRequest(BaseModel):
    keywords: list[str] = Field(default_factory=list, max_length=10)
    match_all: bool = False
    sources: list[str] = Field(default_factory=list)  # rss, arxiv, github, github-mcp
    since: str | None = None  # AAAA-MM-JJ
    until: str | None = None
    published: bool | None = None
    limit: int = Field(20, ge=1, le=50)


class PromptRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20000)


class GrillAnswer(BaseModel):
    options: list[str] = Field(default_factory=list, max_length=20)
    text: str = Field("", max_length=500)
    recommended: bool = False


class Decision(BaseModel):
    approved: bool
    note: str = Field("", max_length=500)


def create_app(deps: WebDeps | None = None) -> FastAPI:
    deps = deps or production_deps()
    runs = RunManager(deps.graph_factory, min_relevance=settings.min_relevance)
    stack = ExitStack()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from app.runtime import open_store

        settings.memory_db_path.parent.mkdir(parents=True, exist_ok=True)
        app.state.connection = storage.connect(settings.database_path)
        app.state.store = stack.enter_context(open_store())
        app.state.saver = stack.enter_context(
            SqliteSaver.from_conn_string(str(settings.memory_db_path.with_name("chat.db")))
        )
        yield
        stack.close()

    app = FastAPI(title="LLM Watch Harness", lifespan=lifespan)
    diagram_cache: dict[str, dict[str, str]] = {}
    app.state.runs = runs
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    def services() -> ChatServices:
        return ChatServices(
            connection=app.state.connection,
            store=app.state.store,
            reports_dir=settings.reports_dir,
            start_run=lambda options: runs.start(options),
            notion_sync=deps.notion_sync,
            github_search=deps.github_search,
        )

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/health")
    def health():
        ollama, models = False, []
        try:
            tags = httpx.get(f"{settings.ollama_url}/api/tags", timeout=2).json()
            models = [model["name"] for model in tags.get("models", [])]
            ollama = True
        except (httpx.HTTPError, ValueError):
            pass
        return {
            "ollama": ollama,
            "model": settings.llm_model,
            "model_available": settings.llm_model in models or f"{settings.llm_model}:latest" in models,
            "tracing": observability.status(),
            "notion": deps.notion_sync is not None,
            "memory": storage.memory_stats(app.state.connection),
        }

    # --- Chat ----------------------------------------------------------------------

    @app.get("/api/conversations")
    def conversations():
        return storage.list_conversations(app.state.connection)

    @app.get("/api/conversations/{conversation_id}")
    def conversation(conversation_id: str):
        graph = build_chat_graph(
            ChatContext(services(), deps.router_llm(app.state.connection), deps.chat_model()), app.state.saver
        )
        state = graph.get_state({"configurable": {"thread_id": f"chat-{conversation_id}"}}).values
        traces = iter(storage.list_traces(app.state.connection, conversation_id))
        messages = []
        for m in state.get("messages", []):
            item = {"role": m.type, "content": m.text, "sources": m.additional_kwargs.get("sources", [])}
            if m.type == "ai":  # une trace par réponse, dans l'ordre
                trace = next(traces, None)
                item["trace_url"] = f"/trace/{trace['id']}" if trace else None
                item["engaged"] = trace["engaged"] if trace else None
                item["langfuse"] = observability.langfuse_links(trace["id"], conversation_id) if trace else None
            messages.append(item)
        return messages

    @app.post("/api/chat")
    def chat(request: ChatRequest):
        conversation_id = request.conversation_id or str(uuid4())
        storage.upsert_conversation(app.state.connection, conversation_id, request.message)
        graph = build_chat_graph(
            ChatContext(services(), deps.router_llm(app.state.connection), deps.chat_model()), app.state.saver
        )
        trace_id = str(uuid4())  # notre trace et la trace Langfuse partagent cette graine
        config = observability.trace_config(conversation_id, "chat", trace_seed=trace_id)

        def events():
            yield _sse({"type": "conversation", "id": conversation_id})
            try:
                for event in stream_chat(graph, conversation_id, request.message, config):
                    if event["type"] == "final":
                        # Parcours du graphe de ce tour : consultable dans un nouvel onglet.
                        title = intent_text(request.message) or request.message
                        quoted = request.message.count("\n>") + request.message.startswith(">")
                        storage.save_trace(app.state.connection, trace_id, "chat", conversation_id,
                                           title + (f" (+{quoted} citation(s))" if quoted else ""),
                                           event["steps"], event["engaged"])
                        event |= {"trace_id": trace_id, "trace_url": f"/trace/{trace_id}",
                                  "langfuse": observability.langfuse_links(trace_id, conversation_id)}
                    yield _sse(event)
            except Exception as error:  # noqa: BLE001 — affiché dans l'interface
                yield _sse({"type": "error", "text": f"{type(error).__name__} : {error}"})
            finally:
                observability.flush()

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # --- Recherche -------------------------------------------------------------------

    @app.post("/api/search")
    def search(request: SearchRequest):
        try:
            results = storage.search_documents(
                app.state.connection, " ".join(request.keywords), limit=request.limit,
                sources=request.sources or None, since=request.since, until=request.until,
                published=request.published, match_all=request.match_all,
            )
        except ValueError as error:
            raise HTTPException(400, str(error))
        return {"count": len(results), "results": results}

    # --- Veilles -------------------------------------------------------------------

    @app.post("/api/runs")
    def start_run(request: RunRequest):
        try:
            return {"id": runs.start(request.model_dump())}
        except RuntimeError as error:
            raise HTTPException(409, str(error))

    @app.get("/api/runs")
    def list_runs():
        return runs.list()

    @app.get("/api/runs/{run_id}")
    def run_events(run_id: str, after: int = 0):
        try:
            return runs.get(run_id).public(after) | {"langfuse": observability.langfuse_links(run_id, run_id)}
        except KeyError:
            raise HTTPException(404, "Run inconnu")

    @app.post("/api/runs/{run_id}/decision")
    def decide(run_id: str, decision: Decision):
        try:
            runs.resume(run_id, decision.approved, decision.note)
        except KeyError:
            raise HTTPException(404, "Run inconnu")
        except RuntimeError as error:
            raise HTTPException(409, str(error))
        return {"id": run_id, "status": "running"}

    # --- Rapports, mémoire, Notion ------------------------------------------------------

    def report_file(year: str, name: str, suffix: str) -> Path:
        """Chemin d'un rapport, confiné à REPORTS_DIR (pas de traversée de répertoire)."""
        root = settings.reports_dir.resolve()
        path = (root / year / name).resolve()
        if root not in path.parents or path.suffix != suffix or not path.exists():
            raise HTTPException(404, "Rapport introuvable")
        return path

    @app.get("/api/reports")
    def reports():
        return [
            {"name": f"{path.parent.name}/{path.name}", "date": path.stem.removeprefix("veille-"),
             "html": f"/reports/{path.parent.name}/{path.with_suffix('.html').name}"
             if path.with_suffix(".html").exists() else None}
            for path in list_reports(settings.reports_dir)
        ]

    @app.get("/api/reports/{year}/{name}")
    def report(year: str, name: str):
        # Markdown brut uniquement : l'interface l'affiche comme texte. La version lisible
        # est le rapport HTML (échappé à la génération), servi par /reports/…
        path = report_file(year, name, ".md")
        html = path.with_suffix(".html")
        return {"name": f"{year}/{name}", "markdown": path.read_text(encoding="utf-8"),
                "html_url": f"/reports/{year}/{html.name}" if html.exists() else None}

    @app.get("/reports/{year}/{name}")
    def report_html(year: str, name: str):
        return FileResponse(report_file(year, name, ".html"), media_type="text/html",
                            headers={"Content-Security-Policy": "script-src 'none'; object-src 'none'"})

    # --- Prompts éditables ------------------------------------------------------------

    @app.get("/api/prompts")
    def prompts():
        return [{"name": name, "description": load_prompt(name)["description"],
                 "overridden": load_prompt(name)["overridden"]} for name in prompt_names()]

    @app.get("/api/prompts/{name}")
    def get_prompt(name: str):
        try:
            return load_prompt(name)
        except PromptError as error:
            raise HTTPException(404, str(error))

    @app.put("/api/prompts/{name}")
    def put_prompt(name: str, request: PromptRequest):
        try:
            return save_prompt(name, request.text)
        except PromptError as error:
            raise HTTPException(400, str(error))

    @app.delete("/api/prompts/{name}")
    def delete_prompt(name: str):
        try:
            return reset_prompt(name)
        except PromptError as error:
            raise HTTPException(404, str(error))

    # --- Grill-me ----------------------------------------------------------------------

    def grill_graph():
        from app.collectors import load_sources
        from app.grill import GrillContext, build_grill_graph

        sources = load_sources(settings.sources_path)
        feeds = {s["url"] for s in sources.get("rss", [])} | set(sources.get("arxiv", {}).get("feeds", []))
        context = GrillContext(llm=deps.router_llm(app.state.connection), configured_feeds=feeds)
        return build_grill_graph(context, app.state.saver, app.state.store)

    def grill_step(result: dict, session_id: str) -> dict:
        if "__interrupt__" in result:
            return {"session": session_id, "question": result["__interrupt__"][0].value}
        WatchMemory(app.state.store).export_markdown(settings.claude_memory_path)
        return {"session": session_id, "profile": result["profile"]}

    @app.post("/api/grill")
    def grill_start():
        session_id = str(uuid4())
        config = {"configurable": {"thread_id": f"grill-{session_id}"},
                  **observability.trace_config(session_id, "grill", trace_seed=session_id)}
        return grill_step(grill_graph().invoke({"queue": ["domains"]}, config), session_id)

    @app.post("/api/grill/{session_id}/answer")
    def grill_answer(session_id: str, answer: GrillAnswer):
        from langgraph.types import Command

        from app.grill import resume_payload

        graph = grill_graph()
        config = {"configurable": {"thread_id": f"grill-{session_id}"},
                  **observability.trace_config(session_id, "grill", trace_seed=session_id)}
        if not graph.get_state(config).next:
            raise HTTPException(409, "Entretien terminé ou inconnu : relancez Grill-me.")
        return grill_step(graph.invoke(Command(resume=resume_payload(answer.model_dump())), config), session_id)

    @app.get("/api/grill/profile")
    def grill_profile():
        return WatchMemory(app.state.store).interests() or {}

    # --- Traces : parcours dans le graphe ------------------------------------------------

    @app.get("/trace/{trace_id}")
    def trace_page(trace_id: str):
        return FileResponse(STATIC / "trace.html")

    @app.get("/api/traces/{trace_id}")
    def trace(trace_id: str):
        found = storage.get_trace(app.state.connection, trace_id)
        if not found:
            raise HTTPException(404, "Trace inconnue")
        visited: dict[str, list[str]] = {}
        for step in found["steps"]:
            visited.setdefault(step["graph"], []).append(step["node"])
        found["langfuse"] = observability.langfuse_links(found["id"], found["session_id"])
        found["diagrams"] = [
            {"graph": name, "mermaid": highlight(mermaid, visited.get(name, []))}
            for name, mermaid in graph_diagrams(found["kind"]).items()
        ]
        return found

    def graph_diagrams(kind: str) -> dict[str, str]:
        if kind not in diagram_cache:
            diagram_cache[kind] = build_diagrams(kind, services(), app.state.connection)
        return diagram_cache[kind]

    @app.get("/api/memory")
    def memory():
        watch_memory = WatchMemory(app.state.store)
        return {
            "lessons": watch_memory.lessons(limit=10),
            "tags": watch_memory.top_tags(limit=12),
            "sources": watch_memory.source_health(),
            "interests": watch_memory.interests(),
            "notion": storage.current_notion_page(app.state.connection),
        }

    @app.post("/api/notion/sync")
    def notion():
        if deps.notion_sync is None:
            raise HTTPException(400, "Notion non configuré (NOTION_TOKEN, NOTION_PARENT_PAGE_ID).")
        try:
            return deps.notion_sync(app.state.connection)
        except RuntimeError as error:
            raise HTTPException(502, str(error))

    return app


def build_diagrams(kind: str, services: ChatServices, connection) -> dict[str, str]:
    """Mermaid des graphes compilés (graphe principal + sous-agents pour une veille)."""
    if kind == "chat":
        return {"chat": build_chat_graph(ChatContext(services, None, None)).get_graph().draw_mermaid()}
    from app.harness.skills import load_skill
    from app.llm import StructuredLLM
    from app.workflow.graph import build_graph
    from app.workflow.state import HarnessContext
    from app.workflow.subagents import build_editorial, build_research, build_review

    context = HarnessContext(
        llm=StructuredLLM(lambda messages, schema: "{}", model="diagramme"),
        connection=connection, skill=load_skill(settings.skill_path), output_dir=settings.output_dir,
    )
    return {
        "veille": build_graph(None, context).get_graph().draw_mermaid(),
        "research": build_research(context).get_graph().draw_mermaid(),
        "review": build_review(context).get_graph().draw_mermaid(),
        "editorial": build_editorial(context).get_graph().draw_mermaid(),
    }


def highlight(mermaid: str, nodes: list[str]) -> str:
    """Colorie les nœuds traversés (et __start__/__end__ s'il y a eu un parcours)."""
    visited = list(dict.fromkeys(n for n in nodes if not n.startswith("__")))
    if not visited:
        return mermaid
    return mermaid.rstrip() + (
        "\n\tclassDef visited fill:#c9f2d6,stroke:#1f7a45,stroke-width:3px,color:#0b2e19;\n"
        f"\tclass {','.join(visited)} visited;\n"
    )


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def __getattr__(name: str):
    # `uvicorn app.web.server:app` : l'application n'est construite qu'à la demande.
    if name == "app":
        return create_app()
    raise AttributeError(name)
