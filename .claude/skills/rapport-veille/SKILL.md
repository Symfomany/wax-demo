---
name: rapport-veille
description: Produire le rapport de veille daté de bout en bout en déclenchant les outils dans l'ordre (doctor → run → audit → resume → report → notion-sync → memory). À utiliser pour « fais la veille du jour », « rapport de veille », « publie la veille dans Notion ».
argument-hint: "[--no-collect | --no-mcp] [note de validation]"
---

# Rapport de veille daté

Arguments reçus : `$ARGUMENTS` (options de `run` et/ou note de validation ; peut être vide).

## Outils à déclencher, dans l'ordre

1. **Diagnostic** : `.venv/bin/python -m app.main doctor`. Si un contrôle est KO, s'arrêter
   et expliquer la correction (ex. `LLM_MODEL` dans `.env`, `ollama serve`).
2. **Veille** : `.venv/bin/python -m app.main run` (+ options `--no-collect` / `--no-mcp`
   présentes dans les arguments). Relever le `run_id` et les lignes `supervisor`.
3. **Audit** : appliquer le skill `audit-digest` (sous-agent `source-auditor`).
4. **Décision** : `resume <run_id> --approved --note "<leçon>"` si tout est traçable,
   sinon `--rejected --note "<motif réutilisable>"`. Si les arguments contiennent une
   note, l'utiliser. En cas de doute, demander à l'utilisateur avant de publier.
5. **Rapport daté** : la publication l'écrit dans `reports/AAAA/veille-AAAA-MM-JJ-HHMM.md`.
   Le relancer au besoin : `.venv/bin/python -m app.main report`.
6. **Notion** : automatique à la publication si `NOTION_TOKEN` et `NOTION_PARENT_PAGE_ID`
   sont configurés ; sinon `.venv/bin/python -m app.main notion-sync` après configuration.
7. **Mémoire** : `.venv/bin/python -m app.main memory --export`.

## Restitution
Terminer par : chemin du rapport, URL Notion (si publiée), nombre de signaux retenus et
écartés, et les 3 signaux « à retenir » avec leur lien — uniquement tels qu'ils figurent
dans le rapport, sans reformuler les faits.
