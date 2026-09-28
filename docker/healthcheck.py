"""Sonde Docker du serveur web : GET /api/health sur l'hôte et le port de Settings (code 0 = sain)."""

import sys
import urllib.request

from app.config import settings

host = "127.0.0.1" if settings.web_host in ("0.0.0.0", "::", "") else settings.web_host
try:
    with urllib.request.urlopen(f"http://{host}:{settings.web_port}/api/health", timeout=5) as response:
        sys.exit(0 if response.status == 200 else 1)
except OSError:
    sys.exit(1)
