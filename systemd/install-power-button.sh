#!/usr/bin/env bash
# Bouton d'alimentation câblé sur la Jetson : appui long (POWER_BUTTON_HOLD_SECONDS, 5 s) → extinction.
#
#   systemd/install-power-button.sh              # installe et active le service root veille-power-button
#   systemd/install-power-button.sh --uninstall  # le retire (l'appui court éteint de nouveau, comme avant)
#
# Le service tourne sous « systemd-inhibit --what=handle-power-key » : tant qu'il vit, logind ignore
# l'appui court ; le démon (python -m app.main power-button) chronomètre l'appui et lance « shutdown now ».
# Essai sans éteindre : sudo .venv/bin/python -m app.main power-button --dry-run
# Suivi : journalctl -u veille-power-button -f
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT=/etc/systemd/system/veille-power-button.service

if [[ "${1:-}" == "--uninstall" ]]; then
  sudo systemctl disable --now veille-power-button.service 2>/dev/null || true
  sudo rm -f -- "$UNIT"
  sudo systemctl daemon-reload
  echo "Service retiré : un appui court sur le bouton éteint de nouveau la Jetson (logind)."
  exit 0
fi

[[ -x "$ROOT/.venv/bin/python" ]] || { echo "venv introuvable : $ROOT/.venv" >&2; exit 2; }
DEVICE="$("$ROOT/.venv/bin/python" -m app.main power-button --detect)" || {
  echo "Aucun bouton d'alimentation (KEY_POWER) détecté dans /proc/bus/input/devices" >&2; exit 2; }
echo "Bouton détecté : $DEVICE"

sudo tee "$UNIT" >/dev/null <<UNIT
[Unit]
Description=Veille : bouton d'alimentation de la Jetson (appui long → extinction)
After=systemd-logind.service

[Service]
Type=simple
WorkingDirectory=$ROOT
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/systemd-inhibit --what=handle-power-key --mode=block --who=veille-power-button --why="Appui long pour éteindre" $ROOT/.venv/bin/python -m app.main power-button
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now veille-power-button.service
sleep 2
systemctl --no-pager --lines=5 status veille-power-button.service || true
echo
systemd-inhibit --list --no-pager | grep -i "veille-power-button" \
  && echo "OK : appui court ignoré ; maintenir le bouton 5 s éteint la Jetson (shutdown now)."
