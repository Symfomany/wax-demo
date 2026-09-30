"""Plantages du runner Ollama (CUDA) : détection, reprise automatique et redémarrage à la demande.

Sur la Jetson Orin (8 Go partagés CPU/GPU), le chargement d'un modèle échoue parfois avec
« llama runner process has terminated: CUDA error: out of memory » : la mémoire unifiée est
momentanément prise par le navigateur, le serveur web ou un autre modèle. Ollama renvoie alors
un HTTP 500 et le runner est mort, mais le service reste debout.

- `is_runner_crash` reconnaît ces erreurs (et seulement elles : une erreur de schéma ou un
  modèle absent ne se règle pas en redémarrant) ;
- `recover` décharge tous les modèles (`keep_alive: 0` → Ollama tue les runners et libère la
  mémoire GPU), attend un peu, puis laisse l'appelant réessayer ;
- `call_with_recovery` enveloppe un appel LLM : reprise automatique, `LLM_CRASH_RETRIES` fois ;
- `restart_runner` sert le bouton « Redémarrer le moteur LLM » : déchargement, redémarrage
  optionnel du service (`OLLAMA_RESTART_COMMAND`, ex. `sudo -n systemctl restart ollama`),
  puis rechargement du modèle pour vérifier qu'il tient en mémoire.
"""

from __future__ import annotations

import logging
import re
import shlex
import subprocess
import threading
import time
from collections.abc import Callable
from typing import TypeVar

import httpx

from app.config import settings

log = logging.getLogger(__name__)
T = TypeVar("T")

CRASH = re.compile(
    r"cuda error|runner process has terminated|llama runner|model runner has unexpectedly stopped|"
    r"out of memory|cudamalloc|ggml_cuda|unable to allocate cuda|gpu has fallen off|exit status 2|"
    # Ollama tué par le noyau (OOM killer) en pleine réponse, ou pas encore relancé par systemd
    r"server disconnected without sending a response|peer closed connection|connection reset by peer|"
    r"signal: killed",
    re.I,
)
_lock = threading.Lock()  # une seule reprise à la fois (chat, Scouts parallèles, review…)


def is_runner_crash(error: BaseException) -> bool:
    """Runner Ollama mort ou GPU saturé ; jamais une erreur de requête (400, modèle inconnu…)."""
    status = getattr(error, "status_code", None)
    if status is not None and status < 500:
        return False
    return bool(CRASH.search(str(error)))


def loaded_models(client: httpx.Client | None = None) -> list[dict]:
    client = client or httpx.Client(timeout=5)
    return client.get(f"{settings.ollama_url}/api/ps").json().get("models", [])


def unload_all(client: httpx.Client | None = None) -> list[str]:
    """Décharge chaque modèle chargé : Ollama arrête son runner et rend la mémoire GPU."""
    client = client or httpx.Client(timeout=30)
    names = [model["name"] for model in loaded_models(client)]
    for name in names:
        client.post(f"{settings.ollama_url}/api/generate", json={"model": name, "keep_alive": 0})
    return names


def recover(client: httpx.Client | None = None, sleep: Callable[[float], None] = time.sleep,
            delay: float | None = None) -> list[str]:
    """Libère le GPU après un plantage ; ne lève jamais (Ollama peut être lui-même en train de redémarrer)."""
    with _lock:
        try:
            unloaded = unload_all(client)
        except (httpx.HTTPError, ValueError) as error:
            log.warning("Ollama injoignable pendant la reprise : %s", error)
            unloaded = []
        sleep(settings.llm_crash_delay if delay is None else delay)
        return unloaded


def call_with_recovery(call: Callable[[], T], retries: int | None = None,
                       on_retry: Callable[[BaseException, int], None] | None = None,
                       recover_fn: Callable[[], object] | None = None) -> T:
    """Appel LLM avec reprise après un plantage du runner (Ollama uniquement)."""
    retries = settings.llm_crash_retries if retries is None else retries
    attempt = 0
    while True:
        try:
            return call()
        except Exception as error:  # noqa: BLE001 — seuls les plantages du runner sont repris
            if settings.llm_provider != "ollama" or attempt >= retries or not is_runner_crash(error):
                raise
            attempt += 1
            from app.metrics import LLM_CRASH_RECOVERIES

            LLM_CRASH_RECOVERIES.inc()
            log.warning("Runner Ollama planté (%s) : reprise %d/%d", str(error)[:200], attempt, retries)
            if on_retry:
                on_retry(error, attempt)
            (recover_fn or recover)()


def restart_runner(client: httpx.Client | None = None, run: Callable[..., object] = subprocess.run,
                   sleep: Callable[[float], None] = time.sleep) -> dict:
    """Redémarrage à la demande : décharge, redémarre le service si configuré, recharge le modèle."""
    client = client or httpx.Client(timeout=max(60, settings.llm_timeout))
    started = time.perf_counter()
    report: dict = {"model": settings.llm_model, "unloaded": [], "service_restarted": False, "loaded": False}
    with _lock:
        try:
            report["unloaded"] = unload_all(client)
        except (httpx.HTTPError, ValueError) as error:
            report["unload_error"] = str(error)
        if settings.ollama_restart_command:
            # Commande de .env uniquement (jamais saisie dans l'interface), sans shell.
            result = run(shlex.split(settings.ollama_restart_command), capture_output=True, text=True, timeout=90)
            report["service_restarted"] = result.returncode == 0
            if result.returncode:
                report["service_error"] = (result.stderr or result.stdout or "").strip()[:300]
            for _ in range(30):  # le service revient en quelques secondes
                try:
                    client.get(f"{settings.ollama_url}/api/version")
                    break
                except httpx.HTTPError:
                    sleep(1)
        sleep(1)
        try:
            response = client.post(f"{settings.ollama_url}/api/generate",
                                   json={"model": settings.llm_model, "prompt": "", "keep_alive": "10m"})
            if response.status_code >= 400:
                report["load_error"] = response.json().get("error", response.text)[:400]
            else:
                report["loaded"] = True
                running = {m["name"]: m for m in loaded_models(client)}
                model = running.get(settings.llm_model) or running.get(f"{settings.llm_model}:latest") or {}
                report["vram_mb"] = round(model.get("size_vram", 0) / 2**20)
        except (httpx.HTTPError, ValueError) as error:
            report["load_error"] = str(error)
    report["ms"] = round((time.perf_counter() - started) * 1000)
    return report
