# Projet : LLM Watch Harness

## But
Produire une veille LLM/GenAI factuelle, sourcée et exploitable.

## Règles non négociables
- Ne jamais inventer une date, un benchmark, une version, une URL ou une annonce.
- Toute affirmation publiée doit être rattachée à une URL source.
- Préférer les sources primaires : release notes, articles arXiv, dépôts GitHub,
  blogs techniques officiels et documentation éditeur.
- Ne jamais stocker tokens, clés API ou secrets dans Git.
- Ne jamais modifier `.env` sans demande explicite.
- Toute modification du schéma SQLite doit avoir une migration et un test.
- Toute sortie LLM doit être validée par Pydantic avant persistance.
- Toute commande destructive requiert une confirmation explicite.
- Les outils MCP GitHub sont en lecture seule ; l'écriture Notion se limite à la page de veille,
  après confirmation.

## Commandes
- Tests (unitaires + E2E) : `.venv/bin/python -m pytest -q`
- E2E réel (Ollama + réseau) : `RUN_LIVE=1 .venv/bin/python -m pytest -q tests/e2e/test_live.py -s`
- Diagnostic (Ollama, modèle, GPU, sources, MCP, migrations) : `python -m app.main doctor`
- Collecte + workflow : `python -m app.main run` (`--no-collect`, `--no-mcp`, `--approve`)
- Validation humaine : `python -m app.main resume <run_id> --approved|--rejected --note "..."`
- Plan d'exécution : `python -m app.main plan --langgraph`
- Recherche GitHub via MCP : `python -m app.main github "llm inference" --limit 5`
- Mémoire : `python -m app.main memory [--export]`
- Interface web de chat : `python -m app.main web` (http://127.0.0.1:8000)
- Rapport daté : `python -m app.main report` · Notion : `python -m app.main notion-sync`
- Instructions templatées : `python -m app.main instructions claude-md|demande|chat`
- Inspection SQLite : `sqlite3 data/watch.db`
- Vérification style : `python -m compileall app`

## Architecture
- Sources autorisées : `sources.toml` (skill `ajout-source` pour toute modification).
- Supervisor + Task Graph : `app/workflow/graph.py`, `app/workflow/tasks.py`.
  Sous-agents (sous-graphes) : `app/workflow/subagents.py` ; prompts : `app/prompts/*.md`.
  Le LLM ne renvoie que des identifiants ; URL, titre et date sont recopiés par
  le code depuis les documents collectés.
- Guards : `app/harness/guards.py` (MCP, injections, contrats d'état) et
  `app/harness/hooks.py` (garde-fous de publication).
- MCP GitHub : serveur `app/mcp_servers/github_server.py`, client
  `app/collectors/github_mcp.py`, déclaré pour Claude Code dans `.mcp.json`.
- Mémoire : SQLite (`app/storage.py`, migrations `MIGRATIONS`), Store LangGraph
  (`app/memory.py`), export Claude Code importé ci-dessous.
- Publication : `app/publishers.py` (fichiers → rapport `templates/report.md.j2` → Notion).
- MCP Notion : serveur `app/mcp_servers/notion_server.py`, client `app/notion.py`.
- Chat : agent `app/chat/` (route → act → respond → guard), serveur `app/web/`.
- Instructions templatées : `templates/claude/` (profil TOML + templates Jinja2).
- Traçage : `app/observability.py` (Langfuse, LangSmith ; session = run ou conversation).
- Hooks Claude Code : `.claude/hooks/`, déclarés dans `.claude/settings.json`.
- Documentation : `docs/getting-started.html`, `docs/index.html`.

## Mémoire de veille
@.claude/memory/veille.md

## Définition de terminé
- Sources visibles et URLs valides.
- JSON conforme au schéma Pydantic.
- Digest Markdown et JSON produits.
- Aucun secret dans les fichiers suivis par Git.
- Tests pertinents verts.
