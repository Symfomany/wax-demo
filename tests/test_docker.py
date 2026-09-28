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
