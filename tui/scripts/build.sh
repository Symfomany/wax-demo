#!/usr/bin/env bash
# Compile la TUI en un exécutable autonome (bun build --compile) : ni Bun ni node_modules requis à l'exécution.
#
#   tui/scripts/build.sh                    # plateforme courante → tui/dist/veille-tui
#   tui/scripts/build.sh bun-linux-arm64    # Jetson Orin (le paquet natif @opentui/core-linux-arm64 est installé au besoin)
#
# L'exécutable trouve le projet deux niveaux au-dessus de lui (tui/dist/ → racine), ou via VEILLE_ROOT.
set -euo pipefail

TUI="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUN="$TUI/node_modules/.bin/bun"
TARGET="${1:-}"
[[ -x "$BUN" ]] || { echo "build : Bun introuvable, lancer d'abord « npm install » dans tui/" >&2; exit 2; }

# Plateforme visée (os-arch) : celle de --target, sinon la machine courante.
case "${TARGET:-$(uname -s)-$(uname -m)}" in
  bun-linux-x64*|Linux-x86_64)   platform="linux-x64" ;;
  bun-linux-arm64*|Linux-aarch64) platform="linux-arm64" ;;
  bun-darwin-arm64*|Darwin-arm64) platform="darwin-arm64" ;;
  bun-darwin-x64*|Darwin-x86_64)  platform="darwin-x64" ;;
  *) echo "build : plateforme non prise en charge : ${TARGET:-$(uname -sm)}" >&2; exit 2 ;;
esac

# Bibliothèque native d'OpenTUI de la plateforme visée (même version que @opentui/core).
version="$(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "$TUI/node_modules/@opentui/core/package.json" | head -1)"
if [[ ! -d "$TUI/node_modules/@opentui/core-$platform" ]]; then
  echo "Installation de @opentui/core-$platform@$version (bibliothèque native de la cible)…"
  (cd "$TUI" && npm install --no-save --no-audit --no-fund --force "@opentui/core-$platform@$version")
fi

# Les bibliothèques des autres plateformes ne sont pas embarquées.
externals=()
for other in darwin-x64 darwin-arm64 linux-x64 linux-x64-musl linux-arm64 linux-arm64-musl win32-x64 win32-arm64; do
  [[ "$other" == "$platform" ]] || externals+=(--external "@opentui/core-$other")
done

mkdir -p "$TUI/dist"
args=(build --compile --minify "${externals[@]}" "$TUI/src/index.tsx" --outfile "$TUI/dist/veille-tui")
[[ -n "$TARGET" ]] && args+=(--target "$TARGET")
(cd "$TUI" && "$BUN" "${args[@]}")
echo "✅ Exécutable : $TUI/dist/veille-tui ($platform) — lancer : tui/dist/veille-tui [écran]"
