"""Annulation coopérative d'une génération (chat) ou d'une veille (run).

Un `CancelToken` est un callback LangChain ajouté à la config du graphe : les appels LLM
des nœuds en héritent. Une fois annulé, il lève `Cancelled` au démarrage de tout nouvel
appel LLM et à chaque token streamé, ce qui ferme la requête (Ollama arrête de générer).
Un appel LLM non streamé déjà en cours va, lui, jusqu'à son terme.
"""

from __future__ import annotations

import threading

from langchain_core.callbacks import BaseCallbackHandler


class Cancelled(RuntimeError):
    """Tâche annulée par l'utilisateur."""


class CancelToken(BaseCallbackHandler):
    raise_error = True  # sinon LangChain journalise l'exception au lieu de la propager

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        if self._event.is_set():
            raise Cancelled("annulé par l'utilisateur")

    def on_llm_start(self, *args, **kwargs) -> None:
        self.check()

    def on_chat_model_start(self, *args, **kwargs) -> None:
        self.check()

    def on_llm_new_token(self, *args, **kwargs) -> None:
        self.check()


def with_token(config: dict, token: CancelToken) -> dict:
    """Config LangGraph + jeton d'annulation (en plus des callbacks de traçage)."""
    return config | {"callbacks": [*(config.get("callbacks") or []), token]}
