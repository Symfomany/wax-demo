#!/usr/bin/env bash
# Valide (et rend en option) des diagrammes Mermaid avec le CLI officiel @mermaid-js/mermaid-cli.
#
#   check.sh fichier.mmd docs/langgraph.md …   # .mmd : un diagramme ; .md : chaque bloc ```mermaid
#   check.sh --out docs/diagrams fichier.mmd    # garde les SVG rendus dans ce dossier
#
# Code de sortie ≠ 0 dès qu'un diagramme ne se rend pas (erreur de syntaxe affichée).
# Version épinglée : MERMAID_CLI_VERSION (défaut 12.0.0). Poste de dev uniquement : mmdc pilote
# Chromium via Puppeteer ; sur arm64, pointer PUPPETEER_EXECUTABLE_PATH vers un Chromium système.
set -euo pipefail

version="${MERMAID_CLI_VERSION:-12.0.0}"
out=""
if [[ "${1:-}" == "--out" ]]; then
    out="${2:?dossier manquant après --out}"
    shift 2
fi
if [[ $# -eq 0 ]]; then
    echo "usage : check.sh [--out DOSSIER] fichier.mmd|fichier.md …" >&2
    exit 2
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
failed=0
for file in "$@"; do
    case "$file" in
        *.mmd) target="$work/$(basename "${file%.mmd}").svg" ;;
        *.md)
            if ! grep -q '^```mermaid' "$file"; then
                echo "–  $file : aucun bloc mermaid"
                continue
            fi
            target="$work/$(basename "$file")" ;;
        *) echo "✗  $file : extension attendue .mmd ou .md" >&2; failed=1; continue ;;
    esac
    if npx -y "@mermaid-js/mermaid-cli@$version" -q -i "$file" -o "$target" >"$work/log" 2>&1; then
        echo "✓  $file"
    else
        echo "✗  $file" >&2
        grep -v -e '^npm warn' -e '^    at ' "$work/log" | head -8 >&2
        failed=1
    fi
done

if [[ -n "$out" && $failed -eq 0 ]]; then
    mkdir -p "$out"
    find "$work" -name '*.svg' -exec cp {} "$out"/ \;
    echo "SVG copiés dans $out"
fi
exit $failed
