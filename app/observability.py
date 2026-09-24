"""Traçage optionnel : Langfuse (callbacks) et LangSmith (variables d'environnement).

Les deux peuvent être actifs en même temps. Chaque trace porte :
- un identifiant de session (run de veille ou conversation du chat), pour
  regrouper les appels dans « Sessions » (Langfuse) et « Threads » (LangSmith) ;
- des tags (`veille`, `chat`) et le modèle utilisé, pour filtrer.
"""

import os
from functools import lru_cache

from rich import print

from app.config import settings


def setup_langsmith() -> bool:
    """LangChain trace automatiquement vers LangSmith si ces variables existent.

    pydantic-settings lit .env sans l'exporter : on recopie les valeurs dans
    l'environnement du processus (jamais dans un fichier).
    """
    if not settings.langsmith_tracing:
        return False
    if not settings.langsmith_api_key:
        print("[yellow]LangSmith activé mais LANGSMITH_API_KEY absente : traçage ignoré.[/yellow]")
        return False
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key
    os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
    os.environ["LANGSMITH_ENDPOINT"] = settings.langsmith_endpoint
    return True


def langfuse_ready() -> bool:
    return bool(
        settings.langfuse_enabled and settings.langfuse_public_key and settings.langfuse_secret_key
    )


@lru_cache(maxsize=1)
def _langfuse_client():
    from langfuse import Langfuse

    # Le client est enregistré par clé publique ; les handlers le retrouvent.
    return Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
    )


def langfuse_trace_id(seed: str) -> str:
    """Identifiant de trace Langfuse déterministe (32 hex) dérivé de notre identifiant :
    l'URL de la trace est connue avant même l'appel."""
    from langfuse import Langfuse

    return Langfuse.create_trace_id(seed=seed)


def langfuse_callbacks(trace_seed: str | None = None) -> list:
    if not settings.langfuse_enabled:
        return []
    if not langfuse_ready():
        print("[yellow]Langfuse activé mais clés absentes : traçage ignoré.[/yellow]")
        return []

    from langfuse.langchain import CallbackHandler

    _langfuse_client()
    context = {"trace_id": langfuse_trace_id(trace_seed)} if trace_seed else None
    return [CallbackHandler(public_key=settings.langfuse_public_key, trace_context=context)]


_project_id_cache: dict[str, str] = {}


def langfuse_project_id() -> str | None:
    """LANGFUSE_PROJECT_ID si défini (aucun appel réseau), sinon lu via l'API.
    Seul un succès est mis en cache : un échec réseau sera retenté au prochain lien."""
    if settings.langfuse_project_id:
        return settings.langfuse_project_id
    if "id" not in _project_id_cache:
        try:
            project_id = _langfuse_client()._get_project_id()
        except Exception:  # noqa: BLE001 — un lien manquant ne doit jamais casser une réponse
            return None
        if project_id:
            _project_id_cache["id"] = project_id
    return _project_id_cache.get("id")


def langfuse_links(trace_seed: str, session_id: str) -> dict[str, str] | None:
    """Liens vers la trace (ce tour / ce run) et la session (conversation / run) dans Langfuse."""
    if not langfuse_ready():
        return None
    project_id = langfuse_project_id()
    if not project_id:
        return None
    base = f"{settings.langfuse_host.rstrip('/')}/project/{project_id}"
    return {
        "trace": f"{base}/traces/{langfuse_trace_id(trace_seed)}",
        "session": f"{base}/sessions/{session_id}",
    }


def trace_config(
    session_id: str, kind: str, user_id: str = "local-user", trace_seed: str | None = None, **extra
) -> dict:
    """Config LangChain/LangGraph commune : callbacks + métadonnées de session."""
    setup_langsmith()
    return {
        "callbacks": langfuse_callbacks(trace_seed),
        "tags": [kind],
        "metadata": {
            # Langfuse : regroupement par session et par utilisateur
            "langfuse_session_id": session_id,
            "langfuse_user_id": user_id,
            "langfuse_tags": [kind],
            # LangSmith : vue « Threads » (clé session_id reconnue)
            "session_id": session_id,
            "model": settings.llm_model,
            **extra,
        },
        "run_name": kind,
    }


def status() -> dict[str, bool]:
    return {
        "langfuse": langfuse_ready(),
        "langsmith": bool(settings.langsmith_tracing and settings.langsmith_api_key),
    }


def flush() -> None:
    if langfuse_ready():
        from langfuse import get_client

        get_client(public_key=settings.langfuse_public_key).flush()
