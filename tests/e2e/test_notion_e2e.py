"""E2E Notion : vrai serveur MCP notion-veille (stdio) ↔ langchain-mcp-adapters ↔ fausse API Notion."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itertools import count

import pytest
from typer.testing import CliRunner

from app import storage
from app.config import settings
from app.main import cli
from app.notion import sync_notion
from tests.conftest import NOW

PARENT = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"


class FakeNotion(BaseHTTPRequestHandler):
    calls: list[dict] = []
    ids = count(1)

    def log_message(self, *args):
        pass

    def _reply(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle(self, method):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        FakeNotion.calls.append({
            "method": method, "path": self.path, "body": body,
            "auth": self.headers.get("Authorization"), "version": self.headers.get("Notion-Version"),
        })
        if self.headers.get("Authorization") != "Bearer ntn_test":
            return self._reply({"message": "unauthorized"}, status=401)
        if method == "POST" and self.path == "/v1/pages":
            if len(body["children"]) > 100:
                return self._reply({"message": "children > 100"}, status=400)
            page_id = f"page-{next(FakeNotion.ids)}"
            return self._reply({"id": page_id, "url": f"https://www.notion.so/{page_id}"})
        if method == "PATCH" and self.path.startswith("/v1/blocks/"):
            return self._reply({"results": body["children"]})
        if method == "PATCH" and self.path.startswith("/v1/pages/"):
            return self._reply({"id": self.path.rsplit("/", 1)[1], "archived": body.get("archived")})
        return self._reply({"message": "not found"}, status=404)

    def do_POST(self):
        self._handle("POST")

    def do_PATCH(self):
        self._handle("PATCH")


@pytest.fixture
def fake_notion():
    FakeNotion.calls = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeNotion)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def seed_digests(connection, tmp_path, number: int) -> None:
    for day in range(1, number + 1):
        item = {"title": f"Signal {day}", "source": "rss", "url": f"https://example.org/{day}",
                "date": None, "summary": "s", "why_it_matters": "w", "tags": ["rss", "feed", "rag"]}
        digest = {"generated_at": f"2026-09-{day:02d}T08:00:00Z", "executive_summary": f"Résumé {day}",
                  "items": [item] * 12, "rejected_count": 0, "period_label": "x"}
        storage.record_digest(connection, f"run-{day}", digest, tmp_path / "d.md", tmp_path / "d.json")


def test_sync_publishes_ten_latest_digests_and_archives_previous_page(connection, tmp_path, fake_notion):
    seed_digests(connection, tmp_path, 12)
    audit: list[str] = []

    first = sync_notion(connection, "ntn_test", PARENT, api_url=fake_notion, audit_log=audit, now=NOW)

    create = FakeNotion.calls[0]
    assert create["path"] == "/v1/pages" and create["version"] == "2022-06-28"
    assert create["body"]["parent"] == {"page_id": PARENT}
    title = create["body"]["properties"]["title"]["title"][0]["text"]["content"]
    assert title == "Veille GenAI — 10 dernières veilles (24/09/2026)"
    headings = [b for b in create["body"]["children"] if b["type"] == "heading_2"]
    assert headings[0]["heading_2"]["rich_text"][0]["text"]["content"].startswith("🛰️ Veille du 12/09/2026")
    # 2 + 10 × (6 + 12 ressources) blocs → 1 création + 1 ajout par tranche de 100
    appended = [c for c in FakeNotion.calls if c["method"] == "PATCH" and "/children" in c["path"]]
    assert first["blocks"] == 2 + 10 * 18 and len(appended) == 1
    assert first["digests"] == 10 and first["archived"] is None
    assert audit[0].startswith("notion.create_page({'parent_page_id'") and "<182 éléments>" in audit[0]

    second = sync_notion(connection, "ntn_test", PARENT, api_url=fake_notion, now=NOW)

    assert second["archived"] == first["page_id"]
    assert FakeNotion.calls[-1]["body"] == {"archived": True}
    assert storage.current_notion_page(connection)["page_id"] == second["page_id"]


def test_sync_without_digest_is_refused(connection, fake_notion):
    with pytest.raises(RuntimeError, match="Aucune veille publiée"):
        sync_notion(connection, "ntn_test", PARENT, api_url=fake_notion)
    assert FakeNotion.calls == []


def test_notion_errors_are_reported(connection, tmp_path, fake_notion):
    seed_digests(connection, tmp_path, 1)
    with pytest.raises(Exception, match="401"):
        sync_notion(connection, "mauvais-jeton", PARENT, api_url=fake_notion)


def test_cli_notion_sync(isolated_settings, fake_notion, monkeypatch, tmp_path):
    connection = storage.connect(settings.database_path)
    seed_digests(connection, tmp_path, 2)
    for name, value in {"notion_token": "ntn_test", "notion_parent_page_id": PARENT,
                        "notion_api_url": fake_notion}.items():
        monkeypatch.setattr(settings, name, value)

    result = CliRunner().invoke(cli, ["notion-sync"], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    assert "https://www.notion.so/page-" in result.output
    assert "2 veille(s)" in result.output
