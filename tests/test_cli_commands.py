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
