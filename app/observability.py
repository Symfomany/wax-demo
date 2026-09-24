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


def langfuse_callbacks() -> list:
    if not settings.langfuse_enabled:
        return []
    if not langfuse_ready():
        print("[yellow]Langfuse activé mais clés absentes : traçage ignoré.[/yellow]")
        return []

    from langfuse.langchain import CallbackHandler

    _langfuse_client()
    return [CallbackHandler(public_key=settings.langfuse_public_key)]


def trace_config(session_id: str, kind: str, user_id: str = "local-user", **extra) -> dict:
    """Config LangChain/LangGraph commune : callbacks + métadonnées de session."""
    setup_langsmith()
    return {
        "callbacks": langfuse_callbacks(),
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
