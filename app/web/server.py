"""Interface web de la veille : chat (SSE), veilles en direct, rapports, mémoire, Notion,
base de connaissances et review d'actualités par URL.

    python -m app.main web      →  http://127.0.0.1:8000
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable
from contextlib import ExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.sqlite import SqliteSaver
from pydantic import BaseModel, Field

from app import knowledge, observability, sources_admin, storage
from app.chat.agent import ChatContext, build_chat_graph, intent_text, stream_chat
from app.chat.tools import ChatServices
from app.config import settings
from app.memory import WatchMemory
from app.harness.prompts import PromptError, load_prompt, prompt_names, reset_prompt, save_prompt
from app.reports import list_reports
from app.assistant import AssistantMessage
from app.news import CrawlReport, NewsError, SearchReport
from app.review import FetchError, Page, ReviewContext, build_review_graph, stream_review, transcript_objections
from app.web.auth import SESSION_COOKIE
from app.web.runs import RunManager
from app.why import why_payload

STATIC = Path(__file__).resolve().parent / "static"
TOKEN_COOKIE = "veille_token"


def build_authenticator():
    """Authentificateur configuré par .env, ou None si WEB_USERNAME / WEB_PASSWORD sont absents.

    Lève AuthConfigError (le serveur ne démarre pas) si la configuration est incomplète ou faible."""
    from app.web.auth import AuthConfigError, Authenticator, Throttle

    if not settings.web_login_enabled:
        return None
    if not (settings.web_username and settings.web_password):
        raise AuthConfigError("Renseigner à la fois WEB_USERNAME et WEB_PASSWORD (ou aucun des deux).")
    return Authenticator(
        settings.web_username, settings.web_password, session_hours=settings.web_session_hours,
        secret=settings.web_session_secret, min_length=settings.web_password_min_length,
        throttle=Throttle(max_failures=settings.web_login_max_failures, lock_seconds=settings.web_login_lock_seconds),
    )


def is_https(request: Request) -> bool:
    return request.headers.get("x-forwarded-proto", request.url.scheme) == "https"


def authorized(request: Request, token: str) -> bool:
    """Jeton présenté en en-tête Bearer ou en cookie (comparaison à temps constant)."""
    header = request.headers.get("authorization", "")
    presented = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
    presented = presented or request.cookies.get(TOKEN_COOKIE, "")
    return bool(presented) and secrets.compare_digest(presented.encode(), token.encode())


@dataclass
class WebDeps:
    """Dépendances remplaçables (tests E2E : faux LLM, faux collecteurs)."""

    router_llm: Callable  # connection -> StructuredLLM
    chat_model: Callable  # () -> BaseChatModel
    graph_factory: Callable  # () -> contextmanager[(graph, context)]
    notion_sync: Callable | None
    github_search: Callable[[str], list] | None
    fetch_page: Callable[[str], Page] | None = None  # défaut : app.review.fetch_page
    review_llm: Callable | None = None  # connection -> StructuredLLM ; défaut : router_llm
    inspect_source: Callable[[str], sources_admin.SourceCandidate] | None = None  # défaut : vérification réseau
    news_crawl: Callable[[dict], "CrawlReport"] | None = None  # défaut : app.news.crawl_all
    news_search: Callable[..., "SearchReport"] | None = None  # défaut : app.news.search_news (API Claude)
    news_older: Callable[[dict, int, set], "CrawlReport"] | None = None  # défaut : app.news.crawl_older
    review_publisher: Callable | None = None  # (connection, review_id) -> dict ; None : Notion non configuré
    assistant_stream: Callable | None = None  # défaut : app.assistant.stream_assistant (API Claude)
    authenticator: Any = None  # défaut : WEB_USERNAME / WEB_PASSWORD (app.web.auth.Authenticator)
    events_search: Callable[..., Any] | None = None  # défaut : app.events.search_events (API Claude)
    events_crawl: Callable[[dict], Any] | None = None  # défaut : app.events.crawl_calendars
    media_search: Callable[..., Any] | None = None  # défaut : app.media.search_media (API Claude)
    media_crawl: Callable[[dict], Any] | None = None  # défaut : app.media.crawl_media
    screenshot_capture: Callable[..., Any] | None = None  # défaut : app.screenshots.capture_news (MCP Playwright)
    benchmarks_fetch: Callable[[str], str] | None = None  # défaut : app.news.default_fetch (pages BenchLM)


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

    review_publisher = None
    if settings.notion_enabled:
        from app.notion import publish_review

        def review_publisher(connection, review_id: str) -> dict:
            return publish_review(connection, review_id, settings.notion_token, settings.notion_parent_page_id,
                                  settings.notion_api_url)

    return WebDeps(
        router_llm=get_llm,
        chat_model=get_chat_model,
        graph_factory=graph_factory,
        notion_sync=notion_sync(),
        github_search=github_search,
        review_publisher=review_publisher,
    )


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=256)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = None
    review_id: str | None = Field(None, max_length=64)  # chat de challenge d'une review


class KnowledgeUpload(BaseModel):
    filename: str = Field(min_length=4, max_length=120)
    content: str = Field(min_length=1, max_length=knowledge.MAX_UPLOAD_BYTES)
    replace: bool = False


class ReviewRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2000)


class SourceRequest(BaseModel):
    url: str = Field(min_length=4, max_length=2000)
    name: str | None = Field(None, max_length=80)


class NewsSearchRequest(BaseModel):
    topics: str = Field("", max_length=500)  # vide : centres d'intérêt du profil Grill-me
    days: int | None = Field(None, ge=1, le=60)


class RadarRequest(BaseModel):
    topics: str = Field("", max_length=500)  # vide : centres d'intérêt du profil Grill-me
    days: int | None = Field(None, ge=1, le=365)


class ScreenshotRequest(BaseModel):
    limit: int | None = Field(None, ge=1, le=40)


class OlderNewsRequest(BaseModel):
    page: int = Field(2, ge=2, le=60)


class AssistantRequest(BaseModel):
    messages: list[AssistantMessage] = Field(min_length=1, max_length=40)
    web: bool = False


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
    token = settings.web_api_token
    auth = deps.authenticator if deps.authenticator is not None else build_authenticator()
    app.state.auth = auth

    def identify(request: Request, client: str) -> tuple[str | None, JSONResponse | None]:
        """(utilisateur, refus) : cookie de session, jeton Bearer, ou HTTP Basic borné par l'anti-force brute."""
        if token and authorized(request, token):
            return "api", None
        if auth is None:
            return None, None
        if user := auth.verify(request.cookies.get(SESSION_COOKIE, "")):
            return user, None
        header = request.headers.get("authorization", "")
        if header.startswith("Basic "):
            if wait := auth.throttle.locked_for(client):
                return None, JSONResponse({"detail": f"Trop d'échecs : réessayez dans {wait} s"}, status_code=429,
                                          headers={"Retry-After": str(wait)})
            if auth.basic(header):
                auth.throttle.success(client)
                return auth.username, None
            auth.throttle.failure(client)
        return None, None

    if token or auth:
        @app.middleware("http")
        async def require_login(request: Request, call_next):
            path = request.url.path
            if token and path == "/" and "token" in request.query_params:
                # Connexion du navigateur : le jeton passe une fois dans l'URL, puis en cookie HttpOnly.
                if not secrets.compare_digest(request.query_params["token"].encode(), token.encode()):
                    return JSONResponse({"detail": "Jeton invalide"}, status_code=401)
                response = RedirectResponse("/", status_code=303)
                response.set_cookie(TOKEN_COOKIE, token, httponly=True, samesite="strict", secure=is_https(request),
                                    max_age=90 * 24 * 3600)
                return response
            user, refusal = identify(request, request.client.host if request.client else "?")
            request.state.user = user
            if refusal:
                return refusal
            # Sans login : / et /static ne portent aucune donnée. Avec login : seule la page de connexion
            # est publique ; /api/health (sonde de bin/veille et de la TUI) répond alors sans détail.
            public = {"/api/health"} | ({"/login", "/api/login", "/api/logout", "/favicon.ico"} if auth else {"/"})
            if user or path in public or (not auth and path.startswith("/static/")):
                return await call_next(request)
            if auth and request.method == "GET" and not path.startswith("/api/") \
                    and "text/html" in request.headers.get("accept", ""):
                target = path + (f"?{request.url.query}" if request.url.query else "")
                return RedirectResponse(f"/login?next={quote(target, safe='')}", status_code=303)
            return JSONResponse({"detail": "Authentification requise" if auth else "Jeton d'API requis"},
                                status_code=401, headers={"WWW-Authenticate": "Bearer"})

    @app.get("/login")
    def login_page():
        if auth is None:
            return RedirectResponse("/", status_code=303)
        return FileResponse(STATIC / "login.html", headers={"Cache-Control": "no-store"})

    @app.post("/api/login")
    def login(credentials: LoginRequest, request: Request):
        if auth is None:
            raise HTTPException(404, "Connexion désactivée (WEB_USERNAME / WEB_PASSWORD absents)")
        client = request.client.host if request.client else "?"
        if wait := auth.throttle.locked_for(client):
            raise HTTPException(429, f"Trop d'échecs de connexion : réessayez dans {wait} s.",
                                headers={"Retry-After": str(wait)})
        if not auth.check(credentials.username, credentials.password):
            auth.throttle.failure(client)
            raise HTTPException(401, "Identifiant ou mot de passe incorrect.")
        auth.throttle.success(client)
        response = JSONResponse({"user": auth.username, "expires_in": auth.session_seconds})
        response.set_cookie(SESSION_COOKIE, auth.issue(), httponly=True, samesite="strict", secure=is_https(request),
                            max_age=auth.session_seconds, path="/")
        return response

    @app.post("/api/logout")
    def logout():
        response = JSONResponse({"user": None})
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @app.get("/api/me")
    def me(request: Request):
        return {"user": getattr(request.state, "user", None), "login": auth is not None}

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
    def health(request: Request):
        if auth and not getattr(request.state, "user", None):
            return {"status": "ok", "login": True}  # sonde publique : aucun détail sans session
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
            "claude_search": bool(settings.claude_search_key) or deps.news_search is not None,
            "screenshots": settings.screenshot_enabled or deps.screenshot_capture is not None,
            "assistant_model": settings.assistant_model,
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
        title = request.message
        if request.review_id:
            review = storage.get_review(app.state.connection, request.review_id)
            if review is None:
                raise HTTPException(404, "Review inconnue")
            conversation_id, title = review["conversation_id"], f"🔬 {review['title']}"
        storage.upsert_conversation(app.state.connection, conversation_id, title)
        graph = build_chat_graph(
            ChatContext(services(), deps.router_llm(app.state.connection), deps.chat_model()), app.state.saver
        )
        trace_id = str(uuid4())  # notre trace et la trace Langfuse partagent cette graine
        config = observability.trace_config(conversation_id, "chat", trace_seed=trace_id)

        def events():
            yield _sse({"type": "conversation", "id": conversation_id})
            try:
                for event in stream_chat(graph, conversation_id, request.message, config, request.review_id):
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

    # --- Base de connaissances -------------------------------------------------------------

    @app.get("/api/knowledge")
    def knowledge_base():
        base = knowledge.load_knowledge()
        return {"files": [f.model_dump() for f in base.files],
                "entries": [e.model_dump(mode="json") | {"url": e.url} for e in base.entries],
                "domains": base.domains(), "errors": base.errors}

    @app.get("/api/knowledge/index")
    def knowledge_index():
        return knowledge.load_knowledge().keyword_index()

    @app.get("/api/knowledge/search")
    def knowledge_search(q: str = "", limit: int = 8):
        entries = knowledge.load_knowledge().search(q[:200], limit=max(1, min(limit, 30)))
        return [e.model_dump(mode="json") | {"url": e.url} for e in entries]

    @app.get("/api/knowledge/files/{name}")
    def knowledge_file(name: str):
        try:
            return knowledge.read_file(name)
        except knowledge.KnowledgeError as error:
            raise HTTPException(404, str(error))

    @app.post("/api/knowledge", status_code=201)
    def knowledge_upload(upload: KnowledgeUpload):
        try:
            return knowledge.save_upload(upload.filename, upload.content, replace=upload.replace).model_dump()
        except knowledge.KnowledgeExists as error:
            raise HTTPException(409, str(error))
        except knowledge.KnowledgeError as error:
            raise HTTPException(400, str(error))

    @app.delete("/api/knowledge/{name}")
    def knowledge_delete(name: str):
        try:
            knowledge.delete_upload(name)
        except knowledge.KnowledgeError as error:
            raise HTTPException(404, str(error))
        return {"deleted": name}

    # --- Sources de la veille (ajout par URL, skill ajout-source) ---------------------------------

    def inspect(url: str) -> sources_admin.SourceCandidate:
        try:
            return (deps.inspect_source or sources_admin.inspect_source)(url)
        except sources_admin.SourceError as error:
            raise HTTPException(400, str(error))

    @app.get("/api/sources")
    def sources():
        return sources_admin.list_sources() | {"health": WatchMemory(app.state.store).source_health()}

    @app.post("/api/sources/inspect")
    def inspect_source(request: SourceRequest):
        candidate = inspect(request.url)
        return candidate.model_dump() | {"label": candidate.label}

    @app.post("/api/sources", status_code=201)
    def add_source(request: SourceRequest):
        candidate = inspect(request.url)  # revérifiée côté serveur : jamais de source non vérifiée
        try:
            return {"added": candidate.model_dump(), "sources": sources_admin.add_source(candidate, request.name)}
        except sources_admin.SourceError as error:
            raise HTTPException(409 if "Déjà présente" in str(error) else 400, str(error))

    @app.delete("/api/sources")
    def remove_source(kind: str, value: str):
        if kind not in {"rss", "arxiv", "github"}:
            raise HTTPException(400, "kind attendu : rss, arxiv ou github")
        try:
            return sources_admin.remove_source(kind, value)
        except sources_admin.SourceError as error:
            raise HTTPException(404, str(error))

    # --- Actus en cartes ------------------------------------------------------------------------

    @app.get("/api/news")
    def news(source: str | None = None, origin: str | None = None, q: str | None = None, limit: int = 60,
             offset: int = 0):
        return {"items": storage.list_news(app.state.connection, source, origin, (q or "").strip() or None,
                                           min(max(limit, 1), 500), max(offset, 0)),
                "sources": storage.news_sources(app.state.connection),
                "claude_search": bool(settings.claude_search_key) or deps.news_search is not None,
                "model": settings.news_model}

    @app.post("/api/news/crawl")
    def news_crawl():
        from app.collectors import load_sources
        from app.news import crawl_all

        report = (deps.news_crawl or crawl_all)(load_sources(settings.sources_path))
        storage.save_news(app.state.connection, [item.record() for item in report.items])
        return {"counts": report.counts, "errors": report.errors, "saved": len(report.items)}

    @app.post("/api/news/older")
    def news_older(request: OlderNewsRequest):
        """Défilement infini : actus plus anciennes (page N des blogs, suite des flux)."""
        from app.collectors import load_sources
        from app.news import crawl_older

        known = storage.news_urls(app.state.connection)
        report = (deps.news_older or crawl_older)(load_sources(settings.sources_path), request.page, known)
        fresh = [item.record() for item in report.items if str(item.url) not in known]
        storage.save_news(app.state.connection, fresh)
        return {"counts": report.counts, "errors": report.errors, "saved": len(fresh), "page": request.page}

    @app.get("/api/document")
    def document(url: str):
        detail = storage.document_detail(app.state.connection, url)
        if detail is None:
            raise HTTPException(404, "Document inconnu de la veille")
        return detail

    @app.post("/api/news/search")
    def news_search(request: NewsSearchRequest):
        from app.news import interest_topics, search_news

        memory = WatchMemory(app.state.store)
        topics, exclusions = interest_topics(memory.interests())
        if request.topics.strip():
            topics = "\n".join(f"- {t.strip()}" for t in request.topics.split(",") if t.strip())
        try:
            report = (deps.news_search or search_news)(topics=topics, exclusions=exclusions,
                                                       memory=memory.prompt_context(), days=request.days)
        except NewsError as error:
            raise HTTPException(400, str(error))
        except Exception as error:  # noqa: BLE001 — erreur API (clé refusée, quota…) affichée telle quelle
            from app.news import explain_api_error

            raise HTTPException(502, explain_api_error(error))
        storage.save_news(app.state.connection, [item.record() for item in report.items])
        return {"saved": len(report.items), "items": [item.record() for item in report.items],
                "results_seen": report.results_seen, "rejected": report.rejected,
                "searches": report.searches, "model": report.model, "topics": report.topics}

    # --- Aperçus des actus (captures d'écran par le MCP Playwright) ------------------------------

    @app.post("/api/news/screenshots")
    def news_screenshots(request: ScreenshotRequest):
        from app.screenshots import ScreenshotError, capture_news

        if deps.screenshot_capture is None and not settings.screenshot_enabled:
            raise HTTPException(409, "Captures désactivées : SCREENSHOT_ENABLED=true dans la configuration "
                                     "(Node.js et le navigateur de Playwright requis).")
        candidates = [n for n in storage.list_news(app.state.connection, limit=200)
                      if not n.get("image") and not n.get("screenshot")]
        try:
            report = (deps.screenshot_capture or capture_news)(candidates, limit=request.limit)
        except ScreenshotError as error:
            raise HTTPException(502, str(error))
        by_id = {n["id"]: n for n in candidates}
        storage.save_news(app.state.connection, [
            {k: v for k, v in by_id[item_id].items() if k != "fetched_at"} | {"screenshot": f"/api/news/{item_id}/screenshot"}
            for item_id in report.saved if item_id in by_id])
        return {"saved": len(report.saved), "errors": report.errors, "skipped": report.skipped,
                "remaining": max(0, len(candidates) - len(report.saved) - len(report.errors))}

    @app.get("/api/news/{item_id}/screenshot")
    def news_screenshot(item_id: str):
        from app.screenshots import ScreenshotError, screenshot_path

        try:
            path = screenshot_path(item_id)
        except ScreenshotError:
            raise HTTPException(404, "Aperçu introuvable")
        if not path.exists():
            raise HTTPException(404, "Aperçu introuvable")
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})

    # --- Événements IA ---------------------------------------------------------------------------

    def radar_topics(request: RadarRequest) -> tuple[str, str]:
        from app.news import interest_topics

        memory = WatchMemory(app.state.store)
        topics, _ = interest_topics(memory.interests())
        if request.topics.strip():
            topics = "\n".join(f"- {t.strip()}" for t in request.topics.split(",") if t.strip())
        return topics, memory.prompt_context()

    def claude_call(function: Callable, **kwargs):
        from app.news import explain_api_error

        try:
            return function(**kwargs)
        except NewsError as error:
            raise HTTPException(400, str(error))
        except Exception as error:  # noqa: BLE001 — erreur API (clé refusée, quota…) affichée telle quelle
            raise HTTPException(502, explain_api_error(error))

    @app.get("/api/events")
    def events(when: str = "upcoming", kind: str | None = None, q: str | None = None, limit: int = 100):
        if when not in {"upcoming", "past", "all"}:
            raise HTTPException(400, "when attendu : upcoming, past ou all")
        return {"items": storage.list_events(app.state.connection, when, kind or None, (q or "").strip() or None,
                                             limit=min(max(limit, 1), 300)),
                "claude_search": bool(settings.claude_search_key) or deps.events_search is not None}

    @app.post("/api/events/search")
    def events_search(request: RadarRequest):
        from app.events import search_events

        topics, memory = radar_topics(request)
        report = claude_call(deps.events_search or search_events, topics=topics, memory=memory, horizon=request.days)
        storage.save_events(app.state.connection, [item.record() for item in report.items])
        return {"saved": len(report.items), "items": [item.record() for item in report.items],
                "results_seen": report.results_seen, "rejected": report.rejected,
                "unverified_dates": report.unverified_dates, "searches": report.searches}

    @app.post("/api/events/crawl")
    def events_crawl():
        from app.collectors import load_sources
        from app.events import crawl_calendars

        report = (deps.events_crawl or crawl_calendars)(load_sources(settings.sources_path))
        storage.save_events(app.state.connection, [item.record() for item in report.items])
        return {"saved": len(report.items), "errors": report.errors}

    @app.get("/api/events.ics")
    def events_calendar():
        """Événements à venir datés, au format iCalendar (abonnement depuis un agenda)."""
        from fastapi.responses import Response

        from app.events import to_ics

        return Response(to_ics(storage.list_events(app.state.connection, "upcoming", limit=300)),
                        media_type="text/calendar; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="veille-evenements.ics"'})

    # --- Vidéos, podcasts, émissions ----------------------------------------------------------------

    @app.get("/api/media")
    def media(kind: str | None = None, source: str | None = None, q: str | None = None, limit: int = 60,
              offset: int = 0):
        return {"items": storage.list_media(app.state.connection, kind or None, source or None,
                                            (q or "").strip() or None, min(max(limit, 1), 300), max(offset, 0)),
                "sources": storage.media_sources(app.state.connection),
                "claude_search": bool(settings.claude_search_key) or deps.media_search is not None}

    @app.post("/api/media/search")
    def media_search(request: RadarRequest):
        from app.media import search_media

        topics, memory = radar_topics(request)
        report = claude_call(deps.media_search or search_media, topics=topics, memory=memory, days=request.days)
        storage.save_media(app.state.connection, [item.record() for item in report.items])
        return {"saved": len(report.items), "items": [item.record() for item in report.items],
                "results_seen": report.results_seen, "rejected": report.rejected, "searches": report.searches}

    @app.post("/api/media/crawl")
    def media_crawl():
        from app.collectors import load_sources
        from app.media import crawl_media

        report = (deps.media_crawl or crawl_media)(load_sources(settings.sources_path))
        storage.save_media(app.state.connection, [item.record() for item in report.items])
        return {"saved": len(report.items), "counts": report.counts, "errors": report.errors}

    # --- Benchmarks (BenchLM.ai) ------------------------------------------------------------------

    def benchmark_fetch():
        from app.news import default_fetch

        return deps.benchmarks_fetch or default_fetch

    @app.get("/api/benchmarks")
    def benchmarks(category: str | None = None, q: str | None = None):
        from app.benchmarks import analyze, catalog_overview

        items = storage.list_benchmarks(app.state.connection, category or None, (q or "").strip()[:100] or None)
        everything = items if not (category or q) else storage.list_benchmarks(app.state.connection)
        return {"items": [{k: v for k, v in item.items() if k != "detail"}
                          | {"analysis": analyze(item, item["detail"]) if item["detail"] else None} for item in items],
                "overview": catalog_overview(everything), "source": settings.benchmarks_url}

    @app.post("/api/benchmarks/crawl")
    def benchmarks_crawl():
        from app.benchmarks import crawl_catalog

        try:
            report = crawl_catalog(benchmark_fetch())
        except (NewsError, FetchError) as error:
            raise HTTPException(502, str(error))
        storage.save_benchmarks(app.state.connection, [item.record() for item in report.items])
        return {"saved": len(report.items), "announced": report.total_announced, "period": report.period,
                "invalid": report.invalid}

    @app.get("/api/benchmarks/{key}")
    def benchmark(key: str, refresh: bool = False):
        from app.benchmarks import KEY, analyze, fetch_detail

        if not KEY.match(key) or (record := storage.get_benchmark(app.state.connection, key)) is None:
            raise HTTPException(404, "Benchmark inconnu : actualisez le catalogue.")
        key = record["key"]  # clé canonique (la requête peut porter le chemin de page ou une autre casse)
        if refresh or storage.benchmark_detail_stale(app.state.connection, key, settings.benchmarks_detail_ttl_hours):
            try:
                detail = fetch_detail(key, record.get("slug"), benchmark_fetch())
                storage.save_benchmark_detail(app.state.connection, key, detail.model_dump())
                record = storage.get_benchmark(app.state.connection, key)
            except (NewsError, FetchError) as error:
                if not record["detail"]:
                    raise HTTPException(502, str(error))
                record["stale_error"] = str(error)  # détail en cache affiché malgré l'échec
        return record | {"analysis": analyze(record, record["detail"])}

    # --- Review d'une actualité par URL --------------------------------------------------------

    def review_graph():
        from app.review import fetch_page

        context = ReviewContext(
            llm=(deps.review_llm or deps.router_llm)(app.state.connection),
            connection=app.state.connection,
            fetch=deps.fetch_page or fetch_page,
            memory=lambda: WatchMemory(app.state.store).prompt_context(),
        )
        return build_review_graph(context)

    def review_events(inputs: dict, trace_seed: str):
        def events():
            try:
                session = (inputs.get("previous") or {}).get("id") or trace_seed
                config = observability.trace_config(session, "review", trace_seed=trace_seed)
                for event in stream_review(review_graph(), inputs, config):
                    if event["type"] == "review" and event["review"]:
                        record = event["review"]
                        storage.save_trace(
                            app.state.connection, trace_seed, "article", record["id"], f"Review : {record['title']}",
                            event["steps"], {"nodes": [s["node"] for s in event["steps"]], "agents": ["reviewer"],
                                             "skills": ["review-actu", "veille-tech"], "prompts": ["review.md"],
                                             "data": ["knowledge/", "reviews (SQLite)"]},
                        )
                        event |= {"trace_url": f"/trace/{trace_seed}",
                                  "langfuse": observability.langfuse_links(trace_seed, session)}
                    yield _sse(event)
            except FetchError as error:
                yield _sse({"type": "error", "text": str(error)})
            except Exception as error:  # noqa: BLE001 — affiché dans l'interface
                yield _sse({"type": "error", "text": f"{type(error).__name__} : {error}"})
            finally:
                observability.flush()

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/reviews")
    def create_review(request: ReviewRequest):
        return review_events({"url": request.url.strip()}, str(uuid4()))

    @app.get("/api/reviews")
    def reviews():
        return storage.list_reviews(app.state.connection)

    @app.get("/api/reviews/{review_id}")
    def get_review(review_id: str):
        record = storage.get_review(app.state.connection, review_id)
        if record is None:
            raise HTTPException(404, "Review inconnue")
        traces = [t for t in storage.list_traces(app.state.connection, review_id) if t["kind"] == "article"]
        record["trace_url"] = f"/trace/{traces[-1]['id']}" if traces else None
        return record

    @app.post("/api/reviews/{review_id}/revise")
    def revise_review(review_id: str):
        record = storage.get_review(app.state.connection, review_id)
        if record is None:
            raise HTTPException(404, "Review inconnue")
        graph = build_chat_graph(
            ChatContext(services(), deps.router_llm(app.state.connection), deps.chat_model()), app.state.saver
        )
        messages = graph.get_state({"configurable": {"thread_id": f"chat-{record['conversation_id']}"}}).values.get("messages", [])
        if not messages:
            raise HTTPException(409, "Aucun échange à prendre en compte : challengez d'abord la review dans le chat.")
        return review_events({"url": record["url"], "previous": record, "objections": transcript_objections(messages)},
                             str(uuid4()))

    @app.post("/api/reviews/{review_id}/notion")
    def publish_review_to_notion(review_id: str):
        if deps.review_publisher is None:
            raise HTTPException(409, "Notion non configuré (NOTION_TOKEN, NOTION_PARENT_PAGE_ID).")
        if storage.get_review(app.state.connection, review_id) is None:
            raise HTTPException(404, "Review inconnue")
        try:
            return deps.review_publisher(app.state.connection, review_id)
        except RuntimeError as error:
            raise HTTPException(409, str(error))
        except Exception as error:  # noqa: BLE001 — erreur MCP / API Notion affichée
            raise HTTPException(502, f"{type(error).__name__} : {error}")

    # --- Assistant rapide (API Claude directe) ------------------------------------------------

    @app.post("/api/assistant")
    def assistant(request: AssistantRequest):
        from app.assistant import build_context, stream_assistant

        if deps.assistant_stream is None and not settings.claude_search_key:
            raise HTTPException(400, "Clé API Claude absente : renseigner CLAUDE_API dans .env puis redémarrer.")
        context = build_context(storage.list_news(app.state.connection, limit=12),
                                storage.recent_digests(app.state.connection, limit=1))

        def events():
            try:
                for event in (deps.assistant_stream or stream_assistant)(request.messages, context=context,
                                                                          web=request.web):
                    yield _sse(event)
            except NewsError as error:
                yield _sse({"type": "error", "text": str(error)})
            except Exception as error:  # noqa: BLE001 — erreur API (quota, clé refusée…) affichée
                from app.news import explain_api_error

                yield _sse({"type": "error", "text": explain_api_error(error)})

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

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

    # --- « Pourquoi cette veille ? » : profil, mémoire typée, règles, ranking ----------------

    @app.get("/why")
    def why_page():
        return FileResponse(STATIC / "why.html")

    @app.get("/api/why")
    def why():
        return why_payload(app.state.connection, app.state.store)

    @app.post("/api/rules/{key}/{decision}")
    def decide_rule(key: str, decision: str):
        """Accepter (règle active) ou écarter une règle suggérée après des rejets récurrents."""
        if decision not in ("accept", "dismiss"):
            raise HTTPException(400, "Décision attendue : accept | dismiss")
        try:
            record = WatchMemory(app.state.store).decide_rule(key, decision == "accept")
        except KeyError:
            raise HTTPException(404, "Règle inconnue")
        return record.model_dump(mode="json")

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
    if kind == "article":
        from app.llm import StructuredLLM

        context = ReviewContext(llm=StructuredLLM(lambda messages, schema: "{}", model="diagramme"),
                                connection=connection)
        return {"review": build_review_graph(context).get_graph().draw_mermaid()}
    from app.harness.skills import load_skill
    from app.llm import StructuredLLM
    from app.workflow.graph import build_graph
    from app.workflow.state import HarnessContext
    from app.workflow.subagents import build_editorial, build_evidence, build_quality, build_research, build_review

    context = HarnessContext(
        llm=StructuredLLM(lambda messages, schema: "{}", model="diagramme"),
        connection=connection, skill=load_skill(settings.skill_path), output_dir=settings.output_dir,
    )
    return {
        "veille": build_graph(None, context).get_graph().draw_mermaid(),
        "quality": build_quality(context).get_graph().draw_mermaid(),
        "research": build_research(context).get_graph().draw_mermaid(),
        "review": build_review(context).get_graph().draw_mermaid(),
        "evidence": build_evidence(context).get_graph().draw_mermaid(),
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
