"""Script bash bin/veille : arguments transmis à `python -m app.main` et cycle interactif.

Un faux interpréteur (VEILLE_PYTHON) journalise chaque appel au lieu de lancer la veille.
"""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "bin" / "veille"
RUN_ID = "11111111-2222-3333-4444-555555555555"

STUB = f"""#!/usr/bin/env bash
# faux python : journalise « app.main <args> » ; simule run et pending
shift 2   # -m app.main
printf '%s\\n' "$*" >> "$VEILLE_LOG"
if [[ "$1" == run ]]; then
  for ((i = 1; i <= $#; i++)); do
    if [[ "${{!i}}" == --run-id-file ]]; then next=$((i + 1)); echo -n "{RUN_ID}" > "${{!next}}"; fi
  done
  echo "Digest prêt pour validation humaine."
elif [[ "$1" == pending && -n "${{STUB_PENDING:-}}" ]]; then
  printf '%s\\t2026-09-24\\n' "{RUN_ID}"
fi
"""


@pytest.fixture
def veille(tmp_path):
    stub = tmp_path / "python"
    stub.write_text(STUB)
    stub.chmod(0o755)
    log = tmp_path / "calls.log"

    def call(*args, stdin="", pending=False, tty=False):
        env = os.environ | {"VEILLE_PYTHON": str(stub), "VEILLE_LOG": str(log)}
        if pending:
            env["STUB_PENDING"] = "1"
        if tty:
            env["VEILLE_ASSUME_TTY"] = "1"
        result = subprocess.run([str(SCRIPT), *args], input=stdin, capture_output=True, text=True, env=env)
        calls = log.read_text().splitlines() if log.exists() else []
        return result, calls

    return call


def test_script_is_executable_and_documents_commands(veille):
    assert os.access(SCRIPT, os.X_OK)
    result, calls = veille("help")
    assert result.returncode == 0 and calls == []
    for command in ("cycle", "run", "resume", "approve", "reject", "pending", "grill", "web"):
        assert f"  {command}" in result.stdout


def test_run_and_resume_forward_arguments(veille):
    _, calls = veille("run", "-k", "MCP", "--max-age", "7")
    assert calls == ["run -k MCP --max-age 7"]

    _, calls = veille("resume", RUN_ID, "--approved", "--note", "ok")
    assert calls[-1] == f"resume {RUN_ID} --approved --note ok"


def test_approve_and_reject_shortcuts(veille):
    _, calls = veille("approve", RUN_ID, "Garder le focus GPU")
    assert calls == [f"resume {RUN_ID} --approved --note Garder le focus GPU"]
    _, calls = veille("reject", RUN_ID, "Trop de tutoriels")
    assert calls[-1] == f"resume {RUN_ID} --rejected --note Trop de tutoriels"


@pytest.mark.parametrize("args", [("reject", RUN_ID), ("resume", RUN_ID), ("show",), ("inconnue",)])
def test_invalid_usage_fails_without_calling_the_app(veille, args):
    result, calls = veille(*args)
    assert result.returncode == 2 and calls == []


def test_cycle_publishes_with_a_note(veille):
    result, calls = veille("cycle", "-k", "agents", stdin="o\nPrivilégier les releases\n", pending=True, tty=True)

    assert result.returncode == 0, result.stderr
    assert calls[0].startswith("run --run-id-file ") and calls[0].endswith(" -k agents")
    assert calls[1] == "pending"
    assert calls[2] == f"resume {RUN_ID} --approved --note Privilégier les releases"


def test_cycle_rejects_with_a_reason(veille):
    _, calls = veille("cycle", stdin="r\nTrop de marketing\n", pending=True, tty=True)
    assert calls[-1] == f"resume {RUN_ID} --rejected --note Trop de marketing"


def test_cycle_can_defer_the_decision(veille):
    result, calls = veille("cycle", stdin="p\n", pending=True, tty=True)
    assert not any(call.startswith("resume") for call in calls)
    assert f"veille approve {RUN_ID}" in result.stdout


def test_cycle_without_terminal_prints_instructions(veille):
    result, calls = veille("cycle", pending=True)
    assert not any(call.startswith("resume") for call in calls)
    assert f"veille reject {RUN_ID}" in result.stdout


def test_cycle_with_nothing_pending_stops_cleanly(veille):
    result, calls = veille("cycle", "--approve")
    assert result.returncode == 0 and "sans validation en attente" in result.stdout
    assert calls[-1] == "pending"


# --- Serveur web en arrière-plan : start / status / restart / stop / logs --------------------

# Faux serveur : répond 200 à /api/health ; « app.main web » reste visible dans `ps`.
SERVER = """
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
port = int(sys.argv[sys.argv.index("--port") + 1])
class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"ollama": True, "model": "fake", "model_available": True, "notion": False,
                           "memory": {"documents": 3}}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args):
        print("requête", self.path, flush=True)
HTTPServer(("127.0.0.1", port), Health).serve_forever()
"""
WEB_STUB = f"""#!/usr/bin/env bash
shift 2   # -m app.main
if [[ "$1" == web ]]; then exec python3 -c '{SERVER}' app.main "$@"; fi
if [[ "$1" == crash ]]; then echo "ImportError: boom"; exit 1; fi
"""


def free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def server(tmp_path):
    stub = tmp_path / "python"
    stub.write_text(WEB_STUB)
    stub.chmod(0o755)
    env = os.environ | {"VEILLE_PYTHON": str(stub), "VEILLE_RUN_DIR": str(tmp_path / "run"),
                        "VEILLE_START_TIMEOUT": "10"}

    def call(*args):
        return subprocess.run([str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=60)

    yield call, tmp_path / "run"
    call("stop")


def test_start_status_restart_stop_and_logs(server):
    call, run_dir = server
    port = str(free_port())

    assert call("status").returncode == 3
    started = call("start", "--port", port)
    assert started.returncode == 0, started.stderr
    assert f"http://127.0.0.1:{port}" in started.stdout
    first_pid = (run_dir / "web.pid").read_text().strip()

    again = call("start", "--port", port)
    assert "déjà démarré" in again.stdout

    status = call("status")
    assert status.returncode == 0 and f"PID {first_pid}" in status.stdout
    assert "LLM : fake disponible" in status.stdout

    restarted = call("restart")  # même port, sans le repréciser
    assert restarted.returncode == 0 and f":{port}" in restarted.stdout
    assert (run_dir / "web.pid").read_text().strip() != first_pid

    assert "requête /api/health" in call("logs").stdout
    stopped = call("stop")
    assert "Serveur arrêté" in stopped.stdout and not (run_dir / "web.pid").exists()
    assert "Aucun serveur" in call("stop").stdout


def test_start_refuses_a_busy_port_and_stale_pid_files(server):
    call, run_dir = server
    port = str(free_port())
    assert call("start", "--port", port).returncode == 0
    (run_dir / "web.pid").write_text("999999")  # PID périmé : le serveur actif n'est plus suivi
    busy = call("start", "--port", port)
    assert busy.returncode == 2 and "répond déjà" in busy.stderr
    assert call("start", "--port", "abc").returncode == 2
    assert call("start", "--bogus").returncode == 2
