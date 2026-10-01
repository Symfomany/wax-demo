#!/usr/bin/env bash
# Monitoring de Scouty Veille AI : Prometheus + Grafana en conteneurs, en permanence (restart: unless-stopped).
#
#   monitoring/setup.sh                  # crée le compte Grafana au premier lancement, puis démarre la pile
#   monitoring/setup.sh --status         # conteneurs, cibles Prometheus, santé de Grafana
#   monitoring/setup.sh --reset-password # nouveau mot de passe pour le compte Grafana
#   monitoring/setup.sh --down           # arrête la pile (données conservées dans les volumes Docker)
#   monitoring/setup.sh --apply-account  # applique un identifiant / mot de passe modifié dans grafana.env
#   monitoring/setup.sh --tailscale      # Grafana en HTTPS sur le réseau Tailscale (https://<machine>.ts.net:3000),
#                                        # réservé au tailnet (jamais Funnel) ; Grafana reste sur 127.0.0.1
#
# Compte Grafana : monitoring/grafana.env (droits 600, hors Git) — GRAFANA_ADMIN_USER (défaut « scouty »),
# GRAFANA_ADMIN_PASSWORD (aléatoire, 24 caractères) ; GRAFANA_HOST / GRAFANA_PORT (défaut 127.0.0.1:3000).
# Inscriptions et accès anonyme désactivés. Accès distant : --tailscale, ou ssh -L 3000:127.0.0.1:3000 jetson.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$DIR/grafana.env"

die() { echo "monitoring : $*" >&2; exit 2; }

new_password() {
  python3 -c 'import secrets, string
alphabet = string.ascii_letters + string.digits + "-_.%+="
while True:
    p = "".join(secrets.choice(alphabet) for _ in range(24))
    if any(c.islower() for c in p) and any(c.isupper() for c in p) and any(c.isdigit() for c in p) \
            and any(c in "-_.%+=" for c in p):
        print(p); break'
}

setting() { grep -E "^$1=" "$ENV_FILE" | tail -n 1 | cut -d= -f2-; }

create_account() {
  local user="${GRAFANA_ADMIN_USER:-scouty}"
  [[ "$user" =~ ^[A-Za-z0-9._-]{3,40}$ ]] || die "identifiant Grafana invalide : $user"
  (umask 077 && cat > "$ENV_FILE" <<EOF
# Compte Grafana du monitoring (hors Git). Changer le mot de passe : monitoring/setup.sh --reset-password
GRAFANA_ADMIN_USER=$user
GRAFANA_ADMIN_PASSWORD=$(new_password)
GRAFANA_HOST=${GRAFANA_HOST:-127.0.0.1}
GRAFANA_PORT=${GRAFANA_PORT:-3000}
EOF
  )
  echo "Compte Grafana créé : $user (mot de passe dans $ENV_FILE)"
}

docker_cli() {
  command -v docker >/dev/null 2>&1 || die "docker introuvable (Jetson : sudo apt install docker.io docker-compose-v2)"
  if docker info >/dev/null 2>&1; then DOCKER=(docker); else DOCKER=(sudo docker); fi
}

compose() { "${DOCKER[@]}" compose -f "$DIR/compose.yaml" --env-file "$ENV_FILE" "$@"; }

grafana_url() { echo "http://$(setting GRAFANA_HOST):$(setting GRAFANA_PORT)"; }

set_setting() {  # remplace ou ajoute une ligne CLÉ=valeur du fichier du compte
  if grep -qE "^$1=" "$ENV_FILE"; then sed -i "s|^$1=.*|$1=$2|" "$ENV_FILE"; else echo "$1=$2" >> "$ENV_FILE"; fi
}

# Grafana servi en HTTPS par Tailscale (certificat du tailnet), sur le même port, pour le tailnet seulement.
tailscale_serve() {
  command -v tailscale >/dev/null 2>&1 || die "tailscale introuvable"
  local host port url
  host="$(tailscale status --json | python3 -c 'import json, sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
  [[ -n "$host" ]] || die "nom Tailscale introuvable (tailscale status)"
  port="$(setting GRAFANA_PORT)"
  url="https://$host:$port/"
  if ! tailscale serve --bg --https="$port" "http://127.0.0.1:$port" >/dev/null 2>&1; then
    echo "Configuration de Tailscale (sudo ; « sudo tailscale set --operator=$USER » l'évite ensuite)…"
    sudo tailscale serve --bg --https="$port" "http://127.0.0.1:$port" >/dev/null
  fi
  set_setting GRAFANA_ROOT_URL "$url"
  compose up -d grafana   # recrée Grafana avec sa nouvelle URL
  wait_grafana
  check_login
  echo
  echo "Grafana : $url  (réseau Tailscale uniquement)"
  echo "Compte  : $(setting GRAFANA_ADMIN_USER) / $(setting GRAFANA_ADMIN_PASSWORD)"
}

wait_grafana() {
  local url; url="$(grafana_url)"
  for ((i = 0; i < 60; i++)); do
    curl -fsS --max-time 2 "$url/api/health" >/dev/null 2>&1 && return 0
    sleep 2
  done
  die "Grafana ne répond pas sur $url : monitoring/setup.sh --status, puis docker logs scouty-grafana"
}

check_login() {
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 -u "$(setting GRAFANA_ADMIN_USER):$(setting GRAFANA_ADMIN_PASSWORD)" \
    "$(grafana_url)/api/dashboards/uid/scouty-jetson")"
  [[ "$code" == 200 ]] || die "connexion Grafana refusée (HTTP $code) : monitoring/setup.sh --reset-password"
}

api_code() {  # code HTTP d'un appel à l'API Grafana (identifiant:mot de passe en 1er argument)
  curl -s -o /dev/null -w '%{http_code}' --max-time 5 -u "$1" "${@:3}" "$(grafana_url)$2"
}

# Grafana ne lit GF_SECURITY_ADMIN_USER / _PASSWORD qu'à la création de sa base : un identifiant ou un mot de
# passe modifié ensuite dans grafana.env doit être appliqué au compte existant (id 1, e-mail admin@localhost).
apply_account() {
  local user password
  user="$(setting GRAFANA_ADMIN_USER)"; password="$(setting GRAFANA_ADMIN_PASSWORD)"
  [[ "$user" =~ ^[A-Za-z0-9._-]{3,40}$ ]] || die "identifiant Grafana invalide : $user"
  if [[ "$(api_code "$user:$password" /api/user)" == 200 ]]; then
    echo "Compte déjà à jour : $user"; return 0
  fi
  # Mot de passe changé lui aussi : réinitialisé par la CLI de Grafana (conteneur, donc Docker).
  if [[ "$(api_code "admin@localhost:$password" /api/user)" != 200 ]]; then
    docker_cli
    compose exec -T grafana grafana cli admin reset-admin-password "$password" >/dev/null
  fi
  [[ "$(api_code "admin@localhost:$password" /api/users/1 -X PUT -H 'Content-Type: application/json' \
        -d "{\"login\":\"$user\",\"name\":\"$user\",\"email\":\"admin@localhost\"}")" == 200 ]] \
    || die "renommage du compte refusé : vérifier GRAFANA_ADMIN_PASSWORD, puis --reset-password"
  [[ "$(api_code "$user:$password" /api/user)" == 200 ]] || die "connexion impossible avec $user après renommage"
  echo "Compte Grafana appliqué : $user (mot de passe inchangé dans le fichier du compte)"
}

status() {
  compose ps
  echo
  curl -fsS --max-time 3 http://127.0.0.1:9090/api/v1/targets 2>/dev/null | python3 -c '
import json, sys
for t in json.load(sys.stdin)["data"]["activeTargets"]:
    print("  %-11s %-7s %s  %s" % (t["labels"]["job"], t["health"], t["scrapeUrl"], t.get("lastError") or ""))' \
    || echo "  Prometheus injoignable (127.0.0.1:9090)"
  curl -fsS --max-time 3 "$(grafana_url)/api/health" >/dev/null 2>&1 \
    && echo "  Grafana : $(grafana_url)" || echo "  Grafana injoignable : $(grafana_url)"
}

main() {
  [[ -f "$DIR/grafana/dashboards/scouty.json" ]] || die "dashboard absent : python3 monitoring/build_dashboard.py"
  [[ -f "$ENV_FILE" ]] || create_account
  docker_cli
  case "${1:-up}" in
    up)
      compose up -d --remove-orphans
      wait_grafana
      check_login
      echo
      echo "Grafana : $(grafana_url)  (dashboard « Scouty — Jetson Orin & LLM »)"
      echo "Compte  : $(setting GRAFANA_ADMIN_USER) / $(setting GRAFANA_ADMIN_PASSWORD)"
      echo "Prometheus (local) : http://127.0.0.1:9090 · état : monitoring/setup.sh --status" ;;
    --status) status ;;
    --down) compose down ;;
    --tailscale) tailscale_serve ;;
    --apply-account) apply_account ;;
    --reset-password)
      local password; password="$(new_password)"
      compose exec -T grafana grafana cli admin reset-admin-password "$password" >/dev/null
      sed -i "s|^GRAFANA_ADMIN_PASSWORD=.*|GRAFANA_ADMIN_PASSWORD=$password|" "$ENV_FILE"
      check_login
      echo "Nouveau mot de passe de $(setting GRAFANA_ADMIN_USER) : $password (enregistré dans $ENV_FILE)" ;;
    *) die "usage : monitoring/setup.sh [--status | --reset-password | --apply-account | --tailscale | --down]" ;;
  esac
}

main "$@"
