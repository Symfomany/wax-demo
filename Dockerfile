# LLM Watch Harness : image multi-architecture (arm64 pour la Jetson Orin, amd64 pour le PC).
# Seules les dépendances vivent dans l'image (/opt/venv) ; le dépôt est monté sur /app par compose.yaml,
# si bien qu'un `git pull` suivi de `docker compose restart` suffit tant que pyproject.toml ne change pas.
# L'application n'a pas besoin du GPU : Ollama reste natif sur l'hôte (JetPack).
FROM python:3.12-slim-bookworm

# Aperçus des actus (MCP Playwright : Node.js + Chromium, ~plusieurs centaines de Mo) : désactivé par défaut
ARG WITH_BROWSER=false
# Propriétaire des fichiers écrits dans le dépôt monté (data/, reports/, sources.toml…) : l'utilisateur de l'hôte
ARG UID=1000
ARG GID=1000

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    VEILLE_PYTHON=/opt/venv/bin/python \
    PYTHONPATH=/app \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright \
    COLUMNS=160

# tzdata : fuseau du cron (CRON_TIMEZONE) ; curl : `bin/veille status`
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata curl \
    && if [ "$WITH_BROWSER" = "true" ]; then apt-get install -y --no-install-recommends nodejs npm; fi \
    && rm -rf /var/lib/apt/lists/*

# Dépendances seules d'abord (couche en cache tant que pyproject.toml ne change pas) ; toutes publiées
# en wheels aarch64 ou pur Python, aucun compilateur requis.
COPY pyproject.toml /tmp/pyproject.toml
RUN python -m venv /opt/venv \
    && python -c "import tomllib; p = tomllib.load(open('/tmp/pyproject.toml', 'rb'))['project']; \
print('\n'.join(p['dependencies'] + p['optional-dependencies']['dev']))" > /tmp/requirements.txt \
    && pip install -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt /tmp/pyproject.toml

# Navigateur de la version de Playwright embarquée par @playwright/mcp@0.0.82 (voir app/screenshots.py)
RUN if [ "$WITH_BROWSER" = "true" ]; then \
        npx -y playwright@1.64.0-alpha-1789764292000 install --with-deps chromium \
        && chmod -R a+rX /opt/ms-playwright; \
    fi

RUN groupadd --gid "$GID" veille && useradd --uid "$UID" --gid "$GID" --create-home veille

# Copie du code : l'image reste utilisable sans montage (docker run), le montage de compose la recouvre.
WORKDIR /app
COPY --chown=veille:veille . /app
USER veille

EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 CMD ["python", "docker/healthcheck.py"]
CMD ["python", "-m", "app.main", "web"]
