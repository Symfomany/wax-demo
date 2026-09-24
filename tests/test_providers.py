"""Fournisseurs LLM : compatible OpenAI (Ollama /v1, vLLM, couche OpenAI de Claude) et API Claude native.

Chaque test lance une fausse API HTTP locale et vérifie la requête réellement envoyée.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.config import settings
from app.llm import ANTHROPIC_FALLBACK_BETA, StructuredLLM, anthropic_invoke, openai_invoke
from app.schemas import ScoutOutput

VALID = {"picks": [{"doc_id": 1, "relevance": 9, "novelty": 5, "confidence": 8, "why_it_matters": "x"}]}


def fake_server(reply):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append({"path": self.path, "body": body, "headers": dict(self.headers)})
            payload = json.dumps(reply(body)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, calls


@pytest.fixture
def openai_server(monkeypatch):
    server, calls = fake_server(lambda body: {
        "id": "c1", "object": "chat.completion", "created": 0, "model": body["model"],
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": json.dumps(VALID)}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    })
    monkeypatch.setattr(settings, "llm_base_url", f"http://127.0.0.1:{server.server_port}/v1")
    monkeypatch.setattr(settings, "llm_model", "claude-opus-5")
    yield calls
    server.shutdown()


def test_openai_compatible_provider_sends_schema_twice(openai_server):
    result = StructuredLLM(openai_invoke(), model="t").generate("Choisis.", ScoutOutput)

    assert result.picks[0].relevance == 9
    request = openai_server[0]
    assert request["path"] == "/v1/chat/completions"
    assert request["body"]["model"] == "claude-opus-5"
    # response_format (Ollama, vLLM) ET schéma dans le prompt (ignoré par la couche OpenAI de Claude)
    assert request["body"]["response_format"]["type"] == "json_schema"
    assert '"doc_id"' in request["body"]["messages"][0]["content"]
    assert "temperature" not in request["body"]  # modèle Claude : pas de température


def test_openai_compatible_provider_keeps_temperature_for_other_models(openai_server, monkeypatch):
    monkeypatch.setattr(settings, "llm_model", "gemma3:4b")
    StructuredLLM(openai_invoke(), model="t").generate("Choisis.", ScoutOutput)
    assert openai_server[0]["body"]["temperature"] == settings.llm_temperature


@pytest.fixture
def anthropic_server(monkeypatch):
    def reply(body):
        return {
            "id": "msg_1", "type": "message", "role": "assistant", "model": body["model"],
            "content": [{"type": "text", "text": json.dumps(VALID)}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }

    server, calls = fake_server(reply)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test")
    monkeypatch.setattr(settings, "llm_model", "claude-opus-5")
    yield calls
    server.shutdown()


def test_anthropic_provider_uses_native_structured_outputs(anthropic_server):
    result = StructuredLLM(anthropic_invoke(), model="t").generate("Choisis.", ScoutOutput)

    assert result.picks[0].doc_id == 1
    request = anthropic_server[0]
    assert request["path"].startswith("/v1/messages")
    body = request["body"]
    assert body["model"] == "claude-opus-5"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert "temperature" not in body  # refusée par les modèles Claude récents
    assert body["fallbacks"] == "default"
    assert ANTHROPIC_FALLBACK_BETA in request["headers"].get("anthropic-beta", "")
