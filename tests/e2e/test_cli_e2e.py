"""E2E complet via la CLI : run → validation humaine → publication → mémoire → nouveau run.

Tout est réel (Typer, LangGraph + checkpoints SQLite, Store SQLite, collecteurs
RSS/arXiv, serveur MCP stdio, guards, rendu) sauf le LLM, simulé, et l'API
GitHub, remplacée par un serveur HTTP local.
"""

import json
import re

from typer.testing import CliRunner

from app import storage
from app.config import settings
from app.main import cli
from app.schemas import Digest

runner = CliRunner()


def invoke(*args: str):
    result = runner.invoke(cli, list(args), catch_exceptions=False, terminal_width=200)
    return result


def run_id_from(output: str) -> str:
    return re.search(r"resume ([0-9a-f-]{36}) --approved", output).group(1)


def test_full_lifecycle(isolated_settings, fake_llm_factory, fake_github):
    tmp = isolated_settings

    # 1. Run : supervisor + collecte parallèle (RSS, arXiv, MCP) → arrêt avant publication
    id_file = tmp / "run-id.txt"
    first = invoke("run", "--run-id-file", str(id_file))
    assert first.exit_code == 0, first.output
    assert (
        "fan-out : collect:rss, collect:arxiv, collect:github_releases, collect:github_mcp"
        in first.output
    )
    assert "'github_mcp': 2" in first.output
    assert "délègue editorial → editorial" in first.output
    assert "Digest prêt pour validation humaine" in first.output
    assert not (tmp / "output").exists()
    run_id = run_id_from(first.output)
    assert id_file.read_text() == run_id  # pour les scripts (bin/veille cycle)
    assert run_id in invoke("pending").output
    assert "Veille LLM" in invoke("show", run_id).output

    connection = storage.connect(settings.database_path)
    collected = {row[0] for row in connection.execute("SELECT url FROM documents")}
    assert "https://github.com/acme/fast-infer" in collected  # via MCP
    assert "https://arxiv.org/abs/2609.00002" not in collected  # filtre mots-clés arXiv

    # 2. Validation humaine : publication + leçon mémorisée
    approved = invoke("resume", run_id, "--approved", "--note", "Garder les dépôts d'inférence")
    assert approved.exit_code == 0, approved.output
    json_path = re.search(r"JSON : (\S+\.json)", approved.output).group(1)
    digest = Digest.model_validate_json(open(json_path).read())
    assert digest.items and {str(i.url) for i in digest.items} <= collected
    assert invoke("resume", run_id, "--approved").exit_code == 1  # déjà repris

    # 3. Mémoire : trois niveaux
    memory = invoke("memory", "--export")
    assert "Garder les dépôts d'inférence" in memory.output
    assert "github_mcp" in memory.output  # santé des sources
    exported = settings.claude_memory_path.read_text()
    assert "✅ Garder les dépôts d'inférence" in exported
    assert "github_mcp : 1 OK / 0 échec(s)" in exported

    # 4. Second run (publication directe) : la leçon est injectée, rien n'est republié
    fake_llm_factory.clear()
    second = invoke("run", "--approve", "--no-collect")
    assert second.exit_code == 0, second.output
    assert "fan-out" not in second.output
    scout_prompts = [p for schema, p in fake_llm_factory if schema == "ScoutOutput"]
    assert scout_prompts and "Garder les dépôts d'inférence" in scout_prompts[0]
    published_first = {str(i.url) for i in digest.items}
    for prompt in scout_prompts:
        assert not any(url in prompt for url in published_first)

    statuses = [row[0] for row in connection.execute("SELECT status FROM runs ORDER BY started_at")]
    assert statuses.count("published") >= 1


def test_rejection_is_recorded(isolated_settings, fake_llm_factory):
    first = invoke("run", "--no-mcp")
    assert "collect:github_mcp" not in first.output
    run_id = run_id_from(first.output)

    rejected = invoke("resume", run_id, "--rejected", "--note", "Trop d'arXiv")

    assert "Digest rejeté" in rejected.output
    assert not (isolated_settings / "output").exists()
    assert "❌ Trop d'arXiv" in settings.claude_memory_path.read_text()


def test_plan_command_renders_task_graph_and_langgraph(isolated_settings, fake_llm_factory):
    result = invoke("plan", "--langgraph")

    assert result.exit_code == 0
    assert "collect:rss → collect:arxiv" in result.output or "Ordre topologique" in result.output
    assert "flowchart LR" in result.output
    assert "supervisor" in result.output and "approval" in result.output


def test_github_command_uses_mcp(isolated_settings, fake_github):
    result = invoke("github", "llm inference", "--limit", "2")

    assert result.exit_code == 0, result.output
    assert "MCP github.search_repositories" in result.output
    assert "acme/fast-infer" in result.output


def test_digest_json_matches_schema_on_disk(isolated_settings, fake_llm_factory):
    result = invoke("run", "--approve", "--no-mcp")
    json_path = re.search(r"JSON : (\S+\.json)", result.output).group(1)
    payload = json.loads(open(json_path).read())
    Digest.model_validate(payload)
    markdown = open(json_path.replace(".json", ".md")).read()
    assert all(item["url"] in markdown for item in payload["items"])
