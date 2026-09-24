#!/usr/bin/env bash
# Hook Stop : « Définition de terminé » — Claude ne s'arrête pas sur des tests rouges.
input=$(cat)
# Évite une boucle infinie si Claude est déjà relancé par ce hook.
if echo "$input" | grep -q '"stop_hook_active": *true'; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR" || exit 0
if ! output=$(.venv/bin/python -m pytest -q -x 2>&1); then
  echo "Tests rouges, la tâche n'est pas terminée :" >&2
  echo "$output" | tail -25 >&2
  exit 2
fi
