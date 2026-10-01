#!/usr/bin/env bash
# Démarrage automatique de la veille conteneurisée au boot (Jetson ou tout Linux avec systemd).
#
#   docker/install-service.sh              # construit l'image, installe et active les unités systemd
#   docker/install-service.sh --no-timer   # sans la veille des jours ouvrés (web + cron seulement)
#
# Unités système écrites (sudo) :
#   veille-docker.service  docker compose up -d au boot, après docker et ollama ; stop à l'arrêt
#   veille-run.service     une veille (python -m app.main run) dans le conteneur cron
#   veille-run.timer       lun.–ven. 08:00, rattrapée si la machine était éteinte (Persistent)
# Désinstaller : sudo systemctl disable --now veille-run.timer veille-docker.service
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR=/etc/systemd/system
WITH_TIMER=true
[[ "${1:-}" == "--no-timer" ]] && WITH_TIMER=false

die() { echo "install-service : $*" >&2; exit 2; }

DOCKER="$(command -v docker)" || die "docker introuvable (Jetson : sudo apt install docker.io docker-compose-v2)"
"$DOCKER" compose version >/dev/null 2>&1 || die "plugin « docker compose » introuvable"
[[ -f "$ROOT/.env" ]] || die "$ROOT/.env absent : cp .env.example .env puis le compléter"

# Propriétaire des fichiers écrits par les conteneurs dans le dépôt monté
export VEILLE_UID="${SUDO_UID:-$(id -u)}" VEILLE_GID="${SUDO_GID:-$(id -g)}"
mkdir -p "$ROOT/data" "$ROOT/output" "$ROOT/reports" "$ROOT/.claude/memory"

# Les conteneurs remplacent l'installation native : pas de double cron ni de conflit sur le port web
if systemctl --user is-enabled veille-cron.service >/dev/null 2>&1; then
  echo "Désactivation du cron natif (systemd utilisateur) : le conteneur cron le remplace"
  systemctl --user disable --now veille-cron.service
fi
if [[ -f "$ROOT/data/web.pid" ]] && kill -0 "$(cat "$ROOT/data/web.pid")" 2>/dev/null; then
  echo "Arrêt du serveur web natif : le conteneur web reprend le port"
  "$ROOT/bin/veille" stop
fi

echo "Construction de l'image (UID $VEILLE_UID)…"
(cd "$ROOT" && sudo --preserve-env=VEILLE_UID,VEILLE_GID,VEILLE_WITH_BROWSER "$DOCKER" compose build)

sudo tee "$UNIT_DIR/veille-docker.service" >/dev/null <<EOF
[Unit]
Description=Veille LLM/GenAI : conteneurs web + cron (docker compose)
Requires=docker.service
After=docker.service network-online.target ollama.service
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$ROOT
Environment=VEILLE_UID=$VEILLE_UID VEILLE_GID=$VEILLE_GID
ExecStart=$DOCKER compose up -d --remove-orphans
ExecStop=$DOCKER compose stop
TimeoutStartSec=600

[Install]
WantedBy=multi-user.target
EOF

sudo tee "$UNIT_DIR/veille-run.service" >/dev/null <<EOF
[Unit]
Description=Veille LLM/GenAI (collecte + scout/critic/editor) dans le conteneur cron
Requires=veille-docker.service
After=veille-docker.service

[Service]
Type=oneshot
WorkingDirectory=$ROOT
ExecStart=$DOCKER compose exec -T cron python -m app.main run
TimeoutStartSec=900
EOF

sudo tee "$UNIT_DIR/veille-run.timer" >/dev/null <<EOF
[Unit]
Description=Veille LLM/GenAI des jours ouvrés

[Timer]
OnCalendar=Mon..Fri 08:00
Persistent=true

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable docker.service
systemctl list-unit-files ollama.service >/dev/null 2>&1 && sudo systemctl enable ollama.service
sudo systemctl enable --now veille-docker.service
if [[ "$WITH_TIMER" == true ]]; then
  sudo systemctl enable --now veille-run.timer
fi

echo "✓ Démarrage au boot activé. État : systemctl status veille-docker · docker compose ps"
echo "  Journaux : docker compose logs -f web cron · veille : journalctl -u veille-run"
