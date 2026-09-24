"""Commandes CLI ajoutées pour les scripts : run --run-id-file, pending, grill, grill-save."""

import json

import pytest
from typer.testing import CliRunner

from app import storage
from app.config import settings
from app.main import cli, parse_grill_reply

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for name, value in {"database_path": tmp_path / "watch.db", "memory_db_path": tmp_path / "memory.db",
                        "claude_memory_path": tmp_path / "veille.md"}.items():
        monkeypatch.setattr(settings, name, value)


@pytest.mark.parametrize(("raw", "expected"), [
    ("", {"recommended": True}),
    ("-", {"options": [], "text": ""}),
    ("1,3", {"options": ["a", "c"], "text": ""}),
    ("2 / surtout Jetson", {"options": ["b"], "text": "surtout Jetson"}),
    ("vLLM, Gemma", {"options": [], "text": "vLLM, Gemma"}),
    ("9", {"options": [], "text": ""}),
])
def test_terminal_grill_reply_parsing(raw, expected):
    assert parse_grill_reply(raw, ["a", "b", "c"]) == expected


def test_grill_save_validates_and_stores_the_profile():
    profile = {"domains": ["robotics"], "priorities": ["robotics"], "keywords": ["vla", "ros"],
               "exclusions": ["tutorial"], "summary": "Tu suis la robotique VLA."}

    result = runner.invoke(cli, ["grill-save", json.dumps(profile)], catch_exceptions=False)

    assert result.exit_code == 0 and "Profil enregistré" in result.output
    assert "Tu suis la robotique VLA." in settings.claude_memory_path.read_text()
    assert runner.invoke(cli, ["grill-save", '{"domains": "pas une liste"}']).exit_code != 0


def test_pending_lists_runs_awaiting_approval():
    connection = storage.connect(settings.database_path)
    storage.set_run_status(connection, "run-a", "awaiting_approval")
    storage.set_run_status(connection, "run-b", "published")

    output = runner.invoke(cli, ["pending"]).output

    assert "run-a" in output and "run-b" not in output


def test_knowledge_command_searches_indexes_and_adds(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "knowledge_uploads_dir", tmp_path / "knowledge")
    result = runner.invoke(cli, ["knowledge", "c'est quoi le KV cache"], catch_exceptions=False)
    assert result.exit_code == 0 and result.output.startswith("KV cache")

    note = tmp_path / "Notes.md"
    note.write_text("## SLM\nDomaine : LLM\n\nPetit modèle de langage.", encoding="utf-8")
    result = runner.invoke(cli, ["knowledge", "--add", str(note)], catch_exceptions=False)
    assert result.exit_code == 0 and "notes.md : 1 entrée(s)" in result.output
    assert (tmp_path / "knowledge" / "notes.md").exists()

    note.write_text("## X\nSource : pas-une-url\n\nTexte", encoding="utf-8")
    assert runner.invoke(cli, ["knowledge", "--add", str(note), "--replace"]).exit_code == 1


def test_review_command_fetches_a_real_page_and_prints_a_sourced_review(monkeypatch):
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from app.llm import StructuredLLM
    from tests.conftest import ARTICLE_HTML, fake_review_llm

    class Article(BaseHTTPRequestHandler):
        def do_GET(self):
            body = ARTICLE_HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Article)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/vllm-fp8"
    monkeypatch.setattr("app.llm.get_llm", lambda connection: StructuredLLM(fake_review_llm(), model="fake"))
    try:
        assert "Adresse non publique refusée" in runner.invoke(cli, ["review", url]).output  # SSRF : refus par défaut
        monkeypatch.setattr(settings, "review_allow_private", True)
        text = runner.invoke(cli, ["review", url], catch_exceptions=False).output
        result = runner.invoke(cli, ["review", url, "--json"], catch_exceptions=False)
    finally:
        server.shutdown()

    assert "vLLM 0.9 adds an FP8 KV cache" in text and "non étayée" in text
    record = json.loads(result.output[result.output.index("\n{") + 1:])
    assert record["page"]["site"] == "vLLM Blog" and record["page"]["published_at"] == "2026-09-20"
    assert storage.get_review(storage.connect(settings.database_path), record["id"]) is not None
