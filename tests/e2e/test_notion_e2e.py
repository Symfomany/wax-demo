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
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        FakeNotion.calls.append({
            "method": method, "path": self.path, "body": body,
            "auth": self.headers.get("Authorization"), "version": self.headers.get("Notion-Version"),
        })
        if self.headers.get("Authorization") != "Bearer ntn_test":
            return self._reply({"message": "unauthorized"}, status=401)
        if method == "GET" and self.path == "/v1/users/me":
            return self._reply({"object": "user", "name": "harness-test", "bot": {"workspace_name": "Test"}})
        if method == "GET" and self.path == f"/v1/pages/{PARENT}":
            return self._reply({"object": "page", "id": PARENT, "archived": False})
        if method == "POST" and self.path == "/v1/pages" and body["parent"]["page_id"] != PARENT:
            return self._reply({"object": "error", "status": 404, "code": "object_not_found",
                                "message": "Could not find page with ID"}, status=404)
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

    def do_GET(self):
        self._handle("GET")

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
    children = create["body"]["children"]
    assert children[3]["type"] == "heading_1"
    assert children[3]["heading_1"]["rich_text"][0]["text"]["content"].startswith("🛰️ Veille du 12/09/2026")
    assert sum(bool(b.get("heading_2", {}).get("is_toggleable")) for b in children) == 9
    assert "cover" not in create["body"]  # aucune image fournie
    # 3 (en-tête) + dernière veille 5 + 12 × 4 + 1 + « précédentes » 1 + 9 sections repliées = 67 blocs
    assert first["blocks"] == 67 and not any(c["method"] == "PATCH" and "/children" in c["path"] for c in FakeNotion.calls)
    assert first["digests"] == 10 and first["archived"] is None
    assert audit[0].startswith("notion.create_page({'parent_page_id'") and "<67 éléments>" in audit[0]

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


def test_unshared_parent_page_gives_an_actionable_error(connection, tmp_path, fake_notion):
    seed_digests(connection, tmp_path, 1)
    with pytest.raises(RuntimeError) as error:
        sync_notion(connection, "ntn_test", "3e5cd9c3-8df7-80d6-b4ad-c76d8ad72a99", api_url=fake_notion)
    message = str(error.value)
    assert "Réponse MCP illisible" not in message
    assert "object_not_found" in message and "Connexions" in message


def test_access_check_explains_what_is_wrong(fake_notion):
    from app.notion import check_access

    assert check_access("ntn_test", PARENT, fake_notion) == (
        True, "intégration « harness-test » (Test), page parente accessible")
    ok, detail = check_access("ntn_test", "3e5cd9c3-8df7-80d6-b4ad-c76d8ad72a99", fake_notion)
    assert not ok and "Connexions" in detail and "harness-test" in detail
    ok, detail = check_access("ntn_bad", PARENT, fake_notion)
    assert not ok and "NOTION_TOKEN" in detail
    assert not any(call["method"] in {"POST", "PATCH"} for call in FakeNotion.calls)  # diagnostic en lecture seule


def test_sync_illustrates_the_latest_digest_with_page_images(connection, tmp_path, fake_notion):
    seed_digests(connection, tmp_path, 2)
    seen = []

    def images(urls):
        seen.extend(urls)
        return {urls[0]: "https://img.example.org/cover.png"}

    sync_notion(connection, "ntn_test", PARENT, api_url=fake_notion, now=NOW, image_lookup=images)

    body = FakeNotion.calls[0]["body"]
    assert set(seen) == {"https://example.org/2"}  # seules les ressources de la dernière veille
    assert body["cover"] == {"type": "external", "external": {"url": "https://img.example.org/cover.png"}}
    assert any(b["type"] == "image" for b in body["children"])


REVIEW = {
    "id": "rev-1", "url": "https://example.org/a", "title": "Qwen4 sort en open weights", "conversation_id": "c-1",
    "revision": 1, "model": "fake", "warnings": [],
    "page": {"final_url": "https://example.org/a", "site": "example.org", "published_at": "2026-09-20", "domains": ["LLM"]},
    "analysis": {"summary": "Synthèse.", "why_it_matters": "Licence Apache.", "relevance": 8, "novelty": 7, "confidence": 9,
                 "source_type": "primaire", "key_points": ["Point 1"], "risks": ["Risque 1"],
                 "claims": [{"claim": "Poids ouverts", "quote": "open weights", "status": "etaye", "kind": "fait"},
                            {"claim": "Inventé", "quote": "", "status": "non_etaye", "kind": "fait"}]},
}


def test_review_is_appended_to_the_current_page_then_kept_on_rebuild(connection, tmp_path, fake_notion):
    from app.notion import publish_review

    storage.save_review(connection, REVIEW)
    with pytest.raises(RuntimeError, match="Aucune page Notion"):
        publish_review(connection, "rev-1", "ntn_test", PARENT, api_url=fake_notion)

    seed_digests(connection, tmp_path, 1)
    page = sync_notion(connection, "ntn_test", PARENT, api_url=fake_notion, now=NOW)
    audit: list[str] = []
    result = publish_review(connection, "rev-1", "ntn_test", PARENT, api_url=fake_notion, audit_log=audit, now=NOW)

    append = FakeNotion.calls[-1]
    assert append["method"] == "PATCH" and append["path"] == f"/v1/blocks/{page['page_id']}/children"
    toggle = append["body"]["children"][0]
    assert toggle["type"] == "heading_3" and toggle["heading_3"]["is_toggleable"]
    assert toggle["heading_3"]["rich_text"][1]["text"]["link"] == {"url": "https://example.org/a"}
    texts = json.dumps(toggle["heading_3"]["children"], ensure_ascii=False)
    assert "Synthèse." in texts and "« open weights »" in texts and "Inventé" not in texts  # affirmations étayées seulement
    assert result == {"url": page["url"], "blocks": 1, "title": "Qwen4 sort en open weights"}
    assert audit[0].startswith("notion.append_blocks(")
    assert storage.get_review(connection, "rev-1")["notion"]["page_url"] == page["url"]

    sync_notion(connection, "ntn_test", PARENT, api_url=fake_notion, now=NOW)  # reconstruction : la review reste
    rebuilt = [c for c in FakeNotion.calls if c["method"] == "POST" and c["path"] == "/v1/pages"][-1]["body"]["children"]
    headings = [b["heading_1"]["rich_text"][0]["text"]["content"] for b in rebuilt if b["type"] == "heading_1"]
    assert "🔬 Reviews d'actualités (1)" in headings
