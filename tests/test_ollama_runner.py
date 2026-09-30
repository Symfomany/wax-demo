"""Plantages du runner Ollama (CUDA) : détection, reprise automatique, redémarrage à la demande."""

import json
from types import SimpleNamespace

import httpx
import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from app import ollama_runner
from app.chat.agent import ChatContext, build_chat_graph, stream_chat
from app.chat.tools import ChatServices
from app.config import settings
from app.ollama_runner import call_with_recovery, is_runner_crash, restart_runner, unload_all
from tests.test_chat import router

CUDA_OOM = ("llama runner process has terminated: CUDA error: out of memory\n"
            "  current device: 0, in function ggml_backend_cuda_buffer_set_tensor")


class ResponseError(Exception):
    """Même forme que ollama.ResponseError (message + status_code)."""

    def __init__(self, error: str, status_code: int = 500):
        super().__init__(f"{error} (status code: {status_code})")
        self.status_code = status_code


@pytest.mark.parametrize(("error", "crash"), [
    (ResponseError(CUDA_OOM), True),
    (ResponseError("llama runner process has terminated: CUDA error"), True),
    (ResponseError("model runner has unexpectedly stopped"), True),
    (httpx.RemoteProtocolError("Server disconnected without sending a response."), True),  # OOM killer
    (ResponseError("llama runner process has terminated: signal: killed"), True),
    (ResponseError("model 'x' not found", 404), False),
    (ResponseError("CUDA error", 400), False),  # erreur de requête : redémarrer n'y change rien
    (ValueError("Sortie invalide : champ manquant"), False),
])
def test_only_runner_crashes_are_recognized(error, crash):
    assert is_runner_crash(error) is crash


def test_call_is_retried_after_freeing_the_gpu():
    calls, recovered, notified = [], [], []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise ResponseError(CUDA_OOM)
        return "ok"

    result = call_with_recovery(flaky, retries=2, on_retry=lambda e, n: notified.append(n),
                                recover_fn=lambda: recovered.append(1))
    assert result == "ok" and len(calls) == 3 and len(recovered) == 2 and notified == [1, 2]


def test_retries_are_bounded_and_other_errors_pass_through(monkeypatch):
    def crash():
        raise ResponseError(CUDA_OOM)

    with pytest.raises(ResponseError):
        call_with_recovery(crash, retries=1, recover_fn=lambda: None)
    attempts = []
    with pytest.raises(ValueError):
        call_with_recovery(lambda: attempts.append(1) or (_ for _ in ()).throw(ValueError("schéma")),
                           recover_fn=lambda: pytest.fail("pas de reprise pour une erreur de schéma"))
    assert attempts == [1]
    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    with pytest.raises(ResponseError):  # la reprise ne concerne qu'Ollama
        call_with_recovery(crash, recover_fn=lambda: pytest.fail("pas de reprise hors Ollama"))


def ollama_api(requests: list, loaded=("ministral-3:3b",), load_status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        requests.append((request.method, request.url.path, body))
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": [{"name": n, "size_vram": 3 * 2**30} for n in loaded]})
        if request.url.path == "/api/generate" and body.get("keep_alive") != 0 and load_status >= 400:
            return httpx.Response(load_status, json={"error": CUDA_OOM})
        return httpx.Response(200, json={"version": "0.13.5"} if request.url.path == "/api/version" else {})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_unload_all_asks_ollama_to_stop_every_runner():
    requests = []
    assert unload_all(ollama_api(requests, loaded=("a", "b"))) == ["a", "b"]
    assert [body for method, path, body in requests if path == "/api/generate"] == [
        {"model": "a", "keep_alive": 0}, {"model": "b", "keep_alive": 0}]


def test_restart_on_demand_unloads_restarts_the_service_and_reloads(monkeypatch):
    monkeypatch.setattr(settings, "llm_model", "ministral-3:3b")
    monkeypatch.setattr(settings, "ollama_restart_command", "sudo -n systemctl restart ollama")
    requests, commands = [], []

    def run(argv, **kwargs):
        commands.append(argv)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    report = restart_runner(ollama_api(requests), run=run, sleep=lambda s: None)

    assert commands == [["sudo", "-n", "systemctl", "restart", "ollama"]]  # sans shell
    assert report["unloaded"] == ["ministral-3:3b"] and report["service_restarted"] is True
    assert report["loaded"] is True and report["vram_mb"] == 3072
    assert {"model": "ministral-3:3b", "prompt": "", "keep_alive": "10m"} in [b for _, _, b in requests]


def test_restart_reports_a_model_that_still_does_not_fit(monkeypatch):
    monkeypatch.setattr(settings, "ollama_restart_command", None)
    report = restart_runner(ollama_api([], load_status=500), run=lambda *a, **k: pytest.fail("aucune commande"),
                            sleep=lambda s: None)
    assert report["loaded"] is False and "out of memory" in report["load_error"]
    assert report["service_restarted"] is False


class CrashOnceModel(GenericFakeChatModel):
    crashes: int = 1

    def _generate(self, messages, *args, **kwargs):
        if self.crashes:
            self.crashes -= 1
            raise ResponseError(CUDA_OOM)
        return super()._generate(messages, *args, **kwargs)


def test_chat_answer_survives_a_cuda_crash(connection, tmp_path, monkeypatch):
    recovered = []
    monkeypatch.setattr(ollama_runner, "recover", lambda: recovered.append(1))
    services = ChatServices(connection=connection, store=InMemoryStore(), reports_dir=tmp_path)
    model = CrashOnceModel(messages=iter([AIMessage("Réponse après reprise.")]))
    graph = build_chat_graph(ChatContext(services=services, router_llm=router(), chat_model=model), InMemorySaver())

    events = list(stream_chat(graph, "c1", "Parle-moi de quantization", {}))

    assert [e for e in events if e["type"] == "llm_recovering"][0]["attempt"] == 1
    assert events[-1]["text"] == "Réponse après reprise." and recovered == [1]
