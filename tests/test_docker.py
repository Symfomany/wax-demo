"""Tests de la conteneurisation (Dockerfile, compose.yaml, service de démarrage), sans Docker."""

import os
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def compose() -> dict:
    return yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))


def test_compose_services_restart_on_host_network():
    services = compose()["services"]
    assert set(services) == {"web", "cron"}
    for name, service in services.items():
        # Réseau de l'hôte : Ollama et MQTT sur 127.0.0.1, interface jamais publiée sur 0.0.0.0
        assert service["network_mode"] == "host", name
        assert "ports" not in service, name
        assert service["restart"] == "unless-stopped", name
        assert "./:/app" in service["volumes"], name
        # Secrets lus depuis .env monté, jamais recopiés dans la config compose
        assert "env_file" not in service, name
        assert set(service.get("environment", {})) <= {"TZ"}, name
    assert services["cron"]["command"][-2:] == ["cron", "start"]
    assert services["web"]["command"][-1] == "web"


def test_image_excludes_secrets_and_local_data():
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()
    for entry in (".env", ".venv/", "data/", ".git/", "tui/node_modules/"):
        assert entry in ignored
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert dockerfile.startswith("#") and "FROM python:3.12" in dockerfile
    assert "\nUSER veille" in dockerfile
    assert "VEILLE_PYTHON=/opt/venv/bin/python" in dockerfile  # bin/veille (tâches du cron) dans le conteneur


def test_install_service_script_is_valid_bash():
    script = ROOT / "docker" / "install-service.sh"
    assert os.access(script, os.X_OK)
    assert subprocess.run(["bash", "-n", str(script)], capture_output=True).returncode == 0
    text = script.read_text(encoding="utf-8")
    assert "WantedBy=multi-user.target" in text and "After=docker.service" in text


def test_healthcheck_fails_without_server():
    env = {**os.environ, "WEB_HOST": "127.0.0.1", "WEB_PORT": "9"}
    result = subprocess.run([sys.executable, "docker/healthcheck.py"], cwd=ROOT, env={**env, "PYTHONPATH": str(ROOT)},
                            capture_output=True, timeout=30)
    assert result.returncode == 1


# --- Image obsolète : dépendance ajoutée à pyproject.toml sans reconstruction ---------------------


def test_stale_image_is_detected_before_starting(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from app import image_check, main
    from app.image_check import declared, missing_dependencies, stale_image_message

    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    baked = tmp_path / "requirements.baked"
    assert missing_dependencies(pyproject, baked) is None  # hors Docker : aucun contrôle
    baked.write_text("\n".join(sorted(declared(pyproject))) + "\n")
    assert missing_dependencies(pyproject, baked) == [] and stale_image_message(pyproject, baked) is None

    baked.write_text("\n".join(d for d in sorted(declared(pyproject)) if not d.startswith("prometheus")))
    message = stale_image_message(pyproject, baked)
    assert "prometheus-client" in message and "docker compose up -d --build" in message

    monkeypatch.setattr(image_check, "BAKED", baked)
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("serveur lancé")))
    result = CliRunner().invoke(main.cli, ["web"])
    assert result.exit_code == 3 and "Image Docker obsolète" in result.output


def test_dockerfile_keeps_the_installed_dependency_list():
    dockerfile = (Path(__file__).resolve().parent.parent / "Dockerfile").read_text()
    assert "mv /tmp/requirements.txt /opt/venv/requirements.baked" in dockerfile
