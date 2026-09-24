"""E2E « live » : vrai Ollama, vraies sources, vrai GitHub. Désactivé par défaut.

    RUN_LIVE=1 .venv/bin/python -m pytest -q tests/e2e/test_live.py -s
"""

import os
import re

import httpx
import pytest
from typer.testing import CliRunner

from app.config import settings
from app.main import cli
from app.schemas import Digest

pytestmark = pytest.mark.skipif(os.environ.get("RUN_LIVE") != "1", reason="RUN_LIVE=1 requis")


def test_live_run_publishes_grounded_digest(tmp_path, monkeypatch):
    for name, value in {
        "database_path": tmp_path / "watch.db",
        "checkpoint_db_path": tmp_path / "checkpoints.db",
        "memory_db_path": tmp_path / "memory.db",
        "output_dir": tmp_path / "output",
        "claude_memory_path": tmp_path / "veille.md",
    }.items():
        monkeypatch.setattr(settings, name, value)

    result = CliRunner().invoke(cli, ["run", "--approve"], catch_exceptions=False, terminal_width=200)
    print(result.output)

    assert result.exit_code == 0
    json_path = re.search(r"JSON : (\S+\.json)", result.output).group(1)
    digest = Digest.model_validate_json(open(json_path).read())
    assert digest.items
    for item in digest.items:
        response = httpx.head(str(item.url), follow_redirects=True, timeout=20,
                              headers={"User-Agent": "Mozilla/5.0"})
        assert response.status_code < 400, item.url
