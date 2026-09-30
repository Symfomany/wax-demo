"""Métriques Prometheus de l'appli (``GET /metrics``) : usage LLM et requêtes HTTP.

Ollama n'expose pas de métriques Prometheus ; ses réponses portent en revanche les durées et
compteurs du serveur (``load_duration``, ``prompt_eval_count``, ``eval_duration``…, en ns). Un callback
LangChain attaché à chaque modèle de chat mesure donc, par appel :

- TTFT (time to first token) côté client, premier token streamé — ``source="client"`` ; à défaut
  (fournisseur sans streaming), chargement + évaluation du prompt côté Ollama — ``source="ollama"`` ;
- latence totale, taille du prompt (caractères envoyés, tokens évalués), tokens générés, débit
  de génération (tokens/s), temps de chargement du modèle (démarrages à froid), erreurs ;
- requêtes en cours et reprises après plantage du runner (``app/ollama_runner.py``).

``kind`` distingue le chat streamé (``chat``) des sorties structurées des agents (``structured``).
Les métriques matérielles (GPU, CPU, RAM, puissance, températures) viennent des exportateurs du
dossier ``monitoring/`` (node_exporter et ``jetson_exporter.py``).
"""

from __future__ import annotations

import threading
import time
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

LABELS = ("provider", "model", "kind")

LLM_REQUESTS = Counter("scouty_llm_requests_total", "Appels LLM terminés", [*LABELS, "status"])
LLM_LATENCY = Histogram("scouty_llm_request_duration_seconds", "Durée totale d'un appel LLM", LABELS,
                        buckets=(0.25, 0.5, 1, 2, 3, 5, 8, 13, 20, 30, 45, 60, 90, 120, 180, 300))
LLM_TTFT = Histogram("scouty_llm_time_to_first_token_seconds", "Délai avant le premier token",
                     [*LABELS, "source"], buckets=(0.1, 0.25, 0.5, 1, 2, 3, 5, 8, 13, 20, 30, 60, 120))
LLM_PROMPT_CHARS = Histogram("scouty_llm_prompt_chars", "Taille du prompt envoyé (caractères)", LABELS,
                             buckets=(250, 500, 1000, 2000, 4000, 8000, 16000, 32000, 64000, 128000))
LLM_PROMPT_TOKENS = Histogram("scouty_llm_prompt_tokens", "Tokens du prompt évalués", LABELS,
                              buckets=(64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384, 32768))
LLM_COMPLETION_TOKENS = Histogram("scouty_llm_completion_tokens", "Tokens générés", LABELS,
                                  buckets=(8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192))
LLM_TOKENS_PER_SECOND = Histogram("scouty_llm_generation_tokens_per_second", "Débit de génération", LABELS,
                                  buckets=(1, 2, 4, 6, 8, 10, 12, 15, 20, 25, 30, 40, 60, 100))
LLM_PROMPT_EVAL_RATE = Histogram("scouty_llm_prompt_eval_tokens_per_second", "Débit d'évaluation du prompt",
                                 LABELS, buckets=(25, 50, 100, 200, 400, 600, 800, 1000, 1500, 2000, 4000))
LLM_LOAD = Histogram("scouty_llm_model_load_seconds", "Chargement du modèle par Ollama (démarrage à froid)",
                     LABELS, buckets=(0.05, 0.1, 0.5, 1, 2, 4, 6, 8, 12, 20, 30, 60))
LLM_IN_FLIGHT = Gauge("scouty_llm_in_flight_requests", "Appels LLM en cours", ["kind"])
LLM_ERRORS = Counter("scouty_llm_errors_total", "Appels LLM en échec, par cause", ["kind", "reason"])
ERROR_REASONS = ("moteur_plante", "delai_depasse", "autre")
LLM_CRASH_RECOVERIES = Counter("scouty_llm_crash_recoveries_total",
                               "Reprises après plantage du runner Ollama (CUDA out of memory…)")
HTTP_REQUESTS = Counter("scouty_http_requests_total", "Requêtes HTTP de l'interface",
                        ["method", "route", "status"])
HTTP_LATENCY = Histogram("scouty_http_request_duration_seconds",
                         "Délai de réponse HTTP (jusqu'aux en-têtes pour les flux SSE)", ["method", "route"],
                         buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30))

NS = 1e9


def _text_length(messages: Any) -> int:
    total = 0
    for batch in messages or []:
        for message in batch:
            content = getattr(message, "content", message)
            if isinstance(content, str):
                total += len(content)
            elif isinstance(content, list):  # blocs multimodaux / Anthropic
                total += sum(len(block.get("text", "")) for block in content if isinstance(block, dict))
    return total


class LLMMetrics(BaseCallbackHandler):
    """Callback LangChain : un appel = une observation par métrique (thread-safe, jamais bloquant)."""

    raise_error = False

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._calls: dict[UUID, dict] = {}
        self._lock = threading.Lock()

    def on_chat_model_start(self, serialized: dict, messages: list, *, run_id: UUID, **kwargs: Any) -> None:
        metadata = kwargs.get("metadata") or {}
        params = kwargs.get("invocation_params") or {}
        call = {
            "start": time.perf_counter(), "first": None,
            "provider": metadata.get("ls_provider") or params.get("_type") or "inconnu",
            "model": metadata.get("ls_model_name") or params.get("model") or params.get("model_name") or "inconnu",
            "chars": _text_length(messages),
        }
        with self._lock:
            self._calls[run_id] = call
        LLM_IN_FLIGHT.labels(self.kind).inc()

    def on_llm_new_token(self, token: str, *, run_id: UUID, **kwargs: Any) -> None:
        call = self._calls.get(run_id)
        if call is not None and call["first"] is None:
            call["first"] = time.perf_counter()

    def _finish(self, run_id: UUID) -> dict | None:
        with self._lock:
            call = self._calls.pop(run_id, None)
        if call is not None:
            LLM_IN_FLIGHT.labels(self.kind).dec()
        return call

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        if call := self._finish(run_id):
            LLM_REQUESTS.labels(call["provider"], call["model"], self.kind, "error").inc()
            LLM_ERRORS.labels(self.kind, error_reason(error)).inc()

    def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs: Any) -> None:
        call = self._finish(run_id)
        if call is None:
            return
        elapsed = time.perf_counter() - call["start"]
        info: dict = {}
        usage: dict = {}
        try:
            generation = response.generations[0][0]
            info = dict(generation.generation_info or {})
            message = getattr(generation, "message", None)
            info |= getattr(message, "response_metadata", None) or {}
            usage = getattr(message, "usage_metadata", None) or {}
        except (AttributeError, IndexError, TypeError):
            pass
        model = info.get("model") or info.get("model_name") or call["model"]
        labels = (call["provider"], model, self.kind)
        LLM_REQUESTS.labels(*labels, "ok").inc()
        LLM_LATENCY.labels(*labels).observe(elapsed)
        if call["chars"]:
            LLM_PROMPT_CHARS.labels(*labels).observe(call["chars"])
        if call["first"] is not None:
            LLM_TTFT.labels(*labels, "client").observe(call["first"] - call["start"])
        elif info.get("prompt_eval_duration") is not None:
            LLM_TTFT.labels(*labels, "ollama").observe(
                (info.get("load_duration") or 0) / NS + info["prompt_eval_duration"] / NS)
        prompt_tokens = info.get("prompt_eval_count") or usage.get("input_tokens")
        completion_tokens = info.get("eval_count") or usage.get("output_tokens")
        if prompt_tokens:
            LLM_PROMPT_TOKENS.labels(*labels).observe(prompt_tokens)
        if completion_tokens:
            LLM_COMPLETION_TOKENS.labels(*labels).observe(completion_tokens)
            if info.get("eval_duration"):
                LLM_TOKENS_PER_SECOND.labels(*labels).observe(completion_tokens / (info["eval_duration"] / NS))
            elif call["first"] is not None and time.perf_counter() > call["first"]:
                LLM_TOKENS_PER_SECOND.labels(*labels).observe(
                    completion_tokens / max(1e-3, call["start"] + elapsed - call["first"]))
        if prompt_tokens and info.get("prompt_eval_duration"):
            LLM_PROMPT_EVAL_RATE.labels(*labels).observe(prompt_tokens / (info["prompt_eval_duration"] / NS))
        if info.get("load_duration") is not None:
            LLM_LOAD.labels(*labels).observe(info["load_duration"] / NS)


def error_reason(error: BaseException) -> str:
    """Cause lisible d'un échec : runner Ollama tué ou planté (mémoire, CUDA), délai dépassé, autre."""
    from app.ollama_runner import is_runner_crash

    if is_runner_crash(error) or "connection refused" in str(error).lower():
        return "moteur_plante"
    if "timeout" in type(error).__name__.lower() or "timed out" in str(error).lower():
        return "delai_depasse"
    return "autre"


CHAT = LLMMetrics("chat")
STRUCTURED = LLMMetrics("structured")


def init_series(provider: str, model: str) -> None:
    """Crée à 0 les séries du modèle configuré, avant le premier appel.

    Sans cela, une série naît au premier appel avec la valeur 1 : Prometheus ne voit aucune
    augmentation (increase/rate = 0), et le premier appel — souvent le seul d'un quart d'heure —
    disparaît des compteurs et des quantiles (TTFT, latence, débit).
    """
    for kind in (CHAT.kind, STRUCTURED.kind):
        labels = (provider, model, kind)
        for status in ("ok", "error"):
            LLM_REQUESTS.labels(*labels, status)
        for source in ("client", "ollama"):
            LLM_TTFT.labels(*labels, source)
        for histogram in (LLM_LATENCY, LLM_PROMPT_CHARS, LLM_PROMPT_TOKENS, LLM_COMPLETION_TOKENS,
                          LLM_TOKENS_PER_SECOND, LLM_PROMPT_EVAL_RATE, LLM_LOAD):
            histogram.labels(*labels)
        LLM_IN_FLIGHT.labels(kind)
        for reason in ERROR_REASONS:
            LLM_ERRORS.labels(kind, reason)


def render() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
