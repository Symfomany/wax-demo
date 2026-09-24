"""Publication : rapport daté, mots-clés, blocs Notion, outils de publication, guard Notion."""

import re

import pytest

from app.config import settings
from app.harness.guards import GuardViolation, notion_policy
from app.keywords import extract_keywords
from app.notion import digest_blocks, page_blocks
from app.publishers import Publication, default_publishers, run_publishers
from app.reports import french_date, list_reports, render_report, report_path
from tests.conftest import NOW

DIGEST = {
    "generated_at": "2026-09-24T10:12:38Z",
    "period_label": "Veille du 24/09/2026",
    "executive_summary": "Semaine centrée sur l'inférence locale.",
    "rejected_count": 1,
    "items": [
        {"title": "vllm-project/vllm: v0.30.0", "source": "github", "url": "https://github.com/vllm-project/vllm/releases/tag/v0.30.0",
         "date": "2026-09-22T08:00:00Z", "summary": "FP8 sur Ampere.", "why_it_matters": "Tester FP8.",
         "tags": ["github", "vllm-project/vllm", "quantization", "gpu"]},
        {"title": "acme/fast-infer : nouveau dépôt open source (420★)", "source": "github",
         "url": "https://github.com/acme/fast-infer", "date": None, "summary": "Serveur d'inférence.",
         "why_it_matters": "À surveiller.", "tags": ["github-mcp", "Python", "inference", "gpu"]},
    ],
}
CRITIQUES = [{"signal_url": "https://arxiv.org/abs/1", "verdict": "drop", "rationale": "Hors sujet.", "factual_risks": []}]


def render(fmt="md", **overrides):
    args = dict(digest=DIGEST, critiques=CRITIQUES, run_id="run-1", model="gemma-3-4b-it",
                collected={"rss": 3, "github_mcp": 2}, trace=["fan-out : collect:rss"], errors=[])
    return render_report(settings.templates_dir, fmt, **(args | overrides))


def test_report_has_front_matter_sections_and_sources():
    report = render()

    front_matter = report.split("---\n")[1]
    assert 'title: "Veille LLM / GenAI — 24/09/2026"' in front_matter
    assert "keywords: [gpu, quantization, inference, python]" in front_matter
    for heading in ["## 🔝 À retenir", "## 📰 Signaux", "## 🗂️ Ressources", "## 🚫 Écartés par le Critic", "## 🧭 Méthodologie"]:
        assert heading in report
    assert "**2 signal(aux) retenu(s)** · 1 écarté(s) · 5 document(s) collecté(s) · modèle `gemma-3-4b-it`" in report
    assert "_Jeudi 24 septembre 2026_" in report
    assert "✨ Nouveau dépôt GitHub · non précisée · [Lire la source ↗](https://github.com/acme/fast-infer)" in report
    assert all(item["url"] in report for item in DIGEST["items"])
    # tags d'affichage filtrés
    assert "🏷️ `quantization` `gpu`" in report and "`vllm-project/vllm`" not in report


def test_report_markdown_structure_is_valid():
    lines = render().splitlines()
    for index, line in enumerate(lines):
        if line.startswith("#") and index > 0:
            assert lines[index - 1] == "", f"titre sans ligne vide avant : {line}"
        if line == "---" and index > 12:
            # pas de titre setext accidentel : la ligne précédente doit être vide
            assert lines[index - 1] == "", "ligne collée à un séparateur ---"


def test_report_without_items_or_collection():
    report = render(digest=DIGEST | {"items": []}, critiques=[], collected={})
    assert "_Aucun signal retenu sur la période._" in report
    assert "mémoire existante (pas de collecte)" in report
    assert "## 🚫 Écartés" not in report


def test_report_paths_are_dated(tmp_path):
    path = report_path(tmp_path, "2026-09-24T10:12:38Z")
    assert path == tmp_path / "2026" / "veille-2026-09-24-1012.md"
    path.parent.mkdir(parents=True)
    path.write_text("x")
    assert list_reports(tmp_path) == [path]
    assert french_date(NOW) == "jeudi 24 septembre 2026"


def test_keywords_skip_source_tags_repos_and_stopwords():
    assert extract_keywords(DIGEST)[:3] == ["gpu", "quantization", "inference"]
    assert "github" not in extract_keywords(DIGEST)
    assert "vllm-project/vllm" not in extract_keywords(DIGEST)


# --- Notion ------------------------------------------------------------------------


def test_notion_blocks_contain_title_summary_description_resources_keywords():
    blocks = digest_blocks({"id": 1, "digest": DIGEST})
    types = [block["type"] for block in blocks]

    assert types == ["heading_2", "callout", "paragraph", "heading_3",
                     "bulleted_list_item", "bulleted_list_item", "paragraph", "divider"]
    text = lambda block: "".join(t["text"]["content"] for t in block[block["type"]]["rich_text"])
    assert text(blocks[0]).startswith("🛰️ Veille du 24/09/2026 — vllm-project/vllm")
    assert text(blocks[1]) == DIGEST["executive_summary"]
    assert "2 signal(aux) retenu(s), 1 écarté(s)" in text(blocks[2])
    link = blocks[4]["bulleted_list_item"]["rich_text"][0]
    assert link["text"]["link"]["url"] == DIGEST["items"][0]["url"]
    assert text(blocks[6]).startswith("Mots-clés : gpu")


def test_notion_page_has_intro_and_table_of_contents_and_respects_text_limit():
    long_digest = DIGEST | {"executive_summary": "x" * 5000}
    blocks = page_blocks([{"id": 1, "digest": long_digest}] * 10, now=NOW)

    assert [b["type"] for b in blocks[:2]] == ["callout", "table_of_contents"]
    assert sum(b["type"] == "heading_2" for b in blocks) == 10
    for block in blocks:
        for segment in block.get(block["type"], {}).get("rich_text", []):
            assert len(segment["text"]["content"]) <= 2000


def test_notion_policy_restricts_writes():
    owned = {"11111111-2222"}
    check = notion_policy("ABCD-ef01", owned)

    assert check("create_page", {"parent_page_id": "abcdef01", "title": "t", "children": []})
    with pytest.raises(GuardViolation, match="hors de la page parente"):
        check("create_page", {"parent_page_id": "autre", "title": "t", "children": []})
    assert check("archive_page", {"page_id": "111111112222"})  # tirets ignorés
    with pytest.raises(GuardViolation, match="non gérée"):
        check("archive_page", {"page_id": "page-d-un-collegue"})
    with pytest.raises(GuardViolation, match="non autorisé"):
        check("delete_database", {})


# --- Outils de publication ---------------------------------------------------------


def publication(connection, tmp_path) -> Publication:
    return Publication(
        run_id="run-1", digest=DIGEST, critiques=CRITIQUES, connection=connection,
        output_dir=tmp_path / "output", reports_dir=tmp_path / "reports",
        templates_dir=settings.templates_dir, model="fake",
    )


def test_publishers_write_files_report_and_call_notion(connection, tmp_path):
    synced = []

    def fake_sync(conn):
        synced.append(conn)
        return {"url": "https://www.notion.so/veille-123"}

    outputs, errors = run_publishers(publication(connection, tmp_path), default_publishers(fake_sync))

    assert errors == []
    assert outputs["notion"] == "https://www.notion.so/veille-123"
    assert re.search(r"reports/2026/veille-2026-09-24-1012\.md$", outputs["report"])
    assert outputs["json"].endswith("digest-20260924-101238.json")
    assert synced == [connection]


def test_secondary_publisher_failure_does_not_block_publication(connection, tmp_path):
    def broken_sync(conn):
        raise RuntimeError("Notion 401 : unauthorized")

    outputs, errors = run_publishers(publication(connection, tmp_path), default_publishers(broken_sync))

    assert "report" in outputs and "json" in outputs
    assert errors == ["publication notion : Notion 401 : unauthorized"]



def test_html_report_is_standalone_and_escapes_source_content():
    evil = DIGEST | {"items": [DIGEST["items"][0] | {"title": "<script>alert(1)</script> v0.30.0",
                                                      "summary": "FP8 <b>sur</b> Ampere."}]}
    html = render(fmt="html", digest=evil)

    assert html.startswith("<!doctype html>") and "<title>Veille LLM / GenAI — 24/09/2026</title>" in html
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
    assert "FP8 &lt;b&gt;sur&lt;/b&gt; Ampere." in html
    assert 'href="https://github.com/vllm-project/vllm/releases/tag/v0.30.0"' in html
    assert "prefers-color-scheme: dark" in html


def test_publisher_writes_markdown_and_html(connection, tmp_path):
    outputs, _ = run_publishers(publication(connection, tmp_path), default_publishers())
    assert outputs["report_html"].endswith(".html") and outputs["report"].endswith(".md")
    assert "<article class=\"signal\"" in open(outputs["report_html"]).read()
