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
import markdown as markdown_lib
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.sqlite import SqliteSaver
from pydantic import BaseModel, Field

from app import observability, storage
from app.chat.agent import ChatContext, build_chat_graph, stream_chat
from app.chat.tools import ChatServices
from app.config import settings
from app.memory import WatchMemory
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
        return [
            {"role": m.type, "content": str(m.content), "sources": m.additional_kwargs.get("sources", [])}
            for m in state.get("messages", [])
        ]

    @app.post("/api/chat")
    def chat(request: ChatRequest):
        conversation_id = request.conversation_id or str(uuid4())
        storage.upsert_conversation(app.state.connection, conversation_id, request.message)
        graph = build_chat_graph(
            ChatContext(services(), deps.router_llm(app.state.connection), deps.chat_model()), app.state.saver
        )
        config = observability.trace_config(conversation_id, "chat")

        def events():
            yield _sse({"type": "conversation", "id": conversation_id})
            try:
                for event in stream_chat(graph, conversation_id, request.message, config):
                    yield _sse(event)
            except Exception as error:  # noqa: BLE001 — affiché dans l'interface
                yield _sse({"type": "error", "text": f"{type(error).__name__} : {error}"})
            finally:
                observability.flush()

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

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
            return runs.get(run_id).public(after)
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

    @app.get("/api/reports")
    def reports():
        return [
            {"name": f"{path.parent.name}/{path.name}", "date": path.stem.removeprefix("veille-")}
            for path in list_reports(settings.reports_dir)
        ]

    @app.get("/api/reports/{year}/{name}")
    def report(year: str, name: str):
        root = settings.reports_dir.resolve()
        path = (root / year / name).resolve()
        if root not in path.parents or path.suffix != ".md" or not path.exists():
            raise HTTPException(404, "Rapport introuvable")
        text = path.read_text(encoding="utf-8")
        body = text.split("---\n", 2)[2] if text.startswith("---\n") else text  # sans front matter
        html = markdown_lib.markdown(body, extensions=["tables", "fenced_code", "md_in_html"])
        return {"name": f"{year}/{name}", "markdown": text, "html": html}

    @app.get("/api/memory")
    def memory():
        watch_memory = WatchMemory(app.state.store)
        return {
            "lessons": watch_memory.lessons(limit=10),
            "tags": watch_memory.top_tags(limit=12),
            "sources": watch_memory.source_health(),
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


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def __getattr__(name: str):
    # `uvicorn app.web.server:app` : l'application n'est construite qu'à la demande.
    if name == "app":
        return create_app()
    raise AttributeError(name)
